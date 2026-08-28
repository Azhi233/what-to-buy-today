"""
闲鱼价格监控 - 主入口
================================
用法：
  python run.py --login     # 首次使用：扫码登录闲鱼（只需一次）
  python run.py             # 正常运行监控
  python run.py --once      # 只检查一轮就退出（调试用）
  python run.py --stats     # 查看历史统计信息
================================
"""

import argparse
import asyncio
import logging
import sys

# Windows 控制台默认 GBK 编码，无法输出 emoji/特殊字符，强制使用 UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from config import MONITOR_ITEMS, MONITOR_SETTINGS
from monitor import GoofishMonitor, filter_items
from notifier import NotifierManager
from storage import SeenStorage, StatsCollector

# 日志配置
_LOG_FMT = "%(asctime)s [%(levelname)s] %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


def _setup_logging(log_file: str = "monitor.log"):
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


async def check_once(monitor, notifier, storage, stats, verbose=True):
    """
    执行一轮完整的监控：
      1. 对每个关键词搜索闲鱼
      2. 过滤出价格合适的商品
      3. 对未推送过的新商品发送通知
    """
    # 检查登录状态
    if not await monitor.check_login_status():
        logger.error("❌ 未检测到登录状态！请先运行: python run.py --login 扫码登录")
        return

    for item in MONITOR_ITEMS:
        keyword = item["keyword"]
        max_price = item["max_price"]
        min_price = item.get("min_price", 0)
        exclude_keywords = item.get("exclude_keywords", [])

        try:
            items = await monitor.search(keyword)
            if not items:
                logger.warning(f"[{keyword}] 未获取到任何商品")
                continue

            matches = filter_items(items, max_price, min_price, exclude_keywords)
            logger.info(
                f"[{keyword}] 共 {len(items)} 条，价格 {min_price}-{max_price} 区间内匹配 {len(matches)} 条"
            )

            stats.record_check(len(items), len(matches))

            for product in matches:
                item_id = product["item_id"]
                if not storage.is_new(item_id):
                    continue

                storage.mark_seen(item_id)
                _notify_product(notifier, stats, product, keyword)

        except Exception as e:
            logger.error(f"[{keyword}] 监控出错: {e}")
            stats.record_error(e)

    # 每轮间隔，避免请求过于频繁
    await asyncio.sleep(2)


def _notify_product(notifier, stats, product: dict, keyword: str):
    """推送单个商品通知。"""
    title = f"闲鱼捡漏: {product['title'][:30]}"
    price_str = f"¥{product['price']:.2f}".rstrip("0").rstrip(".")
    content = (
        f"监控词: {keyword}\n"
        f"价格: {price_str} 💰\n"
        f"标题: {product['title']}\n"
    )
    if product.get("location"):
        content += f"地区: {product['location']}\n"
    if product.get("status"):
        content += f"成色: {product['status']}\n"

    url = product.get("url", "")
    success = notifier.send(title, content, url)
    if success:
        stats.record_notification()
    logger.info(
        f"📦 新商品推送: [{price_str}] {product['title'][:40]} → {url}"
    )


async def run_monitor_forever(monitor, notifier, storage, stats):
    """持续运行监控循环。"""
    interval = MONITOR_SETTINGS.get("interval_minutes", 30)
    logger.info("=" * 55)
    logger.info("  闲鱼价格监控已启动")
    logger.info(f"  监控商品数: {len(MONITOR_ITEMS)}")
    logger.info(f"  轮询间隔: {interval} 分钟")
    logger.info(f"  通知渠道: {notifier.report_channels()}")
    logger.info("  按 Ctrl+C 停止")
    logger.info("=" * 55)

    await check_once(monitor, notifier, storage, stats)

    while True:
        logger.info(f"⏳ 下一轮检查将在 {interval} 分钟后开始...")
        await asyncio.sleep(interval * 60)
        logger.info("🔄 开始新一轮检查...")
        await check_once(monitor, notifier, storage, stats)


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
    parser.add_argument("--stats", action="store_true", help="显示历史统计信息")
    args = parser.parse_args()

    if args.stats:
        stats = StatsCollector(MONITOR_SETTINGS.get("data_dir", "./data"))
        print(stats.get_summary())
        return

    if not validate_config():
        sys.exit(1)

    monitor = GoofishMonitor(MONITOR_SETTINGS)

    if args.login:
        await cmd_login(monitor)
        return

    storage = SeenStorage(MONITOR_SETTINGS.get("data_dir", "./data"))
    stats = StatsCollector(MONITOR_SETTINGS.get("data_dir", "./data"))
    notifier = NotifierManager()

    try:
        await monitor.start()
        if args.once:
            logger.info("单轮检查模式")
            await check_once(monitor, notifier, storage, stats)
            logger.info("单轮检查完成")
        else:
            await run_monitor_forever(monitor, notifier, storage, stats)
    except KeyboardInterrupt:
        logger.info("收到停止信号，正在退出...")
    except Exception as e:
        logger.error(f"运行出错: {e}")
    finally:
        await monitor.stop()


if __name__ == "__main__":
    asyncio.run(main())
