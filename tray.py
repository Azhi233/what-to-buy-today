"""
闲鱼价格监控 - 系统托盘程序（Windows）
===========================================
启动后以托盘图标常驻后台，自动管理监控进程（app.py）。
右键菜单：
  - 显示仪表盘：在浏览器打开监控面板
  - 重新登录闲鱼：打开扫码登录窗口
  - 退出：停止监控进程并退出托盘

用法：
  python tray.py            # 启动托盘（自动拉起/复用监控进程）
依赖：pystray / Pillow（Windows 另需 pywin32）
"""

import os
import subprocess
import sys
import urllib.request

from PIL import Image, ImageDraw
import pystray

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_URL = "http://127.0.0.1:5000"
HEALTHZ_URL = DASHBOARD_URL + "/api/healthz"
CREATE_NO_WINDOW = 0x08000000

_monitor_proc: subprocess.Popen | None = None


def _python() -> str:
    """优先使用安装脚本创建的虚拟环境解释器。"""
    venv = os.path.join(BASE_DIR, ".venv", "Scripts", "python.exe")
    return venv if os.path.exists(venv) else sys.executable


def _app_is_running() -> bool:
    """通过健康探针判断监控进程是否在运行。"""
    try:
        with urllib.request.urlopen(HEALTHZ_URL, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def _ensure_monitor_running() -> None:
    """后台拉起监控进程（app.py），已运行则复用。"""
    global _monitor_proc
    if _app_is_running():
        return
    if _monitor_proc is not None and _monitor_proc.poll() is None:
        return
    _monitor_proc = subprocess.Popen(
        [_python(), os.path.join(BASE_DIR, "app.py"), "--no-browser",
         "--host", "127.0.0.1"],
        cwd=BASE_DIR,
        creationflags=CREATE_NO_WINDOW,
    )


def _stop_monitor() -> None:
    """终止监控进程（含其浏览器子进程）。"""
    global _monitor_proc
    proc = _monitor_proc
    if proc is not None and proc.poll() is None:
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
        )
    _monitor_proc = None


def on_open_dashboard(icon, item):
    """打开浏览器仪表盘。"""
    import webbrowser
    webbrowser.open(DASHBOARD_URL)


def on_relogin(icon, item):
    """弹出扫码登录窗口（run.py --login）。"""
    subprocess.Popen(
        [_python(), os.path.join(BASE_DIR, "run.py"), "--login"],
        cwd=BASE_DIR,
    )


def on_exit(icon, item):
    """停止监控并退出托盘。"""
    _stop_monitor()
    icon.stop()


def _create_icon_image() -> Image.Image:
    """绘制托盘图标：橙色圆底 + 白色上升箭头（价格监控语义）。"""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, size - 2, size - 2), fill=(255, 140, 0, 255))
    # 上升折线（价格趋势）
    d.line([(14, 46), (26, 34), (34, 40), (50, 20)], fill="white", width=5, joint="curve")
    # 箭头尖
    d.polygon([(50, 20), (42, 18), (46, 26)], fill="white")
    return img


def main():
    # 拉起监控进程（幂等：已在运行则复用）
    _ensure_monitor_running()

    menu = pystray.Menu(
        pystray.MenuItem("显示仪表盘", on_open_dashboard, default=True),
        pystray.MenuItem("重新登录闲鱼", on_relogin),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", on_exit),
    )
    icon = pystray.Icon(
        "XianYuMonitor",
        icon=_create_icon_image(),
        title="闲鱼价格监控",
        menu=menu,
    )
    icon.run()


if __name__ == "__main__":
    main()
