"""run.py 配置校验的单元测试。"""


def test_validate_config_rejects_min_above_max(monkeypatch):
    from run import validate_config

    items = [{"keyword": "iPhone 15", "max_price": 5000, "min_price": 6000}]
    monkeypatch.setattr("run.MONITOR_ITEMS", items)
    assert not validate_config()


def test_validate_config_rejects_non_numeric_price(monkeypatch):
    from run import validate_config

    items = [{"keyword": "iPhone 15", "max_price": "五千", "min_price": 0}]
    monkeypatch.setattr("run.MONITOR_ITEMS", items)
    assert not validate_config()


def test_validate_config_rejects_zero_max_price(monkeypatch):
    from run import validate_config

    items = [{"keyword": "iPhone 15", "max_price": 0, "min_price": 0}]
    monkeypatch.setattr("run.MONITOR_ITEMS", items)
    assert not validate_config()


def test_validate_config_accepts_valid_items(monkeypatch):
    from run import validate_config

    items = [{"keyword": "iPhone 15", "max_price": 5000, "min_price": 500}]
    monkeypatch.setattr("run.MONITOR_ITEMS", items)
    assert validate_config()


def test_validate_config_rejects_missing_keyword(monkeypatch):
    from run import validate_config

    monkeypatch.setattr("run.MONITOR_ITEMS", [{"max_price": 5000}])
    assert not validate_config()
