"""
闲鱼价格监控 - 运维入口（薄壳）
================================
常驻监控请使用 `python app.py`（唯一常驻入口，含仪表盘）。
本命令仅用于运维操作：
  python run.py --login     # 扫码登录闲鱼（首次使用，只需一次）
  python run.py --once      # 只检查一轮后退出（调试/验证用）
  python run.py --stats     # 查看历史统计信息
  python run.py --check     # 配置与依赖检查
================================
"""

import argparse
import asyncio
import logging
import os
import sys
import time

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from config import MONITOR_ITEMS, MONITOR_SETTINGS
from database import Database, resolve_db_path
from monitor import GoofishMonitor
from monitor_service import MonitorService
from notifier import NotifierManager

_LOG_FMT = "%(asctime)s [%(levelname)s] %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

# 项目根目录：日志默认路径基于此，服务启动（工作目录非项目根）时也能写到正确位置
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _setup_logging(log_file: str = ""):
    if not log_file:
        log_file = os.path.join(BASE_DIR, "monitor.log")
    formatter = logging.Formatter(_LOG_FMT, datefmt=_LOG_DATEFMT)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        from logging.handlers import RotatingFileHandler
        fh = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    except Exception:
        fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(formatter)
    handlers.append(fh)
    logging.basicConfig(level=logging.INFO, format=_LOG_FMT, datefmt=_LOG_DATEFMT, handlers=handlers, force=True)


_setup_logging()
logger = logging.getLogger("run")


def validate_config() -> bool:
    """检查配置是否有效（关键词、max_price>0、min≤max）。"""
    if not MONITOR_ITEMS:
        logger.error("config.py 中 MONITOR_ITEMS 为空，请先添加要监控的商品！")
        return False
    for i, item in enumerate(MONITOR_ITEMS):
        if not item.get("keyword"):
            logger.error(f"MONITOR_ITEMS[{i}] 缺少 keyword 字段！")
            return False
        if not item.get("max_price"):
            logger.error(f"MONITOR_ITEMS[{i}] ({item['keyword']}) 缺少 max_price 字段！")
            return False
        try:
            max_price = float(item["max_price"])
            min_price = float(item.get("min_price") or 0)
        except (TypeError, ValueError):
            logger.error(f"MONITOR_ITEMS[{i}] ({item['keyword']}) 的价格字段必须是数字！")
            return False
        if max_price <= 0:
            logger.error(f"MONITOR_ITEMS[{i}] ({item['keyword']}) 的 max_price 必须大于 0！")
            return False
        if min_price < 0 or min_price > max_price:
            logger.error(
                f"MONITOR_ITEMS[{i}] ({item['keyword']}) 的 min_price({min_price}) 必须不小于 0 且不大于 max_price({max_price})！"
            )
            return False
    return True


async def cmd_login(monitor):
    """登录模式：打开浏览器让用户扫码登录。"""
    logger.info("正在启动浏览器进行登录...")
    await monitor.start()
    # 强制重新登录：清掉历史登录态，避免已登录时二维码页面被秒关
    await monitor.clear_login_state()
    ok = await monitor.wait_for_login(timeout_minutes=10)
    await monitor.stop()
    if ok:
        print("\n✅ 登录成功！现在可以运行 python app.py 开始监控了。")
    else:
        print("\n⚠️ 登录超时，请重新运行 python run.py --login")


def _stop_running_monitor_if_any() -> None:
    """登录需要独占浏览器配置目录：若监控正在运行则自动暂停（释放 profile 锁）。

    否则两个 Chromium 共用同一 profile，登录窗口会被隐藏的监控实例接管（空白页）。
    托盘触发的重新登录由 tray.py 先行暂停，这里作为兜底（手动执行 run.py --login 时）。
    """
    import subprocess

    pid_file = os.path.join(BASE_DIR, "dashboard.pid")
    if not os.path.exists(pid_file):
        return
    try:
        with open(pid_file, encoding="utf-8") as f:
            pid = int(f.read().strip())
    except (ValueError, OSError):
        return
    if pid <= 0:
        return
    print("检测到监控正在运行，登录需独占浏览器配置，正在自动暂停监控...")
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                   capture_output=True)
    time.sleep(2)  # 等待 chromium 释放 profile 锁
    print("监控已暂停。登录完成后请重启监控（托盘图标 → 退出后重新打开，或 python app.py）")


async def main():
    parser = argparse.ArgumentParser(description="闲鱼价格监控")
    parser.add_argument("--login", action="store_true", help="扫码登录闲鱼（首次使用）")
    parser.add_argument("--once", action="store_true", help="只检查一轮后退出")
    parser.add_argument("--stats", action="store_true", help="显示 SQLite 历史统计信息")
    parser.add_argument("--check", action="store_true", help="执行一次配置与依赖检查后退出")
    args = parser.parse_args()

    # P-02/K-02 单一入口：run.py 仅作运维薄壳，无参数时不再启动常驻监控，
    # 避免与 python app.py 同时常驻导致重复检查/重复推送。
    if not (args.login or args.once or args.stats or args.check):
        parser.print_help()
        print("\n常驻监控请使用 python app.py（唯一常驻入口，含仪表盘）。")
        return

    db_path = resolve_db_path(MONITOR_SETTINGS.get("data_dir", "./data"))

    if args.check:
        if not validate_config():
            sys.exit(1)
        db = Database(db_path)
        try:
            stats = db.get_stats()
            print("配置检查通过")
            print(f"数据库: {db_path}")
            print(f"监控商品: {stats['monitored_products']}")
            print(f"Bark 目标: {len(db.get_bark_targets())}")
        finally:
            db.close()
        return

    if args.stats:
        db = Database(db_path)
        stats = db.get_stats()
        print(f"已发现商品: {stats['total_items']}")
        print(f"累计通知: {stats['total_notified']}")
        print(f"今日通知: {stats['today_notified']}")
        print(f"降价记录: {stats['total_drops']}")
        print(f"检查轮次: {stats['total_checks']}")
        print(f"监控商品: {stats['monitored_products']}")
        if stats['recent_notifications']:
            print("最近通知:")
            for item in stats['recent_notifications']:
                print(f"  [{item['time']}] ¥{item['price'] or 0:g} {item['title']} ({item['keyword']})")
        db.close()
        return

    if not validate_config():
        sys.exit(1)

    if args.login:
        # 登录需独占浏览器配置：若监控在运行先自动暂停（托盘触发时已暂停，这里兜底）
        _stop_running_monitor_if_any()
        settings = dict(MONITOR_SETTINGS)
        settings["hide_browser_window"] = False  # 登录必须显示窗口供扫码
        await cmd_login(GoofishMonitor(settings))
        return

    db = Database(db_path)
    notifier = NotifierManager(db)
    service = MonitorService(db, notifier)
    service.start()
    try:
        # --once：等待首轮完成（last_check_at 非空）后停止
        while service.last_check_at is None and service._thread and service._thread.is_alive():
            await asyncio.sleep(0.2)
    except KeyboardInterrupt:
        logger.info("收到停止信号，正在退出...")
    finally:
        service.stop(timeout=10)
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
