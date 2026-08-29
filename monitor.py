"""
闲鱼 (goofish.com) 价格监控核心模块。

反封号设计原则：
  - 使用真实浏览器引擎（Playwright），行为与真人无异
  - 使用持久化浏览器配置：只需登录一次（扫码），之后自动复用会话
  - 不进行任何写操作（不评论、不购买、不发布），只搜索浏览
  - 每次搜索之间随机延迟，轮询间隔建议 15 分钟以上
  - 默认有头模式（无头模式容易被闲鱼风控拦截）

登录说明：
  闲鱼网页版搜索需要登录。运行 `python run.py --login` 手动扫码登录一次，
  登录状态会保存在浏览器配置目录（user_data_dir）中，之后监控自动复用。
"""

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import subprocess
import sys
import time
from typing import Optional

from playwright.async_api import (
    async_playwright,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

logger = logging.getLogger(__name__)


def _hide_chrome_windows():
    """Windows 下彻底隐藏 Playwright Chromium 窗口（含任务栏按钮）。

    仅匹配 ms-playwright 目录下的 chromium 进程，不会误隐藏用户日常使用的 Chrome。
    窗口隐藏后页面仍正常渲染（有头模式保持），用于后台抓取不影响前台工作。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process chrome -ErrorAction SilentlyContinue | "
             "Where-Object { $_.Path -like '*ms-playwright*' } | "
             "Select-Object -ExpandProperty Id) -join ','"],
            capture_output=True, text=True, timeout=10,
        )
        pids = {int(p) for p in out.stdout.strip().split(",") if p.strip().isdigit()}
        if not pids:
            return

        user32 = ctypes.windll.user32
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]

        def _callback(hwnd, _lparam):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids and user32.IsWindowVisible(hwnd):
                user32.ShowWindow(hwnd, 0)  # SW_HIDE
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(_callback), 0)
        logger.debug("已隐藏后台浏览器窗口")
    except Exception:
        # 窗口隐藏失败不影响监控（窗口仍位于屏幕外）
        logger.debug("隐藏浏览器窗口失败（窗口仍位于屏幕外）")

# 常见 UA 池，随机选择，模拟不同用户
PRO_MODEL_WORDS = {"pro", "max", "plus", "mini", "ultra", "promax", "pro max"}

# ── 万元缩写判定阈值（C5，见 R4/E6 修复）──
WAN_UNIT = 10000                        # 万元换算倍率（"X.YY 万" = X.YY × 10000）
WAN_INT_RANGE = (1, 10)                 # 万元缩写整数部分范围：1 ≤ number < 10
WAN_CANDIDATE_MAX_MULTIPLIER = 3.0      # 万元候选值上限倍数：wan×10000 ≤ max_price×3
WAN_SUSPICIOUS_LITERAL_RATIO = 0.1      # 字面价低于 min_price 的该比例时视为可疑低价

# ── 页面操作超时（毫秒 / 秒）──
PAGE_NAV_TIMEOUT_MS = 30000             # 登录页/首页导航超时
API_REQUEST_TIMEOUT_MS = 20000          # mtop 登录检测请求超时
SEARCH_SELECTOR_TIMEOUT_MS = 20000      # 搜索页商品链接等待超时
PAGE_CLOSE_TIMEOUT_S = 5                # page.close 兜底超时，避免句柄泄漏（H-03）

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

# 商品详情页 URL 模式
ITEM_URL_PATTERN = re.compile(r"/item\?id=(\d+)")

# mtop 接口 AppKey（协议常量，登录检测签名与请求共用）
MTOP_APP_KEY = "34839810"

# ═══════════════════════════════════════════
#  卖家信用等级（从高到低）
# ═══════════════════════════════════════════
SELLER_CREDIT_LEVELS = [
    ("信用极好", 5),
    ("信用优秀", 4.5),
    ("百分百好评", 4.5),
    ("信用较好", 4),
    ("信用一般", 3),
    ("初出茅庐", 2),
    ("信用较差", 1),
    ("信用极差", 0),
]

# 手机号正则（站外联系特征）
PHONE_RE = re.compile(r"1[3-9]\d{9}")


def parse_price(text: str) -> Optional[float]:
    """
    从文本中提取价格。
    支持 "¥ 3999"、"> 2999"、"3999元"、"888.5" 等格式。
    """
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except (ValueError, TypeError):
        return None


def parse_price_extended(text: str, price_num: str = "", price_dec: str = "",
                         wan_scale: bool = False, max_price: float = 0,
                         min_price: float = 0, title: str = "") -> tuple[Optional[float], bool]:
    """
    从价格元素提取价格，识别闲鱼"万元缩写"。

    闲鱼价格元素拆成 number 与 decimal(.小数) 两部分：
      - 普通商品:  ¥2400   → number="2400", decimal=""     → 2400
      - 万元商品:  ¥3.20   → number="3",   decimal=".20"   → 实际 3.20 万元 = 32000
    返回 (价格, 是否万元缩写)。
    万元缩写的判定：wan_scale=True 且 number 部分为 1~9 且 decimal 非空，此时读取"几点几万"。
    若 wan_scale=False，带小数按字面值处理（如 25.5 元保持 25.5）。
    """
    if price_num is not None and price_num != "":
        # 标准 price-wrap 结构
        try:
            num_val = float(price_num.replace(",", ""))
        except (ValueError, TypeError):
            return None, False
        dec = (price_dec or "").strip()
        if dec == "":
            # 无 decimal：普通整数价格（如 2400）
            return num_val, False
        # 有 decimal 段
        frac = "0" + dec if dec.startswith(".") else dec
        try:
            wan = num_val + float(frac)
        except (ValueError, TypeError):
            return None, False
        # 万元缩写只能在有量级证据时启用：显式“万”或监控上限支持万元级。
        # 仅凭 number+decimal 无法区分 3.5 元配件和 3.5 万元设备，默认按字面价格。
        explicit_wan = "万" in (text or "") or "万" in (title or "")
        plausible_wan = max_price > 0 and wan * WAN_UNIT <= max_price * WAN_CANDIDATE_MAX_MULTIPLIER
        # 字面价可疑低：高 min_price 监控下出现明显低于下限的字面价，
        # 判定为万元缩写漏标（万元解释需在监控上限 3 倍内，超预算按万元也不影响过滤）。
        suspicious_literal = (
            min_price > 0 and wan < min_price * WAN_SUSPICIOUS_LITERAL_RATIO
            and wan * WAN_UNIT <= max_price * WAN_CANDIDATE_MAX_MULTIPLIER
        )
        lo, hi = WAN_INT_RANGE
        if wan_scale and lo <= num_val < hi and (explicit_wan or plausible_wan or suspicious_literal):
            return round(wan * WAN_UNIT, 2), True
        return wan, False

    # 回退：直接用 parse_price
    p = parse_price(text)
    return (p, False) if p is not None else (None, False)


def _is_browser_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "browser has been closed" in text
        or ("target page" in text and "closed" in text)
        or "connection closed" in text
        or "context or browser" in text
    )


def compute_mtop_sign(token: str, t: str, app_key: str = MTOP_APP_KEY, data: str = "") -> str:
    """
    计算淘宝 mtop 接口签名。
    算法: md5(token + '&' + t + '&' + appKey + '&' + data)
    token 取自 _m_h5_tk cookie（下划线前的部分）。
    """
    raw = f"{token}&{t}&{app_key}&{data}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


class GoofishMonitor:
    """闲鱼搜索监控器。"""

    def __init__(self, settings: dict):
        self.settings = settings
        self._playwright = None
        self._context: Optional[BrowserContext] = None
        self._api_items: list[dict] = []

    # ─────────────────────────────────────────
    #  浏览器生命周期
    # ─────────────────────────────────────────

    @property
    def user_data_dir(self) -> str:
        return self.settings.get("user_data_dir", "./browser_profile")

    async def start(self):
        """启动持久化浏览器（保留登录状态）。"""
        if self._context:
            return
        self._playwright = await async_playwright().start()

        headless = self.settings.get("headless", False)
        hide_window = bool(self.settings.get("hide_browser_window")) and not headless
        # 注意：无头模式容易被闲鱼风控拦截，默认使用有头模式
        browser_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            # 容器/无显示环境稳定性：--no-zygote 避开 crashpad 在容器的初始化崩溃；
            # 其余为容器最小资源占用与显卡无头环境所需
            "--no-zygote",
            "--disable-crash-reporter",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ]
        if hide_window:
            # 后台抓取：把窗口移到屏幕外（保持有头模式规避风控，但用户不可见）
            browser_args.append("--window-position=-32000,-32000")
        else:
            # 登录/扫码等需要可见窗口：显式指定可见位置，
            # 覆盖 profile 中可能记住的屏外坐标（否则登录窗口会跑到屏幕外无法扫码）
            browser_args.append("--window-position=120,120")
        self._context = await self._playwright.chromium.launch_persistent_context(
            self.user_data_dir,
            headless=headless,
            user_agent=random.choice(USER_AGENTS),
            viewport={
                "width": random.randint(1280, 1440),
                "height": random.randint(800, 900),
            },
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            args=browser_args,
        )
        if hide_window:
            # Windows 下彻底隐藏浏览器窗口（含任务栏按钮），前台工作完全无感知
            _hide_chrome_windows()
        # 清除自动化标记，降低被识别为爬虫的风险
        await self._context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        """)
        await self._inject_login_cookies()
        logger.info(f"浏览器已启动 (headless={headless}, profile={self.user_data_dir})")

    async def _inject_login_cookies(self) -> None:
        """从 cookies 文件注入登录态（服务器"本地扫码模式"）。

        Windows 版 Chromium 的部分 cookie（unb/tracknick）使用新版 app-bound
        加密，Linux 容器无法解密；本地导出明文 cookie 后上传，容器启动时注入。
        注入后 Chromium 以 Linux 格式持久化到 profile，重启无需重复注入。
        """
        cookies_file = self.settings.get("cookies_file") or os.environ.get("MONITOR_COOKIES_FILE", "")
        if not cookies_file or not os.path.exists(cookies_file):
            return
        try:
            with open(cookies_file, encoding="utf-8") as f:
                cookies = json.load(f)
            if not cookies:
                return
            # 已注入过的登录标识直接跳过，避免每次启动重复写入
            existing = {c["name"] for c in await self._context.cookies("https://www.goofish.com")}
            missing = [c for c in cookies if c.get("name") not in existing]
            if not missing:
                return
            await self._context.add_cookies(missing)
            logger.info(f"已注入 {len(missing)} 条登录 cookie（来自 {cookies_file}）")
        except Exception as e:
            logger.warning(f"注入登录 cookie 失败: {e}")

    async def stop(self):
        """关闭浏览器（登录状态会保留在配置目录中）。"""
        if self._context:
            await self._context.close()
            self._context = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.info("浏览器已关闭")

    async def _new_page(self) -> Page:
        """创建页面并挂载 API 响应监听。"""
        self._api_items = []
        page = await self._context.new_page()
        page.on("response", self._on_response)
        return page

    async def _on_response(self, response):
        """拦截搜索 API 响应，收集结构化商品数据。"""
        try:
            url = response.url
            if "mtop" not in url:
                return
            content_type = response.headers.get("content-type", "")
            if "json" not in content_type:
                return
            data = await response.json()
            self._extract_from_json(data)
        except Exception as e:
            # 单条响应解析失败不中断监控，仅记录以便排查页面结构变化
            logger.debug(f"搜索响应解析失败: {e}")

    def _extract_from_json(self, data):
        """从 JSON 数据中递归提取商品信息。"""
        if isinstance(data, dict):
            for key, value in data.items():
                if key in ("items", "itemList", "feeds", "result", "dataList", "itemsList") \
                        and isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            self._parse_api_item(item)
                else:
                    self._extract_from_json(value)
        elif isinstance(data, list):
            for item in data:
                self._extract_from_json(item)

    def _parse_api_item(self, item: dict):
        """解析单个 API 商品项。"""
        item_id = (
            item.get("itemId") or item.get("item_id")
            or item.get("id") or item.get("itemIdStr")
        )
        title = (
            item.get("title") or item.get("itemTitle")
            or item.get("name") or item.get("subject")
        )
        price_text = (
            item.get("price") or item.get("priceText")
            or item.get("priceString") or item.get("displayPrice")
            or item.get("priceInfo", {}).get("price")
            or item.get("priceInfo", {}).get("priceText")
        )
        image = (
            item.get("picUrl") or item.get("pic_url")
            or item.get("image") or item.get("mainPic")
            or (item.get("images", [None]) or [None])[0]
            or item.get("pics", [None])[0]
        )
        location = item.get("location") or item.get("city") or ""
        status = item.get("itemStatus") or item.get("status") or ""

        if not item_id or not title:
            return

        price = parse_price(str(price_text)) if price_text else None
        if price is None:
            return

        self._api_items.append({
            "item_id": str(item_id),
            "title": str(title).strip(),
            "price": price,
            "raw_price": str(price_text),
            "url": f"https://www.goofish.com/item?id={item_id}",
            "image": str(image or ""),
            "location": str(location or ""),
            "status": str(status or ""),
        })

    # ─────────────────────────────────────────
    #  登录状态
    # ─────────────────────────────────────────

    async def check_login_status(self, page: Optional[Page] = None) -> bool:
        """
        检查是否已登录（不刷新页面）。
        通过登录标识 cookie 精确判断，API 与 DOM 检查作为辅助。
        """
        # 方式一：登录标识 cookie（unb/tracknick 仅在登录后出现，判定准确）
        if await self._check_login_cookie():
            return True

        # 方式二：调用 mtop 登录用户 API
        if await self._check_login_via_api():
            return True

        # 方式三：导航到首页检查登录链接（兜底）
        own_page = False
        if page is None:
            if self._context.pages:
                page = self._context.pages[0]
            else:
                page = await self._new_page()
                own_page = True

        try:
            await page.goto("https://www.goofish.com/", wait_until="domcontentloaded", timeout=PAGE_NAV_TIMEOUT_MS)
            await self._human_delay(2, 4)
            login_links = await page.eval_on_selector_all(
                "a[href*='/login']", "els => els.length"
            )
            return login_links == 0
        except Exception as e:
            logger.error(f"检查登录状态失败: {e}")
            return False
        finally:
            if own_page:
                await page.close()

    async def _check_login_cookie(self) -> bool:
        """
        检查登录标识 cookie。
        unb（用户ID）和 tracknick（昵称）只有在登录成功后才会写入，
        未登录的匿名会话不会产生这两个 cookie。
        """
        try:
            cookies = await self._context.cookies()
            for c in cookies:
                # 仅 unb/tracknick 是可靠登录标识（登录成功后才会写入）。
                # last_u_xianyu_web 是浏览追踪 cookie，匿名会话也会存在，会误判为已登录。
                if c["name"] in ("unb", "tracknick") and c.get("value"):
                    return True
        except Exception as e:
            logger.debug(f"登录 cookie 检查失败: {e}")
        return False

    async def _check_login_via_api(self) -> bool:
        """
        调用 mtop.taobao.idlemessage.pc.loginuser.get 接口判断登录状态。
        不刷新页面，使用浏览器上下文自动携带 cookie。
        """
        try:
            cookies = await self._context.cookies()
            m_h5_tk = next((c["value"] for c in cookies if c["name"] == "_m_h5_tk"), "")
            if not m_h5_tk:
                return False

            token = m_h5_tk.split("_")[0]
            t = str(int(time.time() * 1000))
            sign = compute_mtop_sign(token, t)
            url = (
                "https://h5api.m.goofish.com/h5/mtop.taobao.idlemessage.pc.loginuser.get/1.0/"
                f"?jsv=2.7.2&appKey={MTOP_APP_KEY}&t={t}&sign={sign}&v=1.0&type=originaljson"
                "&accountSite=xianyu&dataType=json&timeout=20000"
                "&api=mtop.taobao.idlemessage.pc.loginuser.get&sessionOption=AutoLoginOnly"
            )
            resp = await self._context.request.get(url)
            if resp.status != 200:
                return False
            data = await resp.json()
            ret = data.get("ret", [])
            if not (ret and str(ret[0]).startswith("SUCCESS")):
                return False
            # ret=SUCCESS 仅代表 mtop 调用成功，不代表已登录；
            # 还需返回体带用户数据才算真正登录，避免未登录时误判。
            return bool(data.get("data"))
        except Exception as e:
            logger.debug(f"API 登录检测失败: {e}")
            return False

    async def clear_login_state(self) -> None:
        """清除登录态 cookie，强制重新登录时显示二维码。

        已登录状态下直接打开登录页，_poll_login_status 会立即命中历史 cookie
        而秒关窗口；重新登录（切号/过期重登）前先清 cookie，保证二维码稳定显示。
        """
        try:
            await self._context.clear_cookies()
            logger.info("已清除登录态 cookie，等待重新扫码")
        except Exception as e:
            logger.warning(f"清除登录态失败: {e}")

    async def wait_for_login(self, timeout_minutes: int = 10):
        """
        等待用户手动登录。打开闲鱼首页，用户扫码登录后自动继续。
        轮询检测登录状态时不会刷新页面，避免打断用户登录操作。
        """
        page = await self._new_page()
        try:
            await page.goto("https://www.goofish.com/", wait_until="domcontentloaded", timeout=PAGE_NAV_TIMEOUT_MS)
            # 等页面脚本加载完成，避免误判
            await self._human_delay(3, 5)
            logger.info("=" * 55)
            logger.info("  请在打开的浏览器窗口中扫码登录闲鱼")
            logger.info("  登录成功后本窗口会自动识别（最多等待 %d 分钟）", timeout_minutes)
            logger.info("  建议使用小号监控，更加安全")
            logger.info("=" * 55)

            start = asyncio.get_event_loop().time()
            while asyncio.get_event_loop().time() - start < timeout_minutes * 60:
                if await self._poll_login_status(page):
                    logger.info("✅ 登录成功！登录状态已保存")
                    return True
                await asyncio.sleep(3)
            logger.warning("等待登录超时")
            return False
        finally:
            await page.close()

    async def _poll_login_status(self, page: Page) -> bool:
        """
        轮询检测登录状态（不刷新页面）。

        仅以登录标识 cookie（unb/tracknick，登录成功后才写入）为准。
        不再使用 mtop API / DOM 链接判断：两者在未登录时会误报成功，
        导致二维码页面还没扫就被判定"已登录"而秒关。
        """
        return await self._check_login_cookie()

    # ─────────────────────────────────────────
    #  搜索与解析
    # ─────────────────────────────────────────

    def _build_search_url(self, keyword: str) -> str:
        """构建搜索 URL。"""
        from urllib.parse import quote
        return f"https://www.goofish.com/search?q={quote(keyword)}"

    async def _extract_from_dom(self, page: Page, wan_scale: bool = False,
                                max_price: float = 0, min_price: float = 0) -> list[dict]:
        """
        从渲染后的 DOM 中提取商品信息。
        基于闲鱼搜索卡片的标准结构：
          a[href*='/item?id='] > .feeds-content > .row1-wrap-title(.main-title) + .row3-wrap-price(.price-wrap)
        选择器使用模糊 class 匹配（class 名带随机后缀），兼容页面结构变化。
        wan_scale=True 时启用万元缩写修正（闲鱼省略"万"字）。
        """
        cards = await page.eval_on_selector_all(
            "a[href*='/item?id=']",
            """els => els.map(el => {
                const href = el.getAttribute('href') || '';
                const get = (sel) => {
                    const node = el.querySelector(sel);
                    if (!node) return '';
                    // 优先取 title 属性：完整标题（显示文本可能被 CSS 省略，
                    // 用截断后的标题会导致排除词/必须包含词漏检）
                    const attr = node.getAttribute('title');
                    if (attr && attr.trim()) return attr.trim();
                    return (node.innerText || node.textContent || '').trim();
                };
                const pw = el.querySelector('[class*="price-wrap"]');
                const numEl = pw ? pw.querySelector('[class*="number"]') : null;
                const decEl = pw ? pw.querySelector('[class*="decimal"]') : null;
                return {
                    href: href,
                    title: get('[class*="main-title"]'),
                    price: get('[class*="price-wrap"]'),
                    priceNum: numEl ? (numEl.textContent || '').trim() : '',
                    priceDec: decEl ? (decEl.textContent || '').trim() : '',
                    location: get('[class*="seller-text"]'),
                    status: get('[class*="price-desc"]'),
                    credit: get('[class*="credit-container"]'),
                };
            })""",
        )

        items = []
        for card in cards:
            match = ITEM_URL_PATTERN.search(card["href"])
            if not match:
                continue
            item_id = match.group(1)

            # 解析价格。
            # 注意：闲鱼对万元级商品的价格元素拆成 number(.整数) + decimal(.小数) 两个 span，
            # textContent 如 "¥3.20" 实际表示 3.20 万元 = ¥32000（万元隐式，无"万"字）。
            # wan_scale=True 时才把 "X.YY"(1<=X<10) 解释为万元，避免误伤真实低价商品。
            price, wan_flag = parse_price_extended(
                card["price"], card["priceNum"], card["priceDec"],
                wan_scale=wan_scale, max_price=max_price, min_price=min_price,
                title=card["title"]
            )
            if price is None:
                continue

            title = card["title"] or "未知标题"
            title = re.sub(r"\s+", " ", title).strip()

            items.append({
                "item_id": item_id,
                "title": title[:80],
                "price": price,
                "raw_price": card["price"].replace("\n", " ") + ("(万元)" if wan_flag else ""),
                "url": f"https://www.goofish.com/item?id={item_id}",
                "image": "",
                "location": card["location"],
                "status": card["status"],
                "seller_credit": (card["credit"] or "").strip(),
            })

        return items

    async def search(self, keyword: str, max_price: float = 0,
                     min_price: float = 0) -> list[dict]:
        """
        搜索指定关键词，返回商品列表。
        过滤掉与关键词无关的推荐商品。
        万元缩写修正不再依赖 max_price 阈值（C2）：只要 DOM 拆成 number+decimal 且 1<=number<10，
        统一按“X.YY 万元”修正，避免不同阈值下同一商品价格不一致。
        """
        page = await self._new_page()
        try:
            url = self._build_search_url(keyword)
            logger.info(f"正在搜索: {keyword}")

            await page.goto(url, wait_until="domcontentloaded")
            # 等待商品链接出现
            try:
                await page.wait_for_selector(
                    "a[href*='/item?id=']",
                    timeout=SEARCH_SELECTOR_TIMEOUT_MS,
                )
            except PlaywrightTimeoutError:
                logger.warning(f"搜索 {keyword} 未找到商品链接（可能未登录或没有结果）")

            # 等待渲染稳定
            await self._human_delay(2.0, 4.0)

            max_items = self.settings.get("max_items_per_page", 60)

            # 1) 切到"新发布"（最新）排序：更符合"盯最新上架"需求
            await self._click_label_filter(page, "新发布")

            # 2) 设置价格范围（如果配置了价格区间），服务端只返回区间内商品
            if min_price > 0 or max_price > 0:
                await self._set_price_range(page, min_price, max_price)

            # 3) 翻页抓取：一直翻到最后一页 / 达到上限 / 翻页失效
            all_items: list[dict] = []
            seen_ids: set[str] = set()
            page_total = 1  # 分页指示 "当前/总数"，未知时按 1 处理
            stale_rounds = 0  # 连续无新增的轮数，超过即视为已到底
            for _round in range(self.settings.get("max_scrape_pages", 30)):
                # DOM 解析（主要）；万元判定由显式“万”或 max_price 量级共同确认
                dom_items = await self._extract_from_dom(
                    page, wan_scale=True, max_price=max_price, min_price=min_price,
                )
                merged = self._merge_items(dom_items)
                new_ones = [i for i in merged if i["item_id"] not in seen_ids]
                seen_ids.update(i["item_id"] for i in merged)
                all_items.extend(new_ones)

                page_total = await self._current_page_total(page, page_total)

                # 提前结束：收集足够多 / 只有一页 / 已到最后一页
                if len(all_items) >= max_items or page_total <= 1:
                    break
                if _round + 1 >= page_total:
                    break

                # 连续 2 轮无新增商品 → 已到底，终止（避免空翻）
                if not new_ones:
                    stale_rounds += 1
                    if stale_rounds >= 2:
                        break
                else:
                    stale_rounds = 0

                # 点下一页并等待页码真正前进，确保下一页已加载完再收集
                cur_page = await self._current_page_no(page)
                if not await self._click_next_page(page):
                    break
                if not await self._wait_page_advance(page, cur_page):
                    break
                await self._human_delay(1.0, 2.0)

            # 按关键词过滤，剔除"猜你喜欢"推荐区的无关商品
            all_items = [i for i in all_items if matches_keyword(i["title"], keyword)]

            # 限制数量
            all_items = all_items[:max_items]

            logger.info(f"搜索 {keyword} 共提取 {len(all_items)} 条商品")
            return all_items
        except Exception as e:
            if _is_browser_error(e):
                raise
            logger.error(f"搜索 {keyword} 失败: {e}")
            return []
        finally:
            # B2: page.close 超时兜底，避免异常路径泄漏页面句柄
            try:
                await asyncio.wait_for(page.close(), timeout=PAGE_CLOSE_TIMEOUT_S)
            except Exception:
                try:
                    # 兜底：若 close 超时/失败，尝试强制关闭上下文页面列表
                    if not page.is_closed():
                        await page.close()
                except Exception:
                    pass

    @staticmethod
    async def _click_label_filter(page: Page, label: str) -> None:
        """点击筛选栏中的文本标签（如"新发布"排序）。类名带随机后缀，按文本精确匹配。"""
        try:
            el = page.get_by_text(label, exact=True).first
            await el.click(force=True, timeout=8000)
            await page.wait_for_timeout(2500)
        except Exception:
            logger.debug(f"筛选标签 {label} 点击失败（可能不存在）")

    @staticmethod
    async def _set_price_range(page: Page, min_price: float, max_price: float) -> None:
        """在搜索页价格输入框填入区间并点"确定"（服务端过滤，分页总量随之缩小）。"""
        try:
            inputs = page.locator("input")
            n = await inputs.count()
            if n < 3:
                return
            if min_price > 0:
                await inputs.nth(1).fill(str(int(min_price)))
            if max_price > 0:
                await inputs.nth(2).fill(str(int(max_price)))
            await page.wait_for_timeout(400)
            # "确定"按钮可能在视口外，用 JS click 绕过
            await page.evaluate("""() => {
                const b = [...document.querySelectorAll('button')]
                    .find(x => x.textContent.trim() === '确定');
                if (b) b.click();
            }""")
            await page.wait_for_timeout(3500)
        except Exception as e:
            logger.debug(f"设置价格范围失败: {e}")

    @staticmethod
    async def _current_page_total(page: Page, fallback: int) -> int:
        """读取分页指示 "当前/总数"（如 2/11），失败时返回 fallback。"""
        try:
            txt = await page.evaluate("""() => {
                const el = [...document.querySelectorAll('*')]
                    .find(e => /^\\s*\\d+\\s*\\/\\s*\\d+\\s*$/.test(e.textContent));
                return el ? el.textContent.trim() : '';
            }""")
            if "/" in txt:
                parts = txt.split("/")
                if len(parts) == 2 and parts[1].strip().isdigit():
                    return int(parts[1].strip())
        except Exception:
            pass
        return fallback

    @staticmethod
    async def _current_page_no(page: Page) -> int:
        """读取当前页码（分页指示 "当前/总数" 的"当前"），失败返回 0。"""
        try:
            txt = await page.evaluate("""() => {
                const el = [...document.querySelectorAll('*')]
                    .find(e => /^\\s*\\d+\\s*\\/\\s*\\d+\\s*$/.test(e.textContent));
                return el ? el.textContent.trim() : '';
            }""")
            if "/" in txt:
                parts = txt.split("/")
                if parts[0].strip().isdigit():
                    return int(parts[0].strip())
        except Exception:
            pass
        return 0

    @staticmethod
    async def _wait_page_advance(page: Page, prev_page: int) -> bool:
        """点击翻页后等待页码前进或商品集合变化（最多 12 秒），避免收集到未刷新的旧页。"""
        import time as _time
        start = _time.monotonic()
        while _time.monotonic() - start < 12:
            cur = await GoofishMonitor._current_page_no(page)
            if cur and cur != prev_page:
                return True
            try:
                await page.wait_for_timeout(800)
            except Exception:
                return False
        return False

    @staticmethod
    async def _click_next_page(page: Page) -> bool:
        """点下一页：tiny 版分页的右箭头（最后一个非禁用的 search-page-tiny-arrow-container 按钮）。"""
        try:
            btns = page.locator('button[class*="search-page-tiny-arrow-container"]')
            n = await btns.count()
            # 右箭头是最后一个按钮
            for i in range(n - 1, -1, -1):
                if not await btns.nth(i).is_disabled():
                    await btns.nth(i).click(force=True, timeout=8000)
                    return True
        except Exception:
            pass
        # 兜底：桌面版分页的"下一页"文本按钮
        try:
            nxt = page.get_by_text("下一页", exact=True).first
            await nxt.click(force=True, timeout=5000)
            return True
        except Exception:
            return False

    async def _scroll_and_collect(self, page: Page) -> None:
        """模拟人类滚动页面，触发懒加载。

        闲鱼搜索为瀑布流无限滚动：只有滚动到底才会加载更多。
        持续滚动直到"连续两轮没有新商品"（已到底）或达到轮次上限，
        避免过度滚动触发风控。轮次上限由 MONITOR_SCROLL_ROUNDS 配置。
        """
        max_rounds = max(int(self.settings.get("scroll_rounds", 6)), 0)
        idle = 0
        seen_count = 0
        for _ in range(max_rounds):
            try:
                count = await page.eval_on_selector_all(
                    "a[href*='/item?id=']", "els => els.length")
                if count <= seen_count:
                    idle += 1
                    if idle >= 2:
                        break
                else:
                    idle = 0
                seen_count = max(seen_count, count)
                await page.mouse.wheel(0, random.randint(700, 1300))
                await self._human_delay(1.0, 2.0)
            except Exception:
                break

    def _merge_items(self, dom_items: list[dict]) -> list[dict]:
        """合并 DOM 数据与 API 数据，以 DOM 为准，API 补充字段。"""
        merged = {item["item_id"]: item for item in dom_items}
        for api_item in self._api_items:
            item_id = api_item["item_id"]
            if item_id not in merged:
                merged[item_id] = api_item
            else:
                if not merged[item_id].get("image") and api_item.get("image"):
                    merged[item_id]["image"] = api_item["image"]
                if not merged[item_id].get("location") and api_item.get("location"):
                    merged[item_id]["location"] = api_item["location"]
                if not merged[item_id].get("status") and api_item.get("status"):
                    merged[item_id]["status"] = api_item["status"]
        return list(merged.values())

    @staticmethod
    async def _human_delay(min_sec: float, max_sec: float):
        """模拟人类操作的自然延迟。"""
        await asyncio.sleep(random.uniform(min_sec, max_sec))


