"""
闲鱼监控仪表盘 - Web 界面入口
================================
用法：
  python app.py               # 启动仪表盘（自动打开浏览器）
  python app.py --no-browser  # 启动但不自动打开浏览器
  python app.py --port 8080   # 指定端口
================================
浏览器访问 http://127.0.0.1:5000 即可使用仪表盘。
"""

import argparse
import ctypes
import logging
import logging.handlers
import hmac
import os
import re
import sys
import threading
import time
import webbrowser
from datetime import datetime

# Windows 控制台编码修复
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from flask import Flask, jsonify, render_template, request

from config import DASHBOARD_TOKEN, MONITOR_SETTINGS
from database import Database, resolve_db_path
from monitor_service import MonitorService, build_monitor_settings, seed_products_from_config
from notifier import BarkNotifier, NotifierManager

_LOG_FMT = "%(asctime)s [%(levelname)s] %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

# 项目根目录：日志/PID 等默认路径基于此，服务启动（工作目录非项目根）时也能写到正确位置
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── 价格分布直方图参数（C5）──
DIST_IQR_MULTIPLIER = 1.5       # IQR 离群值剔除系数，与 _iqr_trim 保持一致
DIST_MIN_CORE_SAMPLES = 3       # 核心样本过少时退回全量价格，避免单桶失真
DIST_BINS = 12                  # 直方图分桶数


def _setup_logging(log_file: str = ""):
    """配置日志：控制台 + 轮转文件（5MB x3）。默认写入项目根 dashboard.log。"""
    if not log_file:
        log_file = os.path.join(BASE_DIR, "dashboard.log")
    formatter = logging.Formatter(_LOG_FMT, datefmt=_LOG_DATEFMT)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        from logging.handlers import RotatingFileHandler
        fh = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        fh.setFormatter(formatter)
        handlers.append(fh)
    except Exception:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(formatter)
        handlers.append(fh)
    logging.basicConfig(level=logging.INFO, format=_LOG_FMT, datefmt=_LOG_DATEFMT, handlers=handlers, force=True)


_setup_logging()
logger = logging.getLogger("dashboard")

DB_PATH = resolve_db_path(MONITOR_SETTINGS.get("data_dir", "./data"))
LEGACY_DB_PATH = os.path.join(BASE_DIR, "monitor.db")
if os.path.exists(LEGACY_DB_PATH) and not os.path.exists(DB_PATH):
    try:
        import sqlite3 as _sq
        _src = _sq.connect(LEGACY_DB_PATH)
        _dst = _sq.connect(DB_PATH)
        _src.backup(_dst)
        _dst.close()
        _src.close()
        os.remove(LEGACY_DB_PATH)
        logger.info(f"已迁移旧数据库 monitor.db → {DB_PATH}")
    except Exception as _e:
        logger.warning(f"旧数据库迁移失败（忽略）: {_e}")
db = Database(DB_PATH)
db.seed_bark_from_config()
notifier = NotifierManager(db)
service = MonitorService(db, notifier)

APP_STARTED_AT = time.time()

app = Flask(__name__, static_folder="static", template_folder="templates")


@app.before_request
def require_dashboard_token():
    if not request.path.startswith("/api/") or request.path == "/api/healthz":
        return None
    if not DASHBOARD_TOKEN:
        if request.remote_addr not in {"127.0.0.1", "::1"}:
            return jsonify({"ok": False, "error": "dashboard token is required for remote access"}), 401
        return None
    supplied = request.headers.get("X-Auth-Token", "")
    if not supplied:
        supplied = request.headers.get("Authorization", "")
        if supplied.lower().startswith("bearer "):
            supplied = supplied[7:].strip()
    if not hmac.compare_digest(supplied, DASHBOARD_TOKEN):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return None


# 本地开发环境禁用静态文件缓存，避免浏览器加载旧版 JS/CSS 导致接口不匹配
@app.after_request
def no_cache(resp):
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


# ─────────────────────────────────────────────
#  页面
# ─────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ─────────────────────────────────────────────
#  状态与统计
# ─────────────────────────────────────────────

