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
import threading
import time
import urllib.request

from PIL import Image, ImageDraw
import pystray

# 触发 config._load_dotenv()：让 .env 中的 HOST/PORT 等环境变量在读取前生效，
# 与 app.py / run.py 的配置来源保持一致
import config  # noqa: F401

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_HOST = os.environ.get("HOST", "127.0.0.1")
DASHBOARD_PORT = os.environ.get("PORT", "5000")
DASHBOARD_URL = f"http://{DASHBOARD_HOST}:{DASHBOARD_PORT}"
HEALTHZ_URL = DASHBOARD_URL + "/api/healthz"
CREATE_NO_WINDOW = 0x08000000
# 登录进程退出后等待 chromium 释放 profile 锁的秒数
LOGIN_RESTART_DELAY = 3

# 托盘单实例互斥体名（Windows 命名 Mutex）
TRAY_MUTEX_NAME = "XianYuMonitorTrayMutex"


def _acquire_single_instance() -> bool:
    """Windows 命名互斥体单实例保护：重复启动托盘时静默退出。

    防止 ONLOGON 任务与用户手动双击同时拉起两个托盘，
    进而出现两个监控进程抢占端口/浏览器配置。
    """
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, TRAY_MUTEX_NAME)
        if not handle:
            return True
        already_exists = kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
        return not already_exists
    except Exception:
        return True

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
         "--host", DASHBOARD_HOST, "--port", DASHBOARD_PORT],
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
    """重新登录闲鱼：先暂停监控释放浏览器配置，登录完成后自动恢复监控。

    若不先暂停，两个 Chromium 共用同一 profile 目录时登录窗口会被隐藏的
    监控实例接管（表现为空白页/无法显示）。
    """
    _stop_monitor()  # 释放 profile 锁（taskkill 进程树，含监控 chromium）
    proc = subprocess.Popen(
        [_python(), os.path.join(BASE_DIR, "run.py"), "--login"],
        cwd=BASE_DIR,
    )
    threading.Thread(target=_restart_after_login, args=(proc,), daemon=True).start()


def _restart_after_login(login_proc):
    """登录进程退出（成功/超时/关闭窗口）后，等待 profile 释放再恢复监控。"""
    login_proc.wait()
    time.sleep(LOGIN_RESTART_DELAY)  # 等待 chromium 完全退出并释放 profile 锁
    _ensure_monitor_running()


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
    if not _acquire_single_instance():
        print("托盘已在运行，本次启动已忽略（单实例保护）")
        return
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
