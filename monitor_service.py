"""
后台监控服务。
在独立线程中运行闲鱼监控循环（自带 asyncio 事件循环），
将搜索结果写入数据库，并触发通知。仪表盘与监控服务解耦，通过数据库共享数据。
"""

import asyncio
import logging
import random
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
    from monitor import _is_browser_error as _monitor_browser_error
    return _monitor_browser_error(exc)


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
    if existing or db.get_setting("products_seeded", "") == "1":
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
    db.set_setting("products_seeded", "1")
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
        self._round_count = 0
        self._last_browser_restart_at: float | None = None
        self.login_ok = False
        self._login_failed = False
        self._zero_streak = 0

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
        self._round_count = 0
        self._last_browser_restart_at = time.time()
        self._zero_streak = 0
        self._last_auth_alert_at: float | None = None
        self._last_cleanup_at: float | None = None
        try:
            while not self._stop_event.is_set():
                try:
                    await self._check_round(monitor)
                    self._round_count += 1
                    restart_delay = 5  # 成功后重置退避
                    # B2: 定时回收（50轮 或 12小时）
                    await self._maybe_recycle_browser(monitor)
                    # B1: 每日自动清理（每天一次，03:00后首次触发）
                    await self._maybe_cleanup()
                    # B3: 连续0条告警
                    await self._check_zero_streak_alert()
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

                try:
                    interval = float(self.db.get_setting("interval_minutes", 30))
                except (TypeError, ValueError):
                    interval = 30.0
                    logger.warning("interval_minutes 无效，回退到 30 分钟")
                # ±5分钟随机抖动，降低固定间隔的行为指纹
                jitter = random.uniform(-5, 5)
                effective = max(1, interval + jitter)
                self.next_check_at = time.time() + effective * 60
                logger.info(f"本轮检查完成，约 {effective:.1f} 分钟后进行下一轮（抖动 ±5min）")

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

    # ── B2: 定时回收 ──
    async def _maybe_recycle_browser(self, monitor: GoofishMonitor):
        """50轮 或 12小时 触发主动重启，减少慢性泄漏."""
        now = time.time()
        due_round = self._round_count > 0 and self._round_count % 50 == 0
        due_time = (now - (self._last_browser_restart_at or now)) >= 12 * 3600
        if not (due_round or due_time):
            return
        reason = "50轮" if due_round else "12小时"
        logger.info(f"定时回收：已运行 {self._round_count} 轮，触发浏览器重启（{reason}）")
        async with self._browser_lock:
            ok = await self._restart_browser(monitor)
            if ok:
                self._last_browser_restart_at = now

    # ── B1: 每日自动清理 ──
    async def _maybe_cleanup(self):
        """每天凌晨后首次触发保留策略清理（避免每轮都执行）。"""
        now = time.time()
        if self._last_cleanup_at and now - self._last_cleanup_at < 20 * 3600:
            return
        # 仅在本地时间 03:00 之后触发，避免刚启动就清理
        try:
            hour = datetime.now().hour
            if self._last_cleanup_at is None and hour < 3:
                return
        except Exception:
            pass
        try:
            r = self.db.get_retention()
            stats = self.db.cleanup_expired(vacuum=False, **r)
            self._last_cleanup_at = now
            if any(stats.values()):
                logger.info(f"自动清理完成: {stats}")
        except Exception as e:
            logger.warning(f"自动清理失败: {e}")

    # ── B3: 登录过期告警 ──
    async def _check_zero_streak_alert(self):
        """
        连续3轮总商品数为0则判定登录/风控异常，推送 Bark 告警。
        同一异常 6 小时内不重复推送。
        """
        # 汇总本轮总数：round_items 是本轮累计，结束后为最终值
        if self.round_items == 0:
            self._zero_streak += 1
        else:
            self._zero_streak = 0
            return
        if self._zero_streak < 3:
            return
        now = time.time()
        if self._last_auth_alert_at and now - self._last_auth_alert_at < 6 * 3600:
            return
        self._last_auth_alert_at = now
        self.last_error = "连续3轮未获取到商品，疑似登录过期或被风控"
        self.status = "error"
        self.db.log_check("*", "auth_expired", 0, 0, self.last_error)
        try:
            self.notifier.send(
                "闲鱼监控 — 登录异常告警",
                "连续3轮未获取到商品，可能是登录过期或被风控。\n请运行 python run.py --login 重新扫码。",
                "",
            )
        except Exception:
            pass

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
            self.login_ok = await monitor.check_login_status()
            if not self.login_ok:
                msg = "未检测到登录状态，请先运行 python run.py --login 扫码登录"
                self.last_error = msg
                self.status = "error"
                self._login_failed = True
                logger.error(msg)
                self.db.log_check("*", "error", 0, 0, msg)
                return
            self._login_failed = False

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
            if exc is None and not self._login_failed:
                self.status = "running"

    async def _check_keyword(self, monitor: GoofishMonitor, product):
        keyword = product["keyword"]
        max_price = product["max_price"]
        min_price = product["min_price"] or 0
        exclude_keywords = parse_exclude_keywords(product["exclude_keywords"] or "")
        must_include = parse_exclude_keywords(product["must_include"] or "")

        items = await monitor.search(
            keyword,
            max_price=max_price,       # 用于万元缩写量级判断
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
            verdict = {"pass": False, "hard": [], "warn": []}

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
                # 老商品每次降价也必须重新过硬校验，不能沿用首次入库时的结论。
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
                # 保留已记录的风险说明；若当前重新评估发现警告则追加。
                old_flags = existing["risk_flags"] or ""
                current_flags = "；".join(verdict["warn"])
                item["risk_flags"] = "；".join(dict.fromkeys(
                    flag for flag in (old_flags + "；" + current_flags).split("；") if flag
                ))

            result = self.db.upsert_item(item, keyword)
            if result["price_dropped"] and verdict["pass"]:
                price_drop_notices.append(item) 
            elif result["price_dropped"]:
                logger.info(f"[{keyword}] 降价商品通过数据库更新，但因风险校验未通过而不推送: {item['item_id']}")
            
            # 新商品分支也需要保留 verdict，供降价判断结构保持一致。

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

        # 推送新匹配商品 — B4: 单轮>10条合并为摘要，避免 Bark/邮件 风暴
        notified_count = 0
        if len(matches) > 10:
            # 全部标记已推送
            for it in matches:
                self.db.mark_notified(it["item_id"])
            from notifier import BarkNotifier as _BN
            summary_title, summary_content = _BN.build_summary_payload(matches, keyword)
            # 复用统一发送（所有启用的渠道）；url 置空，摘要里提示看仪表盘
            ok_list = await asyncio.to_thread(self.notifier.send, summary_title, summary_content, "")
            ok = bool(ok_list)
            for it in matches:
                self.db.log_notification(it["item_id"], keyword, it["title"], it["price"], it.get("url",""), "推送(摘要)" if ok else "控制台(摘要)")
            notified_count = len(matches) if ok else 0
            logger.info(f"[{keyword}] 单轮 {len(matches)} 条匹配过多，已合并为摘要推送")
        else:
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
        ok = bool(await asyncio.to_thread(self.notifier.send, title, content, url))
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