def matches_keyword(title: str, keyword: str) -> bool:
    """
    判断标题是否与搜索关键词相关（严格模式）。
    将关键词拆分为词元（中文词组 / 字母数字串）：
      - 含较长主词（长度 >= 4，如 "iphone"、"dgx"、"switch"）时，还需再命中任一其他词元
        （如搜 "DGX spark" 必须同时含 "dgx" 和 "spark"，杜绝 "大疆Spark" 混入）
      - 不含主词时，需命中至少 2 个词元
      - 若关键词含"型号数字词"（如 15 / 256 / m3 / 4090），标题必须包含，杜绝 "14" 冒充 "15"
    用于过滤"猜你喜欢"推荐区、同名词的不同品类及不同型号商品。
    """
    if not keyword or not title:
        return False
    title_lower = title.lower()
    tokens = re.findall(r"[\u4e00-\u9fa5]{2,}|[a-zA-Z0-9]+", keyword)
    if not tokens:
        return False

    matched = [t for t in tokens if len(t) >= 2 and t.lower() in title_lower]

    # 型号数字词：含数字的词元（如 "15"、"256"、"m3"、"a7m4"、"4090"）是区分型号的关键，
    # 标题必须命中全部，防止 "iPhone 14" 混入 "iPhone 15"，"4060" 冒充 "4090" 等。
    model_tokens = [
        t for t in tokens
        if re.search(r"\d", t) and len(t) >= 2
    ]
    model_tokens.extend(t for t in tokens if t.lower() in PRO_MODEL_WORDS)
    for t in model_tokens:
        if t.lower() not in title_lower:
            return False

    # 命中主词（较长词元）→ 还需任意一个其他词元
    for t in matched:
        if len(t) >= 4:
            return len(matched) >= 2
    # 无主词 → 需至少 2 个词元
    return len(matched) >= 2