@app.route("/api/status")
def api_status():
    status = {
        "monitor_status": service.status,
        "current_keyword": service.current_keyword,
        "last_check_at": _ts(service.last_check_at),
        "next_check_at": _ts(service.next_check_at),
        "last_error": service.last_error,
        "round_items": service.round_items,
        "round_matches": service.round_matches,
        "interval_minutes": db.get_setting(
            "interval_minutes", str(MONITOR_SETTINGS.get("interval_minutes", 30))),
        "headless": build_monitor_settings(db).get("headless", False),
        "login_ok": service.login_ok,
    }
    return jsonify(status)


@app.route("/api/stats")
def api_stats():
    return jsonify(db.get_stats())


@app.route("/api/healthz")
def api_healthz():
    """健康探针：Uptime Kuma / 系统检查用."""
    rss_mb = None
    try:
        import psutil  # 可选
        rss_mb = round(psutil.Process().memory_info().rss / 1024 / 1024, 1)
    except Exception:
        pass
    db_mb = None
    db_ok = True
    try:
        db_mb = round(os.path.getsize(DB_PATH) / 1024 / 1024, 2) if os.path.exists(DB_PATH) else None
        db._query("SELECT 1")
    except Exception:
        db_ok = False
    healthy = db_ok and service.status in {"starting", "running", "checking"}
    return jsonify({
        "ok": healthy,
        "db_ok": db_ok,
        "status": service.status,
        "uptime_seconds": int(time.time() - APP_STARTED_AT),
        "uptime_human": _ts(APP_STARTED_AT),
        "last_check_at": _ts(service.last_check_at),
        "next_check_at": _ts(service.next_check_at),
        "last_error": service.last_error,
        "rss_mb": rss_mb,
        "db_mb": db_mb,
        "monitored_products": db.count_products(),
        "total_items": db.get_stats().get("total_items", 0),
    })


# ─────────────────────────────────────────────
#  监控商品 CRUD
# ─────────────────────────────────────────────

@app.route("/api/products")
def api_products():
    products = []
    for row in db.get_products():
        products.append({
            "id": row["id"],
            "keyword": row["keyword"],
            "max_price": row["max_price"],
            "min_price": row["min_price"],
            "exclude_keywords": row["exclude_keywords"],
            "must_include": row["must_include"] or "",
            "enabled": bool(row["enabled"]),
            "created_at": row["created_at"],
        })
    return jsonify(products)


@app.route("/api/products", methods=["POST"])
def api_add_product():
    payload, err = _validate_price_pair(request.get_json(silent=True) or {}, require_keyword=True)
    if err:
        return jsonify(err[0]), err[1]
    pid = db.add_product(payload["keyword"], payload["max_price"], payload["min_price"],
                         payload["exclude_keywords"], 1, payload["must_include"])
    return jsonify({"ok": True, "id": pid})


def _validate_price_pair(data, require_keyword: bool):
    """校验商品表单（POST/PUT 共用），返回 (payload, error_response)。"""
    keyword = (data.get("keyword") or "").strip()
    if require_keyword and not keyword:
        return None, ({"ok": False, "error": "关键词不能为空"}, 400)
    try:
        max_price = float(data.get("max_price", 0))
        min_price = float(data.get("min_price", 0))
    except (TypeError, ValueError):
        return None, ({"ok": False, "error": "价格格式错误"}, 400)
    if max_price <= 0:
        return None, ({"ok": False, "error": "最高价格必须大于 0"}, 400)
    if min_price < 0 or min_price > max_price:
        return None, ({"ok": False, "error": "最低价格必须在 0 到最高价格之间"}, 400)
    return {
        "keyword": keyword,
        "max_price": max_price,
        "min_price": min_price,
        "exclude_keywords": data.get("exclude_keywords", "") or "",
        "must_include": data.get("must_include", "") or "",
    }, None


@app.route("/api/products/<int:pid>", methods=["PUT"])
def api_update_product(pid):
    payload, err = _validate_price_pair(request.get_json(silent=True) or {}, require_keyword=True)
    if err:
        return jsonify(err[0]), err[1]
    db.update_product(pid, payload["keyword"], payload["max_price"], payload["min_price"],
                      payload["exclude_keywords"], payload["must_include"])
    return jsonify({"ok": True})


