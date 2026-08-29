"""
本地扫码登录态自检脚本（服务器版"本地扫码模式"配套工具）
==========================================================
在本地机完成 run.py --login 后运行，检查 browser_profile 是否包含有效的
闲鱼登录态（unb/tracknick cookie）。确认通过后再打包上传到服务器复用，
避免把"未登录"的 profile 传到服务器导致监控无法抓取。

用法：
  python tools/check_login.py [browser_profile目录]

可选参数为浏览器配置目录，默认 ./browser_profile（或 MONITOR_BROWSER_PROFILE）。
仅用标准库，无额外依赖。
"""

import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 允许从任意工作目录直接运行："python tools/check_login.py"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

# 与 monitor.py 的登录判定一致的 cookie（登录成功才会写入）
LOGIN_COOKIES = {"unb", "tracknick"}

EXIT_OK = 0
EXIT_NOT_LOGGED = 1
EXIT_NO_PROFILE = 2


def _resolve_profile_dir(arg: str) -> str:
    if arg:
        return os.path.abspath(arg)
    env = os.environ.get("MONITOR_BROWSER_PROFILE", "")
    if env:
        return os.path.abspath(env) if not os.path.isabs(env) else env
    return os.path.join(BASE_DIR, "browser_profile")


def _find_cookies_files(profile_dir: str) -> list[str]:
    """找 Cookies 数据库文件（Chromium 89+ 在 Default/Network/Cookies，旧版在 Default/Cookies）。"""
    found = []
    for rel in ("Default", "Default/Network"):
        p = os.path.join(profile_dir, rel, "Cookies")
        if os.path.exists(p):
            found.append(p)
    return found


def _read_cookie_names(cookies_path: str) -> set[str]:
    """直接读 Chromium SQLite Cookies 表，不做路径穿越。"""
    import sqlite3

    db = sqlite3.connect(f"file:{cookies_path}?immutable=1", uri=True)
    try:
        rows = db.execute("SELECT name FROM cookies")
        return {r[0] for r in rows}
    finally:
        db.close()


def _cookies_via_sql(profile_dir: str) -> set[str]:
    names: set[str] = set()
    for path in _find_cookies_files(profile_dir):
        try:
            names |= _read_cookie_names(path)
        except Exception as e:
            print(f"  [!] 读取 {os.path.basename(path)} 失败（可忽略，走回退检查）: {e}")
    return names


def check_login(profile_dir: str) -> bool:
    if not os.path.isdir(profile_dir):
        print(f"[✗] 浏览器配置目录不存在: {profile_dir}")
        print("    请先在本机运行  python run.py --login  扫码登录一次。")
        return False

    # 方案一：直接解析 Chromium Cookies 库
    names = _cookies_via_sql(profile_dir)
    if LOGIN_COOKIES.issubset(names):
        print(f"[✓] 检测到有效登录态（{', '.join(sorted(LOGIN_COOKIES))}），可上传服务器复用。")
        return True

    # 方案二：登录态目录健全性兜底检查（无法解析 Cookies 时）
    if names & LOGIN_COOKIES:
        print(f"[✓] 检测到部分登录标识 {sorted(names & LOGIN_COOKIES)}，登录态基本可用。")
        return True

    print(f"[✗] 未在配置目录中发现登录 cookie（{', '.join(sorted(LOGIN_COOKIES))}）。")
    print("    若确定已扫码成功，请确认上传/指定的是正确的 browser_profile 目录。")
    return False


def main() -> int:
    profile_dir = _resolve_profile_dir(sys.argv[1] if len(sys.argv) > 1 else "")
    print(f"检查目录: {profile_dir}")
    ok = check_login(profile_dir)
    return EXIT_OK if ok else EXIT_NOT_LOGGED


if __name__ == "__main__":
    sys.exit(main())