# ═══════════════════════════════════════════
#  卖家信用评分与防骗检测
# ═══════════════════════════════════════════

def credit_score(credit_text: str) -> Optional[float]:
    """
    将卖家信用文本转换为分数（无对应等级返回 None）。
    信用文本形如 "卖家信用极好"、"百分百好评"、"初出茅庐"。
    """
    if not credit_text:
        return None
    for name, score in SELLER_CREDIT_LEVELS:
        if name in credit_text:
            return score
    return None


def detect_scam(
    item: dict,
    median_price: float = 0,
    scam_rules: Optional[dict] = None,
    anomaly_ratio: float = 0.5,
) -> tuple[list[str], list[str]]:
    """
    识别引流/虚假商品文案。
    返回 (硬过滤原因, 警告标记)：
      - 硬过滤：站外导流、先款定金、仿品标识（脱离平台/明确假货，直接过滤）
      - 警告标记：夸张承诺、压力话术、规避用语、价格异常（可能误伤，仅提示）
    触发词配置见 config.py 的 SCAM_RULES。
    """
    if scam_rules is None:
        scam_rules = {}
    hard: list[str] = []
    warn: list[str] = []

    title = item.get("title", "") or ""
    status = item.get("status", "") or ""
    text = f"{title} {status}"

    for name, rule in scam_rules.items():
        mode = rule.get("mode", "mark")
        keywords = rule.get("keywords", [])
        for kw in keywords:
            if kw and kw in text:
                (hard if mode == "exclude" else warn).append(f"{name}({kw})")
                break

    # 手机号外露 → 疑似站外联系，仅标记（部分真实卖家会留电话）
    if PHONE_RE.search(text):
        warn.append("标题含手机号(疑似站外联系)")

    # 价格明显低于市场中位数 → 真捡漏 or 引流标低价
    price = item.get("price", 0) or 0
    if median_price > 0 and price > 0 and anomaly_ratio > 0:
        if price < median_price * anomaly_ratio:
            warn.append(f"价格仅为市场中位价 ¥{median_price:.0f} 的 {int(anomaly_ratio * 100)}% 以下")

    return hard, warn


