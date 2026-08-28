"""
多渠道通知系统。
支持：Bark (iOS) / PushPlus (微信) / SMTP (邮件) / Telegram / 控制台日志
"""

import logging
import smtplib
import time
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr

import requests

from config import (
    BARK_CONFIG,
    PUSHPLUS_CONFIG,
    SMTP_CONFIG,
    TELEGRAM_CONFIG,
)

logger = logging.getLogger(__name__)


def _safe_request(url: str, timeout: int = 10, **kwargs) -> requests.Response:
    """发起 HTTP 请求并记录异常。"""
    resp = requests.post(url, timeout=timeout, **kwargs)
    resp.raise_for_status()
    return resp


# ═══════════════════════════════════════════
#  各通知渠道实现
# ═══════════════════════════════════════════

class BarkNotifier:
    """Bark - iOS 推送通知（单条目标）。"""

    MIN_INTERVAL = 0.3  # B4: Bark 单目标间隔，避免限频

    def __init__(self, server: str = "https://api.day.app", key: str = "",
                 label: str = "", enabled: bool = True):
        # 兼容旧的 dict 配置传入方式
        if isinstance(server, dict):
            cfg = server
            server = cfg.get("server", "https://api.day.app")
            key = cfg.get("key", "")
            label = cfg.get("label", "")
            enabled = cfg.get("enabled", True)
        self.server = (server or "https://api.day.app").rstrip("/")
        self.key = (key or "").strip()
        self.label = (label or "").strip()
        self._enabled_flag = bool(enabled)
        self._last_sent_at = 0.0

    @property
    def enabled(self) -> bool:
        return self._enabled_flag and bool(self.key)

    @property
    def display_name(self) -> str:
        return self.label or self.key[:8]

    def send(self, title: str, content: str, url: str = "") -> bool:
        if not self.enabled:
            return False
        # B4: 限流
        now = time.time()
        wait = self.MIN_INTERVAL - (now - self._last_sent_at)
        if wait > 0:
            time.sleep(wait)
        try:
            api_url = (
                f"{self.server}/{self.key}/"
                f"{requests.utils.quote(title)}/{requests.utils.quote(content)}"
            )
            if url:
                api_url += f"?url={requests.utils.quote(url)}"
            resp = _safe_request(api_url)
            result = resp.json()
            ok = result.get("code") == 200
            if ok:
                self._last_sent_at = time.time()
            return ok
        except Exception as e:
            logger.error(f"[Bark:{self.display_name}] 发送失败: {e}")
            return False

    @staticmethod
    def build_summary_payload(items: list[dict], keyword: str, max_lines: int = 6) -> tuple[str, str]:
        """B4: 单轮过多时使用的摘要内容（供 MonitorService 调用）。"""
        total = len(items)
        prices = sorted(i.get("price", 0) for i in items if i.get("price"))
        pmin = min(prices) if prices else 0
        pmax = max(prices) if prices else 0
        title = f"闲鱼批量: {keyword} 发现 {total} 条新品"
        lines = [f"监控词: {keyword}", f"共 {total} 条，价格 {pmin:.0f}~{pmax:.0f}，仅展示前 {max_lines} 条："]
        for it in items[:max_lines]:
            lines.append(f"¥{it.get('price',0):.0f} {it.get('title','')[:22]}")
        lines.append("详情见仪表盘：市场分析 / 通知记录")
        return title, "\n".join(lines)


class PushPlusNotifier:
    """PushPlus - 微信推送。"""

    def __init__(self, config: dict):
        self.config = config

    @property
    def enabled(self) -> bool:
        return self.config.get("enabled", False) and self.config.get("token")

    def send(self, title: str, content: str, url: str = "") -> bool:
        if not self.enabled:
            return False
        try:
            # PushPlus 支持 HTML 内容
            html = f"<h3>{title}</h3><p>{content}</p>"
            if url:
                html += f'<p><a href="{url}">点击查看商品</a></p>'
            resp = _safe_request(
                "http://www.pushplus.plus/send",
                json={
                    "token": self.config["token"],
                    "title": title,
                    "content": html,
                },
            )
            result = resp.json()
            return result.get("code") == 200
        except Exception as e:
            logger.error(f"[PushPlus] 发送失败: {e}")
            return False


class SMTPNotifier:
    """SMTP - 邮件推送。"""

    def __init__(self, config: dict):
        self.config = config

    @property
    def enabled(self) -> bool:
        cfg = self.config
        return cfg.get("enabled", False) and all([
            cfg.get("host"), cfg.get("user"), cfg.get("password"), cfg.get("to")
        ])

    def send(self, title: str, content: str, url: str = "") -> bool:
        if not self.enabled:
            return False
        try:
            cfg = self.config
            # 构建邮件
            body = f"{content}\n\n商品链接: {url}" if url else content
            msg = MIMEText(body, "plain", "utf-8")
            msg["From"] = formataddr((str(Header("闲鱼监控", "utf-8")), cfg["user"]))
            msg["To"] = cfg["to"]
            msg["Subject"] = Header(title, "utf-8")

            if cfg.get("port") == 465:
                server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=15)
            else:
                server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=15)
                server.starttls()
            server.login(cfg["user"], cfg["password"])
            server.sendmail(cfg["user"], [cfg["to"]], msg.as_string())
            server.quit()
            return True
        except Exception as e:
            logger.error(f"[SMTP] 发送失败: {e}")
            return False


