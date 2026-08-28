from database import Database, parse_exclude_keywords


def _sample_item(item_id: str, price: float, title: str = "iPhone 15") -> dict:
    return {
        "item_id": item_id,
        "keyword": "iPhone 15",
        "title": title,
        "price": price,
        "url": f"https://www.goofish.com/item?id={item_id}",
        "image": "",
        "location": "",
        "status": "",
        "seller_credit": "",
        "risk_flags": "",
    }


def test_seed_bark_from_config_is_noop_without_real_key(tmp_path):
    db = Database(str(tmp_path / "monitor.db"))
    try:
        assert db.seed_bark_from_config() == 0
        assert db.get_bark_targets() == []
    finally:
        db.close()


def test_clear_items_removes_items_history_and_price_changes(tmp_path):
    db = Database(str(tmp_path / "monitor.db"))
    try:
        db.upsert_item(_sample_item("1", 3000.0), "iPhone 15")
        db.record_price_history("iPhone 15", 3000.0, 3000.0, 3000.0, 1, 3000.0, 3000.0, 1)
        db.add_price_change("1", "iPhone 15", 3500.0, 3000.0, "iPhone 15")

        db.clear_items()
        assert db.get_latest_items() == []
        assert db.get_price_history("iPhone 15") == []
        assert db.get_recent_price_changes() == []
    finally:
        db.close()


def test_cleanup_expired_removes_old_price_changes(tmp_path):
    db = Database(str(tmp_path / "monitor.db"))
    try:
        db.add_price_change("1", "iPhone 15", 3500.0, 3000.0, "iPhone 15")
        db._execute(
            "UPDATE item_price_changes SET time=datetime('now','localtime','-100 days')"
        )
        stats = db.cleanup_expired(items_days=30, history_days=60,
                                   checks_keep=500, notifications_keep=1000)
        assert stats["price_changes_deleted"] >= 1
        assert db.get_recent_price_changes() == []
    finally:
        db.close()


def test_get_last_price_change_returns_most_recent(tmp_path):
    db = Database(str(tmp_path / "monitor.db"))
    try:
        assert db.get_last_price_change("1") is None
        db.add_price_change("1", "iPhone 15", 3500.0, 3200.0, "iPhone 15")
        db.add_price_change("1", "iPhone 15", 3200.0, 3000.0, "iPhone 15")
        change = db.get_last_price_change("1")
        assert change is not None
        assert change["old_price"] == 3200.0
        assert change["new_price"] == 3000.0
    finally:
        db.close()


def test_parse_exclude_keywords_keeps_phrase_with_space(tmp_path):
    raw = "iPhone 14,换屏，爆屏"
    assert parse_exclude_keywords(raw) == ["iPhone 14", "换屏", "爆屏"]
    assert parse_exclude_keywords("") == []