@app.route("/api/products/<int:pid>", methods=["DELETE"])
def api_delete_product(pid):
    db.delete_product(pid)
    return jsonify({"ok": True})


@app.route("/api/products/<int:pid>/toggle", methods=["POST"])
def api_toggle_product(pid):
    enabled = db.toggle_product(pid)
    return jsonify({"ok": True, "enabled": enabled})


# ─────────────────────────────────────────────
#  市场分析
# ─────────────────────────────────────────────

@app.route("/api/analysis")
def api_analysis():
    keyword = request.args.get("keyword", "")
    if not keyword:
        keywords = db.get_keywords() or [p["keyword"] for p in db.get_products()]
        keyword = keywords[0] if keywords else ""
        if not keyword:
            return jsonify({"ok": False, "error": "暂无监控商品"})

    def _parse_float(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    price_min = _parse_float(request.args.get("price_min"))
    price_max = _parse_float(request.args.get("price_max"))

    if keyword == "all":
        items = db.get_latest_items(limit=500, price_min=price_min, price_max=price_max)
    else:
        items = db.get_latest_items(keyword=keyword, limit=500, price_min=price_min, price_max=price_max)

    data = {
        "ok": True,
        "keyword": keyword,
        "items": [_item_json(r) for r in items],
        "distribution": _calc_distribution([r["price"] for r in items]),
        "trend": _calc_trend(db, keyword if keyword != "all" else ""),
    }
    return jsonify(data)


@app.route("/api/price-changes")
def api_price_changes():
    rows = db.get_recent_price_changes(limit=100)
    return jsonify([dict(r) for r in rows])


# ─────────────────────────────────────────────
#  通知记录
# ─────────────────────────────────────────────

@app.route("/api/notifications")
def api_notifications():
    rows = db.get_notifications(limit=200)
    return jsonify([dict(r) for r in rows])


@app.route("/api/notifications/clear", methods=["POST"])
def api_clear_notifications():
    """清空所有通知记录（不影响已通知标记，已通知过的不重复推送）。"""
    n = db.clear_notifications()
    return jsonify({"ok": True, "deleted": n})


@app.route("/api/notifications/clear-before", methods=["POST"])
def api_clear_notifications_before():
    """清除某时间点之前的所有通知记录（body: {"before": "YYYY-MM-DD HH:MM:SS"}）。"""
    before = (request.get_json(silent=True) or {}).get("before", "").strip()
    if not before:
        return jsonify({"ok": False, "error": "缺少 before 时间参数"}), 400
    n = db.clear_notifications_before(before)
    return jsonify({"ok": True, "deleted": n, "before": before})


@app.route("/api/checks")
def api_checks():
    rows = db.get_checks(limit=100)
    return jsonify([dict(r) for r in rows])


@app.route("/api/test-notify", methods=["POST"])
def api_test_notify():
    ok = notifier.send(
        "闲鱼监控 - 测试通知",
        f"这是一条测试推送，发送时间 {datetime.now().strftime('%H:%M:%S')}",
        "",
    )
    return jsonify({"ok": bool(ok), "channels": ok})


# ─────────────────────────────────────────────
#  设置与运行控制
# ─────────────────────────────────────────────

@app.route("/api/onboarding/status")
def api_onboarding_status():
    """新用户引导状态：首次使用（未配通知/无商品）时前端弹出引导。"""
    from config import PUSHPLUS_CONFIG, SMTP_CONFIG, TELEGRAM_CONFIG

    bark_ok = any(r["enabled"] and r["bark_key"] for r in db.get_bark_targets())
    smtp_db = (db.get_channel_config().get("smtp") or {})
    smtp_ok = bool(smtp_db.get("enabled") and smtp_db.get("host") and smtp_db.get("user")
                   and smtp_db.get("password") and smtp_db.get("to"))
    if not smtp_ok:
        smtp_ok = bool(SMTP_CONFIG.get("enabled") and SMTP_CONFIG.get("host")
                       and SMTP_CONFIG.get("user") and SMTP_CONFIG.get("password")
                       and SMTP_CONFIG.get("to"))
    pushplus_ok = bool(PUSHPLUS_CONFIG.get("enabled") and PUSHPLUS_CONFIG.get("token"))
    tg_ok = bool(TELEGRAM_CONFIG.get("enabled") and TELEGRAM_CONFIG.get("bot_token")
                 and TELEGRAM_CONFIG.get("chat_id"))
    return jsonify({
        "need_notify": not (bark_ok or smtp_ok or pushplus_ok or tg_ok),
        "need_product": db.count_products() == 0,
    })


@app.route("/api/channel-config", methods=["GET"])
def api_get_channel_config():
    """读取通知渠道配置（密码脱敏）。"""
    from config import SMTP_CONFIG

    smtp = dict(SMTP_CONFIG)
    smtp.update((db.get_channel_config().get("smtp") or {}))
    return jsonify({"smtp": {
        "enabled": bool(smtp.get("enabled")),
        "host": smtp.get("host", ""),
        "port": smtp.get("port", 465),
        "user": smtp.get("user", ""),
        "password_set": bool(smtp.get("password")),
        "to": smtp.get("to", ""),
    }})


@app.route("/api/channel-config", methods=["POST"])
def api_set_channel_config():
    """保存通知渠道配置（SMTP）。只接收修改的字段，密码留空表示保持原值。"""
    data = request.get_json(silent=True) or {}
    smtp_in = data.get("smtp") or {}
    host = (smtp_in.get("host") or "").strip()
    user = (smtp_in.get("user") or "").strip()
    to = (smtp_in.get("to") or "").strip()
    if host:
        if not user or not to:
            return jsonify({"ok": False, "error": "SMTP 需填写邮箱账号与收件人"}), 400
        old = db.get_channel_config().get("smtp") or {}
        password = (smtp_in.get("password") or "").strip() or old.get("password", "")
        if not password:
            return jsonify({"ok": False, "error": "SMTP 需填写授权码（密码）"}), 400
        try:
            port = int(smtp_in.get("port") or old.get("port", 465))
        except (TypeError, ValueError):
            port = 465
        cfg = db.get_channel_config()
        cfg["smtp"] = {
            "enabled": 1, "host": host, "port": port,
            "user": user, "password": password, "to": to,
        }
        db.set_channel_config(cfg)
        return jsonify({"ok": True})
    # host 为空 = 清除 SMTP 配置
    cfg = db.get_channel_config()
    cfg["smtp"] = {"enabled": 0, "host": "", "port": 465, "user": "", "password": "", "to": ""}
    db.set_channel_config(cfg)
    return jsonify({"ok": True})


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    from config import BARK_CONFIG, PUSHPLUS_CONFIG, SMTP_CONFIG, TELEGRAM_CONFIG
    settings = {
        "interval_minutes": db.get_setting("interval_minutes", str(MONITOR_SETTINGS.get("interval_minutes", 30))),
        "headless": db.get_setting("headless", str(MONITOR_SETTINGS.get("headless", False))),
        "data_dir": MONITOR_SETTINGS.get("data_dir", "./data"),
        "user_data_dir": MONITOR_SETTINGS.get("user_data_dir", "./browser_profile"),
        "channels": {
            "bark": bool(BARK_CONFIG.get("enabled") and BARK_CONFIG.get("key")),
            "pushplus": bool(PUSHPLUS_CONFIG.get("enabled") and PUSHPLUS_CONFIG.get("token")),
            "smtp": bool(SMTP_CONFIG.get("enabled") and SMTP_CONFIG.get("host")
                         and SMTP_CONFIG.get("user") and SMTP_CONFIG.get("password")
                         and SMTP_CONFIG.get("to")),
            "telegram": bool(TELEGRAM_CONFIG.get("enabled") and TELEGRAM_CONFIG.get("bot_token")
                             and TELEGRAM_CONFIG.get("chat_id")),
        },
    }
    return jsonify(settings)


@app.route("/api/settings", methods=["POST"])
def api_set_settings():
    data = request.get_json(silent=True) or {}
    if "interval_minutes" in data:
        try:
            val = max(5, int(data["interval_minutes"]))
            db.set_setting("interval_minutes", str(val))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "间隔时间格式错误"}), 400
    if "headless" in data:
        db.set_setting("headless", "true" if data["headless"] else "false")
    return jsonify({"ok": True})