def evaluate_item(
    item: dict,
    max_price: float,
    min_price: float,
    exclude_keywords: list[str],
    must_include: list[str] = None,
    min_seller_credit: str = "信用一般",
    strict_unknown_credit: bool = False,
    median_price: float = 0,
    scam_rules: Optional[dict] = None,
    anomaly_ratio: float = 0.5,
) -> dict:
    """
    综合评估商品是否值得推送。
    返回 {"pass": bool, "hard": [过滤原因], "warn": [警告标记]}。
    任一硬性原因（价格区间/排除词/必须包含/信用过低/高风险文案）都会导致不推送。
    """
    hard: list[str] = []
    warn: list[str] = []

    price = item.get("price", 0) or 0
    if price < min_price:
        hard.append(f"低于最低价 ¥{min_price}")
    if price > max_price:
        hard.append(f"高于最高价 ¥{max_price}")

    title = item.get("title", "") or ""
    for kw in exclude_keywords:
        if kw and kw in title:
            hard.append(f"含排除词「{kw}」")

    # 必须包含词：标题必须包含全部指定词（用于区分不同配置）
    for kw in must_include or []:
        if kw and kw not in title:
            hard.append(f"缺必需词「{kw}」")

    # 卖家信用过滤：信用等级低于门槛则过滤
    credit = item.get("seller_credit", "") or ""
    if credit:
        score = credit_score(credit)
        threshold = credit_score(min_seller_credit)
        if score is not None and threshold is not None and score < threshold:
            hard.append(f"卖家信用过低({credit})")
        elif score is None:
            (hard if strict_unknown_credit else warn).append("卖家信用未知")
    elif strict_unknown_credit:
        hard.append("卖家信用未知")
    else:
        warn.append("卖家信用未知")

    # 引流文案识别
    scam_hard, scam_warn = detect_scam(
        item, median_price=median_price,
        scam_rules=scam_rules, anomaly_ratio=anomaly_ratio,
    )
    hard.extend(scam_hard)
    warn.extend(scam_warn)

    return {"pass": not hard, "hard": hard, "warn": warn}
