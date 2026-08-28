"""
后台监控服务。
在独立线程中运行闲鱼监控循环（自带 asyncio 事件循环），
将搜索结果写入数据库，并触发通知。仪表盘与监控服务解耦，通过数据库共享数据。
"""

import asyncio
import logging
import threading
import time
from datetime import datetime

from config import MIN_SELLER_CREDIT, MONITOR_ITEMS, MONITOR_SETTINGS, SCAM_RULES
from database import Database, parse_exclude_keywords
from monitor import GoofishMonitor, credit_score, evaluate_item, filter_items
from notifier import NotifierManager

logger = logging.getLogger(__name__)


class BrowserDeadError(RuntimeError):
    """浏览器上下文已失效，需要自动重启。"""


def _is_browser_error(exc: Exception) -> bool:
    """判断异常是否由浏览器/上下文失效引起。"""
    text = str(exc).lower()
    return (
        "browser has been closed" in text
        or ("target page" in text and "closed" in text)
        or "connection closed" in text
        or "context or browser" in text
    )


def build_monitor_settings(db: Database) -> dict:
    """从数据库读取运行设置，合并配置文件默认值。"""
    settings = dict(MONITOR_SETTINGS)
    try:
        headless = db.get_setting("headless", "")
        if headless in ("true", "True", "1"):
            settings["headless"] = True
        elif headless in ("false", "False", "0"):
            settings["headless"] = False
    except Exception:
        pass
    return settings


def seed_products_from_config(db: Database) -> int:
    """首次运行时，把 config.py 里的监控商品同步到数据库。"""
    existing = db.get_products()
    if existing:
        return 0
    count = 0
    for item in MONITOR_ITEMS:
        keyword = item.get("keyword")
        if not keyword:
            continue
        db.add_product(
            keyword=keyword,
            max_price=item.get("max_price", 99999),
            min_price=item.get("min_price", 0),
            exclude_keywords=",".join(item.get("exclude_keywords", [])),
            must_include=",".join(item.get("must_include", [])),
            enabled=1,
        )
        count += 1
    return count