@app.route("/api/control", methods=["POST"])
def api_control():
    action = (request.get_json(silent=True) or {}).get("action", "")
    if action == "start":
        service.start()
    elif action == "stop":
        service.stop()
    elif action == "check_now":
        service.trigger_check()
    else:
        return jsonify({"ok": False, "error": "未知操作"}), 400
    return jsonify({"ok": True})


@app.route("/api/export")
def api_export():
    """导出商品数据为 CSV。"""
    import csv
    import io
    from flask import Response

    def _csv_safe(value) -> str:
        """防止 CSV 公式注入：以 = + - @ 等开头的单元格加单引号前缀，避免 Excel 执行公式。"""
        s = str(value)
        if s.startswith(("=", "+", "-", "@", "\t", "\r")):
            return "'" + s
        return s

    keyword = request.args.get("keyword", "")
    items = db.get_latest_items(keyword=keyword or None, limit=5000)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["item_id", "keyword", "title", "price", "url", "location",
                     "status", "seller_credit", "risk_flags", "notified",
                     "first_seen", "last_seen"])
    for r in items:
        writer.writerow([_csv_safe(r["item_id"]), _csv_safe(r["keyword"]), _csv_safe(r["title"]),
                         r["price"], _csv_safe(r["url"]), _csv_safe(r["location"]),
                         _csv_safe(r["status"]), _csv_safe(r["seller_credit"] or ""),
                         _csv_safe(r["risk_flags"] or ""),
                         1 if r["notified"] else 0, r["first_seen"], r["last_seen"]])

    safe_keyword = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_]+", "_", keyword or "all").strip("_")[:60] or "all"
    filename = f"xianyu_items_{safe_keyword}.csv"
    return Response(
        "\ufeff" + output.getvalue(),  # BOM 让 Excel 正确识别 UTF-8
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/api/bark-targets", methods=["GET"])
def api_bark_targets():
    rows = db.get_bark_targets()
    return jsonify([{
        "id": r["id"],
        "label": r["label"] or "",
        "server": r["server"],
        "bark_key_masked": (r["bark_key"][:4] + "****" + r["bark_key"][-2:]) if len(r["bark_key"]) > 6 else "****",
        "enabled": bool(r["enabled"]),
        "created_at": r["created_at"],
    } for r in rows])


@app.route("/api/bark-targets", methods=["POST"])
def api_add_bark_target():
    data = request.get_json(silent=True) or {}
    label = (data.get("label") or "").strip()
    server = (data.get("server") or "https://api.day.app").strip().rstrip("/") or "https://api.day.app"
    bark_key = (data.get("bark_key") or "").strip()
    if not bark_key:
        return jsonify({"ok": False, "error": "Bark Key 不能为空"}), 400
    # 基本校验：Bark Key 应该是字母数字混合字符串，长度至少 10
    if len(bark_key) < 10:
        return jsonify({"ok": False, "error": "Bark Key 长度过短，请检查"}), 400
    tid = db.add_bark_target(label, server, bark_key, 1)
    return jsonify({"ok": True, "id": tid})


@app.route("/api/bark-targets/<int:tid>", methods=["PUT"])
def api_update_bark_target(tid):
    row = db.get_bark_target(tid)
    if not row:
        return jsonify({"ok": False, "error": "未找到该推送目标"}), 404
    data = request.get_json(silent=True) or {}
    label = (data.get("label") or "").strip()
    server = (data.get("server") or row["server"]).strip().rstrip("/") or "https://api.day.app"
    bark_key = (data.get("bark_key") or "").strip() or row["bark_key"]
    if not bark_key:
        return jsonify({"ok": False, "error": "Bark Key 不能为空"}), 400
    db.update_bark_target(tid, label, server, bark_key)
    return jsonify({"ok": True})


@app.route("/api/bark-targets/<int:tid>", methods=["DELETE"])
def api_delete_bark_target(tid):
    row = db.get_bark_target(tid)
    if not row:
        return jsonify({"ok": False, "error": "未找到该推送目标"}), 404
    db.delete_bark_target(tid)
    return jsonify({"ok": True})


@app.route("/api/bark-targets/<int:tid>/toggle", methods=["POST"])
def api_toggle_bark_target(tid):
    enabled = db.toggle_bark_target(tid)
    return jsonify({"ok": True, "enabled": enabled})


@app.route("/api/bark-targets/<int:tid>/test", methods=["POST"])
def api_test_bark_target(tid):
    row = db.get_bark_target(tid)
    if not row:
        return jsonify({"ok": False, "error": "未找到该推送目标"}), 404
    bn = BarkNotifier(server=row["server"], key=row["bark_key"], label=row["label"])
    ok = bn.send("闲鱼监控 - Bark 测试", f"这是一条测试推送 ({row['label'] or row['bark_key'][:6]})", "")
    return jsonify({"ok": ok, "label": row["label"] or row["bark_key"][:6]})


@app.route("/api/retention", methods=["GET"])
def api_get_retention():
    return jsonify(db.get_retention())


@app.route("/api/retention", methods=["POST"])
def api_set_retention():
    data = request.get_json(silent=True) or {}
    try:
        updates = {}
        for k in ("items_days", "history_days", "checks_keep", "notifications_keep"):
            if k in data:
                v = int(data[k])
                if v < 1:
                    return jsonify({"ok": False, "error": f"{k} 必须 >= 1"}), 400
                updates[k] = v
        if updates:
            db.set_retention(**updates)
        return jsonify({"ok": True, "retention": db.get_retention()})
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "参数格式错误"}), 400