class TelegramNotifier:
    """Telegram Bot 推送。"""

    def __init__(self, config: dict):
        self.config = config

    @property
    def enabled(self) -> bool:
        cfg = self.config
        return cfg.get("enabled", False) and cfg.get("bot_token") and cfg.get("chat_id")

    def send(self, title: str, content: str, url: str = "") -> bool:
        if not self.enabled:
            return False
        try:
            text = f"<b>{title}</b>\n\n{content}"
            if url:
                text += f"\n\n<a href=\"{url}\">点击查看商品</a>"
            resp = _safe_request(
                f"https://api.telegram.org/bot{self.config['bot_token']}/sendMessage",
                json={
                    "chat_id": self.config["chat_id"],
                    "text": text,
                    "parse_mode": "HTML",
                },
            )
            return resp.json().get("ok", False)
        except Exception as e:
            logger.error(f"[Telegram] 发送失败: {e}")
            return False


class ConsoleNotifier:
    """控制台日志，方便调试（总是启用）。"""

    def send(self, title: str, content: str, url: str = "") -> bool:
        logger.info("=" * 50)
        logger.info(f"🔔 {title}")
        logger.info(content)
        if url:
            logger.info(f"🔗 {url}")
        logger.info("=" * 50)
        return True


# ═══════════════════════════════════════════
#  通知管理器
# ═══════════════════════════════════════════

class NotifierManager:
    """统一管理所有通知渠道。"""

    def __init__(self, db=None):
        self.db = db
        self._bark_targets_cache = None  # 缓存 Bark 目标列表
        self.channels = [
            ConsoleNotifier(),
            PushPlusNotifier(PUSHPLUS_CONFIG),
            SMTPNotifier(SMTP_CONFIG),
            TelegramNotifier(TELEGRAM_CONFIG),
        ]
        # Bark 从数据库动态加载，不再从 config 硬编码
        # 但保留一个回退：若数据库无 Bark 目标，且 config 有配置，则用 config
        self._legacy_bark = BarkNotifier(BARK_CONFIG) if BARK_CONFIG.get("key") else None

    def _get_bark_targets(self) -> list[BarkNotifier]:
        """获取所有启用的 Bark 目标（从数据库动态加载）。"""
        if self.db is None:
            return [self._legacy_bark] if self._legacy_bark and self._legacy_bark.enabled else []
        rows = self.db.get_bark_targets()
        targets = []
        for r in rows:
            if r["enabled"] and r["bark_key"]:
                targets.append(BarkNotifier(
                    server=r["server"],
                    key=r["bark_key"],
                    label=r["label"],
                    enabled=True,
                ))
        # 回退：若数据库无 Bark 目标，且 config 有配置，则用 config
        if not targets and self._legacy_bark and self._legacy_bark.enabled:
            targets.append(self._legacy_bark)
        return targets

    def _get_all_channels(self) -> list:
        """获取所有渠道（含 Bark 动态目标）。"""
        channels = list(self.channels)
        channels.extend(self._get_bark_targets())
        return channels

    def send(self, title: str, content: str, url: str = "") -> list[str]:
        """向所有启用渠道发送通知，返回成功渠道列表。"""
        success_channels = []
        for channel in self._get_all_channels():
            try:
                if channel.send(title, content, url):
                    name = type(channel).__name__
                    if isinstance(channel, ConsoleNotifier):
                        continue
                    if isinstance(channel, BarkNotifier):
                        name = f"Bark:{channel.display_name}"
                    success_channels.append(name)
            except Exception as e:
                logger.error(f"[{type(channel).__name__}] 发送异常: {e}")
        return success_channels

    def report_channels(self) -> str:
        """返回启用的通知渠道列表。"""
        enabled = self._get_all_channels()
        if len(enabled) <= 1:
            return "无（仅控制台日志）"
        names = []
        for c in enabled:
            if isinstance(c, ConsoleNotifier):
                continue
            if isinstance(c, BarkNotifier):
                names.append(f"Bark:{c.display_name}(已开启)")
            else:
                name = type(c).__name__.replace("Notifier", "")
                names.append(f"{name}(已开启)")
        return ", ".join(names) if names else "无（仅控制台日志）"

    def get_bark_targets(self) -> list[dict]:
        """返回所有 Bark 目标（供仪表盘展示）。"""
        if self.db is None:
            if self._legacy_bark and self._legacy_bark.enabled:
                return [{"id": 0, "label": "默认", "server": "https://api.day.app",
                         "bark_key": self._legacy_bark.key, "enabled": True}]
            return []
        rows = self.db.get_bark_targets()
        return [dict(r) for r in rows]