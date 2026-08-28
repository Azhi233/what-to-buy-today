"""tray.py 系统托盘：菜单结构与图标生成。"""
import os
import sys

sys.path.insert(0, ".")
from tray import _create_icon_image, _python  # noqa: E402


def test_icon_image_generated():
    """托盘图标应生成 64x64 RGBA 图。"""
    img = _create_icon_image()
    assert img.size == (64, 64)
    assert img.mode == "RGBA"


def test_python_prefers_venv():
    """_python() 应优先返回 .venv 解释器。"""
    p = _python()
    assert os.path.basename(os.path.dirname(p)) == "Scripts"