@app.route("/api/cleanup", methods=["POST"])
def api_cleanup():
    """手动触发保留策略清理."""
    data = request.get_json(silent=True) or {}
    vacuum = bool(data.get("vacuum"))
    r = db.get_retention()
    stats = db.cleanup_expired(vacuum=vacuum, **r)
    return jsonify({"ok": True, "stats": stats, "retention": r})


# ─────────────────────────────────────────────
#  商品归档 / 忽略
# ─────────────────────────────────────────────

@app.route("/api/items/<item_id>/disposition", methods=["POST"])
def api_set_item_disposition(item_id: str):
    """设置商品分组：track=价格监测 / ignore=忽略 / restore=恢复待处理。"""
    action = (request.get_json(silent=True) or {}).get("action", "")
    mapping = {"track": "tracking", "ignore": "ignored", "restore": "new"}
    disposition = mapping.get(action)
    if not disposition:
        return jsonify({"ok": False, "error": "无效动作（track/ignore/restore）"}), 400
    row = db.get_item(item_id)
    if not row:
        return jsonify({"ok": False, "error": "商品不存在"}), 404
    db.set_item_disposition(item_id, disposition)
    return jsonify({"ok": True, "item_id": item_id, "disposition": disposition})


@app.route("/api/clear-items", methods=["POST"])
def api_clear_items():
    db.clear_items()
    return jsonify({"ok": True})