class MonitorService:
    """后台监控服务：线程 + 独立 asyncio 事件循环。"""

    def __init__(self, db: Database, notifier: NotifierManager):
        self.db = db
        self.notifier = notifier
        self._stop_event = threading.Event()
        self._trigger_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._browser_lock = asyncio.Lock()

        # 运行状态（供仪表盘查询）
        self.status = "stopped"          # stopped | starting | running | checking | error
        self.current_keyword: str = ""
        self.last_check_at: float | None = None
        self.next_check_at: float | None = None
        self.last_error: str = ""
        self.round_items = 0
        self.round_matches = 0

    # ─────────────────────────────────────
    #  生命周期
    # ─────────────────────────────────────

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._thread_main, daemon=True, name="monitor")
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        self._trigger_event.set()

    def trigger_check(self):
        """立即触发一轮检查。"""
        self._trigger_event.set()

    def _thread_main(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._monitor_loop())

    # ─────────────────────────────────────
    #  主监控循环
    # ─────────────────────────────────────

    async def _monitor_loop(self):
        monitor = GoofishMonitor(build_monitor_settings(self.db))
        self.status = "starting"

        # 启动浏览器（失败自动重试）
        if not await self._start_browser_with_retry(monitor):
            self.status = "error"
            logger.error(f"浏览器启动失败: {self.last_error}")
            return
        self.status = "running"
        logger.info("监控服务已启动")

        restart_delay = 5
        try:
            while not self._stop_event.is_set():
                try:
                    await self._check_round(monitor)
                    restart_delay = 5  # 成功后重置退避
                except Exception as e:
                    self.status = "error"
                    self.last_error = str(e)
                    logger.error(f"监控轮次异常: {e}")
                    if not _is_browser_error(e):
                        logger.info("非浏览器异常，等待下轮再试")
                        await asyncio.sleep(30)
                        continue
                    async with self._browser_lock:
                        ok = await self._restart_browser(monitor)
                    if not ok:
                        break
                    logger.warning(f"{restart_delay} 秒后自动重试...")
                    await asyncio.sleep(restart_delay)
                    restart_delay = min(restart_delay * 2, 120)
                    continue  # 立即重试本轮

                interval = float(self.db.get_setting("interval_minutes", 30))
                self.next_check_at = time.time() + interval * 60
                logger.info(f"本轮检查完成，{interval} 分钟后进行下一轮")

                # 等待下一轮或手动触发
                while not self._stop_event.is_set():
                    remaining = self.next_check_at - time.time()
                    if remaining <= 0:
                        break
                    if self._trigger_event.wait(timeout=min(remaining, 5)):
                        self._trigger_event.clear()
                        break
                    if self._stop_event.is_set():
                        break
        finally:
            try:
                await monitor.stop()
            except Exception:
                pass
            if not self._stop_event.is_set():
                self.status = "error"
            else:
                self.status = "stopped"
            logger.info("监控服务已停止")

    async def _start_browser_with_retry(self, monitor: GoofishMonitor, attempts: int = 3) -> bool:
        """启动浏览器，失败时重试数次。"""
        for i in range(attempts):
            try:
                await monitor.start()
                return True
            except Exception as e:
                self.last_error = str(e)
                logger.error(f"浏览器启动失败 ({i + 1}/{attempts}): {e}")
                await asyncio.sleep(5)
        return False

    async def _restart_browser(self, monitor: GoofishMonitor) -> bool:
        """重启浏览器（登录配置保留在 profile 目录）。"""
        self.status = "error"
        try:
            await monitor.stop()
        except Exception:
            pass
        await asyncio.sleep(2)  # 等待进程/端口完全释放
        try:
            await monitor.start()
            logger.info("浏览器已自动重启")
            return True
        except Exception as e:
            self.last_error = f"浏览器重启失败: {e}"
            logger.error(f"浏览器重启失败: {e}")
            return False

    async def _ensure_browser(self, monitor: GoofishMonitor):
        """检查浏览器是否存活，失效则抛出 BrowserDeadError 由外层重启。"""
        ctx = getattr(monitor, "_context", None)
        if ctx is not None:
            try:
                if not ctx.is_closed():
                    return
            except Exception:
                pass
        raise BrowserDeadError("浏览器已失效，正在自动重启")

    # ─────────────────────────────────────
    #  单轮检查
    # ─────────────────────────────────────

    async def _check_round(self, monitor: GoofishMonitor):
        self.status = "checking"
        self.last_error = ""
        self.round_items = 0
        self.round_matches = 0

        exc: Exception | None = None
        try:
            # 浏览器失效则抛错，由外层负责重启
            await self._ensure_browser(monitor)

            # 登录检查（API 方式，不刷新页面）
            if not await monitor.check_login_status():
                msg = "未检测到登录状态，请先运行 python run.py --login 扫码登录"
                self.last_error = msg
                self.status = "error"
                logger.error(msg)
                self.db.log_check("*", "error", 0, 0, msg)
                return

            products = self.db.get_products(enabled_only=True)
            if not products:
                seeded = seed_products_from_config(self.db)
                products = self.db.get_products(enabled_only=True)
                if seeded:
                    logger.info(f"已从 config.py 同步 {seeded} 个监控商品")

            for product in products:
                keyword = product["keyword"]
                self.current_keyword = keyword
                try:
                    await self._check_keyword(monitor, product)
                except Exception as e:
                    if _is_browser_error(e):
                        raise  # 浏览器问题，交给外层重启
                    logger.error(f"[{keyword}] 检查失败: {e}")
                    self.db.log_check(keyword, "error", 0, 0, str(e))
                await asyncio.sleep(2)  # 关键词之间间隔，降低请求频率
        except Exception as e:
            exc = e
            raise
        finally:
            self.current_keyword = ""
            self.last_check_at = time.time()
            if exc is None:
                self.status = "running"

    async def _check_keyword(self, monitor: GoofishMonitor, product):
        keyword = product["keyword"]
        max_price = product["max_price"]
        min_price = product["min_price"] or 0
        exclude_keywords = parse_exclude_keywords(product["exclude_keywords"] or "")
        must_include = parse_exclude_keywords(product["must_include"] or "")

        items = await monitor.search(
            keyword,
            max_price=max_price,       # 用于万元缩写修正校正
            min_price=min_price,
            must_include=must_include,
        )
        if not items:
            logger.warning(f"[{keyword}] 未获取到任何商品")
            self.db.log_check(keyword, "ok", 0, 0, "未获取到商品")
            return

        # ---- 第 1 遍：基于"核心商品"计算参考价格 ----
        # 核心商品两步筛选：
        #   ① must_include 必含词（区分同配置/正品，排除配件与无关品类）
        #   ② IQR 剔除统计极端 outliers（极低价引流标价、极高配机型），仅保留主流价格带
        # 这样无论用户 min/max 设得多窄，过滤后均价都能代表样本里的主流价位。
        samples_by_config = []
        for i in items:
            price = i.get("price")
            if not price or price <= 0:
                continue
            title = i.get("title", "") or ""
            if any(kw and kw not in title for kw in must_include):
                continue
            samples_by_config.append(price)

        core_prices = sorted(samples_by_config)
        core_prices = _iqr_trim(core_prices)
        core_count = len(core_prices)

        # 参考中位价：优先用核心商品，其次退回全部商品
        median_price = core_prices[len(core_prices) // 2] if core_prices else 0.0

        # ---- 记录价格历史（原始均值 + 过滤后核心指标） ----
        all_prices = sorted(i["price"] for i in items if i.get("price"))
        if all_prices:
            self.db.record_price_history(
                keyword=keyword,
                median_price=core_prices[len(core_prices) // 2] if core_prices
                    else all_prices[len(all_prices) // 2],
                avg_price=round(sum(all_prices) / len(all_prices), 2),
                filtered_avg=round(sum(core_prices) / len(core_prices), 2) if core_prices else None,
                core_count=core_count,
                min_price=all_prices[0],
                max_price=all_prices[-1],
                item_count=len(all_prices),
            )

        # 写入商品池 + 评估
        matches = []
        filtered = []
        price_drop_notices = []
        for item in items:
            existing = self.db.get_item(item["item_id"])
            is_new = existing is None

            if is_new:
                # 卖家信用 + 价格区间 + 排除词 + 必须包含 + 引流文案 综合评估
                verdict = evaluate_item(
                    item,
                    max_price=max_price,
                    min_price=min_price,
                    exclude_keywords=exclude_keywords,
                    must_include=must_include,
                    min_seller_credit=MIN_SELLER_CREDIT,
                    median_price=median_price,
                    scam_rules=SCAM_RULES,
                )
                # 警告标记（如价格异常/夸张话术）随商品入库，推送时展示
                item["risk_flags"] = "；".join(verdict["warn"])
                if verdict["pass"]:
                    matches.append(item)
                else:
                    filtered.append((item, verdict["hard"]))
            else:
                # 已有商品保留原始风险标记，避免被覆盖
                item["risk_flags"] = existing["risk_flags"] or ""

            result = self.db.upsert_item(item, keyword)
            if result["price_dropped"]:
                price_drop_notices.append(item)

        # 推送降价提醒
        for item in price_drop_notices:
            row = self.db.get_item(item["item_id"])
            if row:
                # 查询降价记录获取原价
                changes = self.db._query(
                    "SELECT old_price FROM item_price_changes WHERE item_id=? ORDER BY id DESC LIMIT 1",
                    (item["item_id"],),
                )
                old_price = changes[0]["old_price"] if changes else None
                if old_price is not None:
                    await self._notify_drop(item, keyword, old_price)

        # 推送新匹配商品
        notified_count = 0
        for item in matches:
            self.db.mark_notified(item["item_id"])
            if await self._notify_match(item, keyword):
                notified_count += 1

        self.round_items += len(items)
        self.round_matches += len(matches)

        # 统计被过滤的商品数量与原因（日志中可见）
        filtered_detail = ""
        if filtered:
            reason_count: dict[str, int] = {}
            for _, reasons in filtered:
                for r in reasons:
                    reason_count[r] = reason_count.get(r, 0) + 1
            top = sorted(reason_count.items(), key=lambda x: -x[1])[:3]
            filtered_detail = "，过滤 " + "、".join(f"{r}×{c}" for r, c in top)

        msg = f"新增 {len(matches)} 条匹配，{len(price_drop_notices)} 条降价{filtered_detail}"
        self.db.log_check(keyword, "ok", len(items), len(matches), msg)
        logger.info(f"[{keyword}] 共 {len(items)} 条，匹配 {len(matches)} 条，降价 {len(price_drop_notices)} 条{filtered_detail}")

    # ─────────────────────────────────────
    #  通知
    # ─────────────────────────────────────

    async def _notify_match(self, item: dict, keyword: str) -> bool:
        title = f"闲鱼捡漏: {item['title'][:30]}"
        price_str = _fmt_price(item["price"])
        content = (
            f"监控词: {keyword}\n"
            f"价格: {price_str} 💰\n"
            f"标题: {item['title']}\n"
        )
        if item.get("seller_credit"):
            content += f"卖家信用: {item['seller_credit']}\n"
        if item.get("location"):
            content += f"地区: {item['location']}\n"
        if item.get("risk_flags"):
            content += f"⚠️ 注意: {item['risk_flags']}\n"
        url = item.get("url", "")
        ok = self.notifier.send(title, content, url)
        self.db.log_notification(
            item["item_id"], keyword, item["title"], item["price"], url,
            "推送" if ok else "控制台",
        )
        return ok

    async def _notify_drop(self, item: dict, keyword: str, old_price: float) -> bool:
        title = f"📉 降价提醒: {item['title'][:25]}"
        content = (
            f"监控词: {keyword}\n"
            f"原价: {_fmt_price(old_price)} → 现价: {_fmt_price(item['price'])} 💰\n"
            f"标题: {item['title']}\n"
        )
        url = item.get("url", "")
        ok = self.notifier.send(title, content, url)
        self.db.log_notification(
            item["item_id"], keyword, f"[降价] {item['title']}", item["price"], url,
            "推送" if ok else "控制台",
        )
        return ok


def _fmt_price(price: float) -> str:
    return f"¥{price:.2f}".rstrip("0").rstrip(".")


def _iqr_trim(prices: list[float]) -> list[float]:
    """
    用 IQR（四分位距）剔除统计极端值，返回主流价格带样本。
    保留 [Q1 - 1.5*IQR, Q3 + 1.5*IQR] 之内的价格，
    滤掉极低价的引流标价与极高配机型的离群值，使均值/中位数有参考意义。
    样本过少（<5）时不做剔除，避免误删。
    """
    n = len(prices)
    if n < 5:
        return prices
    q1 = prices[n // 4]
    q3 = prices[3 * n // 4]
    iqr = q3 - q1
    if iqr <= 0:
        return prices
    lo = q1 - 1.5 * iqr
    hi = q3 + 1.5 * iqr
    return [p for p in prices if lo <= p <= hi]
