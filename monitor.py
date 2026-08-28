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
import logging
import random
import re
import time
from typing import Optional

from playwright.async_api import (
    async_playwright,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

logger = logging.getLogger(__name__)

# 常见 UA 池，随机选择，模拟不同用户
PRO_MODEL_WORDS = {"pro", "max", "plus", "mini", "ultra", "promax", "pro max"}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

# 商品详情页 URL 模式
ITEM_URL_PATTERN = re.compile(r"/item\?id=(\d+)")

# 搜索 API 名称（用于拦截响应，获取结构化数据）
SEARCH_API_NAMES = [
    "idlemtopsearch.pc.search",
    "idlemtopsearch.pc.item.search",
]

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
                         title: str = "") -> tuple[Optional[float], bool]:
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
        # 万元缩写只能在有量级证据时启用：显式“万”或标题/监控上限支持万元级。
        # 仅凭 number+decimal 无法区分 3.5 元配件和 3.5 万元设备，默认按字面价格。
        explicit_wan = "万" in (text or "") or "万" in (title or "")
        scale_supported = max_price >= 10000 if max_price else False
        if wan_scale and 1 <= num_val < 10 and (explicit_wan or scale_supported):
            return wan * 10000, True
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


def compute_mtop_sign(token: str, t: str, app_key: str = "34839810", data: str = "") -> str:
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
        self._search_api_data: list[dict] = []

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
        # 注意：无头模式容易被闲鱼风控拦截，默认使用有头模式
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
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        # 清除自动化标记，降低被识别为爬虫的风险
        await self._context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        """)
        logger.info(f"浏览器已启动 (headless={headless}, profile={self.user_data_dir})")

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
        self._search_api_data = []
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
            # 记录搜索结果
            for api_name in SEARCH_API_NAMES:
                if api_name in url:
                    self._search_api_data.append(data)
            self._extract_from_json(data)
        except Exception:
            pass

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
            await page.goto("https://www.goofish.com/", wait_until="domcontentloaded", timeout=30000)
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
                if c["name"] in ("unb", "tracknick", "last_u_xianyu_web") and c.get("value"):
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
                f"?jsv=2.7.2&appKey=34839810&t={t}&sign={sign}&v=1.0&type=originaljson"
                "&accountSite=xianyu&dataType=json&timeout=20000"
                "&api=mtop.taobao.idlemessage.pc.loginuser.get&sessionOption=AutoLoginOnly"
            )
            resp = await self._context.request.get(url)
            if resp.status != 200:
                return False
            data = await resp.json()
            ret = data.get("ret", [])
            return bool(ret and str(ret[0]).startswith("SUCCESS"))
        except Exception as e:
            logger.debug(f"API 登录检测失败: {e}")
            return False

    async def wait_for_login(self, timeout_minutes: int = 10):
        """
        等待用户手动登录。打开闲鱼首页，用户扫码登录后自动继续。
        轮询检测登录状态时不会刷新页面，避免打断用户登录操作。
        """
        page = await self._new_page()
        try:
            await page.goto("https://www.goofish.com/", wait_until="domcontentloaded", timeout=30000)
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
          1. 登录标识 cookie（unb/tracknick 仅登录后出现）
          2. mtop 登录用户 API
          3. 页面 DOM 中登录链接是否消失
        """
        # 方案一：登录标识 cookie
        if await self._check_login_cookie():
            return True

        # 方案二：API 精确检测
        if await self._check_login_via_api():
            return True

        # 方案三：页面 DOM 中"登录"链接是否消失
        try:
            login_links = await page.eval_on_selector_all(
                "a[href*='/login']", "els => els.length"
            )
            if login_links == 0:
                return True
        except Exception:
            pass

        return False

    # ─────────────────────────────────────────
    #  搜索与解析
    # ─────────────────────────────────────────

    def _build_search_url(self, keyword: str) -> str:
        """构建搜索 URL。"""
        from urllib.parse import quote
        base = f"https://www.goofish.com/search?q={quote(keyword)}"
        # 排序参数可能触发风控，默认不加（综合排序）
        return base

    async def _extract_from_dom(self, page: Page, wan_scale: bool = False,
                                max_price: float = 0) -> list[dict]:
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
                    return node ? (node.innerText || node.textContent || '').trim() : '';
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
                wan_scale=wan_scale, max_price=max_price, title=card["title"]
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
                     min_price: float = 0, must_include: list[str] = None) -> list[dict]:
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
                    timeout=20000,
                )
            except PlaywrightTimeoutError:
                logger.warning(f"搜索 {keyword} 未找到商品链接（可能未登录或没有结果）")

            # 等待渲染稳定
            await self._human_delay(2.0, 4.0)

            # 模拟滚动加载更多
            await self._scroll_and_collect(page)

            # DOM 解析（主要）；万元判定由显式“万”或 max_price 量级共同确认
            dom_items = await self._extract_from_dom(page, wan_scale=True, max_price=max_price)

            # 合并 API 数据（补充字段）
            all_items = self._merge_items(dom_items)

            # 按关键词过滤，剔除"猜你喜欢"推荐区的无关商品
            all_items = [i for i in all_items if matches_keyword(i["title"], keyword)]

            # 限制数量
            max_items = self.settings.get("max_items_per_page", 60)
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
                await asyncio.wait_for(page.close(), timeout=5)
            except Exception:
                try:
                    # 兜底：若 close 超时/失败，尝试强制关闭上下文页面列表
                    if not page.is_closed():
                        await page.close()
                except Exception:
                    pass

    async def _scroll_and_collect(self, page: Page):
        """模拟人类滚动页面，触发懒加载。"""
        scroll_rounds = random.randint(1, 3)
        for _ in range(scroll_rounds):
            try:
                await page.mouse.wheel(0, random.randint(600, 1200))
                await self._human_delay(0.8, 1.8)
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


def filter_items(
    items: list[dict],
    max_price: float,
    min_price: float,
    exclude_keywords: list[str],
) -> list[dict]:
    """
    过滤商品：价格在 [min_price, max_price] 区间内，
    且标题不包含排除关键词。
    """
    results = []
    for item in items:
        price = item.get("price", 0)
        title = item.get("title", "")
        if price < min_price or price > max_price:
            continue
        if any(kw in title for kw in exclude_keywords):
            continue
        results.append(item)
    return results


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
            hard.append("卖家信用未知")
    else:
        hard.append("卖家信用未知")

    # 引流文案识别
    scam_hard, scam_warn = detect_scam(
        item, median_price=median_price,
        scam_rules=scam_rules, anomaly_ratio=anomaly_ratio,
    )
    hard.extend(scam_hard)
    warn.extend(scam_warn)

    return {"pass": not hard, "hard": hard, "warn": warn}