# ─────────────────────────────────────────────
#  工具函数
# ─────────────────────────────────────────────

def _ts(epoch: float | None):
    if not epoch:
        return None
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


def _item_json(row) -> dict:
    return {
        "item_id": row["item_id"],
        "keyword": row["keyword"],
        "title": row["title"],
        "price": row["price"],
        "url": row["url"],
        "image": row["image"] or "",
        "location": row["location"] or "",
        "status": row["status"] or "",
        "seller_credit": row["seller_credit"] or "",
        "risk_flags": row["risk_flags"] or "",
        "notified": bool(row["notified"]),
        "disposition": row["disposition"] if "disposition" in row.keys() else "new",
        "first_seen": row["first_seen"],
        "last_seen": row["last_seen"],
    }


def _calc_distribution(prices: list[float], bins: int = DIST_BINS) -> list[dict]:
    """
    计算价格分布（直方图数据）。
    使用更细的步长以展示中高价商品的细微差价：
      - 先截掉可能污染分布的极端值（IQR 方法）
      - 步长依据价格量级选择（万元级商品用几百元一档，而非粗分）
    """
    if not prices:
        return []
    prices = sorted(p for p in prices if p and p > 0)
    if not prices:
        return []
    low, high = prices[0], prices[-1]
    if high <= low:
        return [{"from": low, "to": high, "count": len(prices)}]

    # 用 IQR 剔除极端 outliers，聚焦主流价格带，避免单根柱子吞掉万元级差价
    q1 = prices[len(prices) // 4]
    q3 = prices[3 * len(prices) // 4]
    iqr = q3 - q1
    core_low = max(low, q1 - DIST_IQR_MULTIPLIER * iqr)
    core_high = min(high, q3 + DIST_IQR_MULTIPLIER * iqr)
    core = [p for p in prices if core_low <= p <= core_high]
    if len(core) < DIST_MIN_CORE_SAMPLES:  # 数据太少就退回全量
        core = prices
    if not core:
        core = prices

    c_low, c_high = core[0], core[-1]
    span = c_high - c_low
    # 依据价格量级选择合理的细化步长：万元级用几百，千元级用几十
    def nice_step(raw: float) -> float:
        if raw <= 0:
            return 10
        # 找到不小于 raw 的"好看"步长（1/2/2.5/5 × 10^n 的倍数）
        base = 10 ** (len(str(int(raw))) - 1)
        for factor in (1, 2, 2.5, 5, 10):
            if base * factor >= raw:
                return base * factor
        return base * 10

    step = nice_step(span / bins)

    # 分桶
    edges = []
    cur = (c_low // step) * step
    while cur < c_high + step:
        edges.append(cur)
        cur += step

    result = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        count = sum(1 for p in core if lo <= p < hi)
        if count:
            result.append({"from": lo, "to": hi, "count": count})
    return result


def _calc_trend(db: Database, keyword: str, limit: int = 100) -> list[dict]:
    """计算价格走势（按检查时间聚合，epoch 排序）。"""
    if keyword:
        rows = db.get_price_history(keyword, limit=limit)
    else:
        rows = db.get_all_price_history(limit=limit)
    points = []
    for r in reversed(rows):
        points.append({
            "time": r["check_time"],
            "epoch": r["epoch"] if "epoch" in r.keys() else None,
            "keyword": r["keyword"],
            "median": r["median_price"],
            "avg": r["avg_price"],
            "filtered_avg": r["filtered_avg"],
            "core_count": r["core_count"],
            "min": r["min_price"],
            "max": r["max_price"],
            "count": r["item_count"],
        })
    return points


def _write_pid():
    """写入进程 PID 文件，便于管理。"""
    try:
        with open(os.path.join(BASE_DIR, "dashboard.pid"), "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass


def _remove_pid():
    try:
        os.remove(os.path.join(BASE_DIR, "dashboard.pid"))
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    """检查 PID 对应的进程是否存活。"""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _check_single_instance() -> bool:
    """
    单实例保护：若已有仪表盘实例在运行则返回 False。
    防止重复启动导致多个进程抢占端口、页面缓存错乱。
    """
    pid_file = os.path.join(BASE_DIR, "dashboard.pid")
    if os.path.exists(pid_file):
        try:
            with open(pid_file) as f:
                old_pid = int(f.read().strip())
            if _pid_alive(old_pid):
                print(f"⚠️  仪表盘已在运行 (PID {old_pid})，请勿重复启动")
                print("   如需重启：先停止旧进程，再运行 python app.py")
                return False
        except (ValueError, OSError):
            pass
        _remove_pid()
    return True


def main():
    parser = argparse.ArgumentParser(description="闲鱼监控仪表盘")
    # F-07: host/port 支持环境变量覆盖（HOST/PORT），命令行参数优先
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 5000)))
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"),
                        help="监听地址，容器部署用 0.0.0.0")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    if not _check_single_instance():
        return

    # 首次运行：从 config.py 同步监控商品
    seeded = seed_products_from_config(db)
    if seeded:
        logger.info(f"已从 config.py 同步 {seeded} 个监控商品")

    # 启动监控服务（后台线程）
    service.start()

    url = f"http://127.0.0.1:{args.port}"
    print("=" * 55)
    print("  闲鱼监控仪表盘已启动")
    print(f"  地址: {url}")
    print("  监控状态: 运行中 (浏览器窗口请保持打开，可最小化)")
    print("  按 Ctrl+C 停止")
    print("=" * 55)

    _write_pid()

    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    try:
        app.run(host=args.host, port=args.port, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        print("\n正在停止监控服务...")
    finally:
        service.stop(timeout=10)
        db.close()
        _remove_pid()


if __name__ == "__main__":
    main()
