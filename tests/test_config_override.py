"""config.py 服务器单品模式（MONITOR_KEYWORD 环境变量覆盖）。"""
import importlib
import os

import pytest


@pytest.fixture
def fresh_config():
    """每次重新加载 config 模块，并清理 MONITOR_* 环境变量，隔离用例。"""
    for k in ("MONITOR_KEYWORD", "MONITOR_MAX_PRICE", "MONITOR_MIN_PRICE",
              "MONITOR_EXCLUDE_KEYWORDS", "MONITOR_MUST_INCLUDE"):
        os.environ.pop(k, None)
    yield
    for k in ("MONITOR_KEYWORD", "MONITOR_MAX_PRICE", "MONITOR_MIN_PRICE",
              "MONITOR_EXCLUDE_KEYWORDS", "MONITOR_MUST_INCLUDE"):
        os.environ.pop(k, None)
    importlib.reload(importlib.import_module("config"))


def _load():
    import config
    importlib.reload(config)
    return config


def test_single_item_env_override(fresh_config):
    """设置 MONITOR_KEYWORD 后，MONITOR_ITEMS 仅包含该单商品。"""
    os.environ["MONITOR_KEYWORD"] = "DGX spark"
    os.environ["MONITOR_MAX_PRICE"] = "30000"
    os.environ["MONITOR_MIN_PRICE"] = "25000"
    os.environ["MONITOR_EXCLUDE_KEYWORDS"] = "询价,私聊"
    os.environ["MONITOR_MUST_INCLUDE"] = "全新"
    config = _load()
    assert len(config.MONITOR_ITEMS) == 1
    item = config.MONITOR_ITEMS[0]
    assert item["keyword"] == "DGX spark"
    assert item["max_price"] == 30000
    assert item["min_price"] == 25000
    assert item["exclude_keywords"] == ["询价", "私聊"]
    assert item["must_include"] == ["全新"]


def test_env_override_strips_blank_tokens(fresh_config):
    """排除/必须词的空段应被过滤，不带逗号时为空列表。"""
    os.environ["MONITOR_KEYWORD"] = "DGX spark"
    os.environ["MONITOR_EXCLUDE_KEYWORDS"] = "询价, ,私聊"
    os.environ["MONITOR_MUST_INCLUDE"] = ""
    config = _load()
    item = config.MONITOR_ITEMS[0]
    assert item["exclude_keywords"] == ["询价", "私聊"]
    assert item["must_include"] == []


def test_default_items_when_no_override(fresh_config):
    """未设置 MONITOR_KEYWORD 时保持默认列表（桌面/测试不受影响）。"""
    config = _load()
    assert config.MONITOR_ITEMS[0]["keyword"] == "iPhone 15 Pro Max"
