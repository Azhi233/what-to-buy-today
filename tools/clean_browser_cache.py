"""
清空 Chromium 浏览器缓存目录（保留登录态与商品数据库）。
==========================================================
用途：服务器版每周自动清理浏览器缓存（防磁盘膨胀 / 保持浏览器健康）。
只删除 Cache / Code Cache / GPUCache / Service Worker 缓存等纯缓存目录；
保留 Cookies、Local Storage、Session Storage（登录态）与整个 data/ 数据库。

用法：
  python tools/clean_browser_cache.py [browser_profile目录]
  默认目录：/app/browser_profile（容器内）或 ./browser_profile（本地）

服务器 cron 示例（每周一 04:00，容器运行中直接执行即可，Chromium 会自动重建缓存）：
  0 4 * * 1 docker exec xianyu-monitor python /app/tools/clean_browser_cache.py >> /opt/xianyu/cache-clean.log 2>&1
"""

import argparse
import os
import shutil
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 仅缓存，可安全删除；登录态（Cookies/Local Storage）与 Preferences 保留
CACHE_DIRS = [
    "Default/Cache",
    "Default/Code Cache",
    "Default/GPUCache",
    "Default/Media Cache",
    "Default/Service Worker/CacheStorage",
    "Default/Service Worker/ScriptCache",
]


def _dir_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def clean_browser_cache(profile_dir: str) -> tuple[int, list[str]]:
    """删除 profile 下的浏览器缓存目录。返回 (释放字节数, 失败列表)。"""
    if not os.path.isdir(profile_dir):
        raise FileNotFoundError(f"浏览器配置目录不存在: {profile_dir}")
    freed = 0
    failed = []
    for rel in CACHE_DIRS:
        path = os.path.join(profile_dir, rel)
        if not os.path.isdir(path):
            continue
        try:
            freed += _dir_size(path)
            shutil.rmtree(path, ignore_errors=True)
            if os.path.exists(path):
                raise OSError("目录删除后仍存在")
        except Exception as e:  # 文件被占用等：跳过，下次再清
            failed.append(f"{rel}: {e}")
    return freed, failed


def main() -> int:
    parser = argparse.ArgumentParser(description="清空 Chromium 浏览器缓存（保留登录态）")
    parser.add_argument("profile_dir", nargs="?", default="",
                        help="browser_profile 目录（默认 /app/browser_profile 或 ./browser_profile）")
    args = parser.parse_args()

    profile_dir = args.profile_dir or os.environ.get("MONITOR_BROWSER_PROFILE", "") or (
        "/app/browser_profile" if os.path.isdir("/app/browser_profile") else "./browser_profile")
    try:
        freed, failed = clean_browser_cache(profile_dir)
    except FileNotFoundError as e:
        print(f"[✗] {e}")
        return 2

    mb = freed / 1024 / 1024
    print(f"[✓] 已清理浏览器缓存，释放 {mb:.1f} MB（{profile_dir}）")
    if failed:
        print(f"[!] 以下目录清理失败（浏览器使用中，通常可忽略）: {failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
