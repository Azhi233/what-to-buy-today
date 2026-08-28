"""
闲鱼价格监控 - 主入口
================================
用法：
  python run.py --login     # 首次使用：扫码登录闲鱼（只需一次）
  python run.py             # 正常运行监控
  python run.py --once      # 只检查一轮后退出（调试用）
  python run.py --stats     # 查看历史统计信息
================================
"""

import argparse
import asyncio
import logging
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from config import MONITOR_ITEMS
from database import Database
from monitor import GoofishMonitor
from monitor_service import MonitorService
from notifier import NotifierManager

_LOG_FMT = "%(asctime)s [%(levelname)s] %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


def _setup_logging(log_file: str = "monitor.log"):
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
    """检查配置是否有效。"""
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
    return True


async def cmd_login(monitor):
    """登录模式：打开浏览器让用户扫码登录。"""
    logger.info("正在启动浏览器进行登录...")
    await monitor.start()
    ok = await monitor.wait_for_login(timeout_minutes=10)
    await monitor.stop()
    if ok:
        print("\n✅ 登录成功！现在可以运行 python run.py 开始监控了。")
    else:
        print("\n⚠️ 登录超时，请重新运行 python run.py --login")


async def main():
    parser = argparse.ArgumentParser(description="闲鱼价格监控")
    parser.add_argument("--login", action="store_true", help="扫码登录闲鱼（首次使用）")
    parser.add_argument("--once", action="store_true", help="只检查一轮后退出")
    parser.add_argument("--stats", action="store_true", help="显示 SQLite 历史统计信息")
    args = parser.parse_args()

    if args.stats:
        db = Database("monitor.db")
        print(db.get_stats())
        db.close()
        return

    if not validate_config():
        sys.exit(1)

    if args.login:
        await cmd_login(GoofishMonitor({}))
        return

    db = Database("monitor.db")
    notifier = NotifierManager(db)
    service = MonitorService(db, notifier)
    service.start()
    try:
        if args.once:
            while service.last_check_at is None and service._thread and service._thread.is_alive():
                await asyncio.sleep(0.2)
            service.stop()
            if service._thread:
                service._thread.join(timeout=10)
        else:
            while service._thread and service._thread.is_alive():
                await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("收到停止信号，正在退出...")
    finally:
        service.stop()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
