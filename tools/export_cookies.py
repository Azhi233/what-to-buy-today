"""
本地登录态导出脚本（服务器版"本地扫码模式"配套工具）
==========================================================
本地扫码登录后运行本脚本，把闲鱼域名的明文登录 cookie 导出为 cookies.json，
随 browser_profile/data 一起上传服务器。容器启动时注入这些 cookie，
解决 Windows app-bound 加密 cookie 无法在 Linux 容器解密的问题。

用法：
  python tools/export_cookies.py [browser_profile目录] [输出文件]

  默认读取 ./browser_profile（或 MONITOR_BROWSER_PROFILE），输出 ./cookies.json。
  需要已安装 playwright 依赖（项目的 .venv）。
  注意：本机监控正在运行时请先停止（profile 被浏览器占用）。
"""

import asyncio
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)


def _resolve(profile_arg: str, out_arg: str):
    env = os.environ.get("MONITOR_BROWSER_PROFILE", "")
    profile = os.path.abspath(profile_arg) if profile_arg else (
        os.path.abspath(env) if env else os.path.join(BASE_DIR, "browser_profile"))
    out = os.path.abspath(out_arg) if out_arg else os.path.join(BASE_DIR, "cookies.json")
    return profile, out


def _to_add_cookie_format(cookies: list[dict]) -> list[dict]:
    """Playwright cookies() 返回结构 → add_cookies() 所需结构。

    注意：add_cookies 的 Cookie 对象中 url 与 domain 互斥（同时提供会报错），
    这里保留 domain + path（更精确），不再构造 url。
    """
    result = []
    for c in cookies:
        entry = {k: c[k] for k in ("name", "value", "domain", "path") if k in c}
        if "expires" in c and c.get("expires", -1) > 0:
            entry["expires"] = c["expires"]
        if "httpOnly" in c:
            entry["httpOnly"] = c["httpOnly"]
        if "secure" in c:
            entry["secure"] = c["secure"]
        if "sameSite" in c and c["sameSite"]:
            entry["sameSite"] = c["sameSite"]
        result.append(entry)
    return result


async def export(profile: str, out: str) -> int:
    from playwright.async_api import async_playwright

    if not os.path.isdir(profile):
        print(f"[✗] 浏览器配置目录不存在: {profile}")
        return 1

    p = await async_playwright().start()
    ctx = await p.chromium.launch_persistent_context(profile, headless=True)
    try:
        cookies = await ctx.cookies()
    finally:
        await ctx.close()
        await p.stop()

    # 只保留闲鱼相关域名，减小体积与隐私面
    keep = [c for c in cookies if "goofish.com" in c.get("domain", "") or "taobao.com" in c.get("domain", "")]
    if not keep:
        print("[✗] 未找到闲鱼/淘宝域名的 cookie，请确认已扫码登录。")
        return 1

    names = [c["name"] for c in keep if c["name"] in ("unb", "tracknick")]
    if not names:
        print("[⚠] 未找到登录标识 cookie（unb/tracknick），导出的会话可能不完整，请确认已登录。")

    with open(out, "w", encoding="utf-8") as f:
        json.dump(_to_add_cookie_format(keep), f, ensure_ascii=False, indent=1)

    print(f"[✓] 已导出 {len(keep)} 条 cookie 到 {out}（含登录标识: {', '.join(names) or '无'}）")
    print("    请将该文件与 browser_profile/、data/ 一起上传到服务器。")
    return 0


def main() -> int:
    profile, out = _resolve(
        sys.argv[1] if len(sys.argv) > 1 else "",
        sys.argv[2] if len(sys.argv) > 2 else "",
    )
    print(f"读取配置目录: {profile}")
    print(f"输出 cookie 文件: {out}")
    return asyncio.run(export(profile, out))


if __name__ == "__main__":
    sys.exit(main())
