from app import app


def test_healthz_is_public():
    from app import service

    client = app.test_client()
    service.status = "running"
    try:
        resp = client.get("/api/healthz")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
    finally:
        service.status = "stopped"


def test_healthz_reflects_db_and_monitor_state():
    from app import service

    client = app.test_client()
    service.status = "running"
    resp = client.get("/api/healthz")
    body = resp.get_json()
    assert body["ok"] is True
    assert body["db_ok"] is True

    service.status = "error"
    resp = client.get("/api/healthz")
    assert resp.get_json()["ok"] is False
    service.status = "stopped"


def test_invalid_json_returns_json_error_instead_of_server_error():
    client = app.test_client()
    resp = client.post(
        "/api/products",
        data="not-json",
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert resp.is_json


def test_add_product_validates_price_range():
    client = app.test_client()
    resp = client.post("/api/products", json={
        "keyword": "测试商品",
        "max_price": 100,
        "min_price": 200,
    })
    assert resp.status_code == 400


def test_remote_api_requires_token_when_unset():
    client = app.test_client()
    resp = client.get("/api/status", environ_base={"REMOTE_ADDR": "10.1.2.3"})
    assert resp.status_code == 401

    resp = client.get("/api/healthz", environ_base={"REMOTE_ADDR": "10.1.2.3"})
    assert resp.status_code == 200


def test_local_api_allowed_without_token():
    client = app.test_client()
    resp = client.get("/api/status", environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert resp.status_code == 200


def test_export_filename_sanitized():
    client = app.test_client()
    resp = client.get("/api/export?keyword=..%2F..%2Fetc%2Fpasswd")
    assert resp.status_code == 200
    disp = resp.headers["Content-Disposition"]
    assert ".." not in disp
    assert "/" not in disp
    assert disp.endswith(".csv")


def test_export_prevents_csv_formula_injection():
    """S-06：以 = + - @ 开头的单元格加单引号，防止 Excel 公式注入。"""
    from app import db

    db.add_product("CSV注入测试", 1000, 100, "", 1, "")
    products = [p for p in db.get_products() if p["keyword"] == "CSV注入测试"]
    assert products, "测试商品未创建"
    pid = products[0]["id"]
    try:
        db.upsert_item({
            "item_id": "csv_inject_1",
            "title": "=HYPERLINK(http://evil.example)",
            "price": 500,
            "raw_price": "500",
            "url": "https://www.goofish.com/item?id=csv_inject_1",
            "image": "",
            "location": "+86 13800000000",
            "status": "",
            "seller_credit": "",
            "risk_flags": "@SUM(A1:A2)",
        }, "CSV注入测试")
        client = app.test_client()
        resp = client.get("/api/export?keyword=CSV%E6%B3%A8%E5%85%A5%E6%B5%8B%E8%AF%95")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8-sig")
        assert "'=HYPERLINK(http://evil.example)" in body
        assert "'+86 13800000000" in body
        assert "'@SUM(A1:A2)" in body
    finally:
        db.delete_product(pid)


# ── 新用户引导 / 通知渠道配置 ─────────────────────────────

def test_onboarding_status_empty_db():
    """空数据库（无通知渠道、无商品）→ 引导两项都需要。"""
    from app import db

    client = app.test_client()
    for p in db.get_products():
        db.delete_product(p["id"])
    for t in db.get_bark_targets():
        db.delete_bark_target(t["id"])
    db.set_channel_config({})
    s = client.get("/api/onboarding/status").get_json()
    assert s["need_notify"] is True
    assert s["need_product"] is True


def test_onboarding_status_clears_after_bark_and_product():
    """配置 Bark + 添加商品后 → 引导不再出现（第二次进入不需要）。"""
    from app import db

    client = app.test_client()
    tid = db.add_bark_target("测试", "https://api.day.app", "test_bark_key_1234567890", 1)
    pid = db.add_product("引导测试商品", 1000, 100, "", 1, "")
    try:
        s = client.get("/api/onboarding/status").get_json()
        assert s["need_notify"] is False
        assert s["need_product"] is False
    finally:
        db.delete_bark_target(tid)
        db.delete_product(pid)


def test_channel_config_smtp_validation_and_masking():
    """SMTP 配置：缺必填 400；保存后 GET 密码脱敏。"""
    import json

    from app import db

    client = app.test_client()
    # 缺收件人 → 400
    r = client.post("/api/channel-config", json={"smtp": {
        "host": "smtp.qq.com", "user": "a@qq.com", "password": "secret",
    }})
    assert r.status_code == 400
    # 完整配置 → 200
    r = client.post("/api/channel-config", json={"smtp": {
        "host": "smtp.qq.com", "port": 465, "user": "a@qq.com",
        "password": "smtp_secret_123", "to": "b@qq.com",
    }})
    assert r.status_code == 200
    try:
        cfg = client.get("/api/channel-config").get_json()
        assert cfg["smtp"]["password_set"] is True
        assert "smtp_secret_123" not in json.dumps(cfg)
        # SMTP 配置生效后，引导不再提示通知
        s = client.get("/api/onboarding/status").get_json()
        assert s["need_notify"] is False
    finally:
        db.set_channel_config({})


# ── S-07 / S-04 / 运行控制 回归 ───────────────────────────────

def test_bark_target_key_is_masked():
    """S-07：Bark Key 不回明文，仅返回脱敏值。"""
    client = app.test_client()
    key = "test_bark_key_1234567890"
    resp = client.post("/api/bark-targets", json={
        "label": "回归测试", "server": "https://api.day.app", "bark_key": key,
    })
    assert resp.status_code == 200
    tid = resp.get_json()["id"]
    try:
        rows = client.get("/api/bark-targets").get_json()
        row = next(r for r in rows if r["id"] == tid)
        assert "bark_key" not in row
        assert "****" in row["bark_key_masked"]
        assert key not in row["bark_key_masked"]
        assert key not in str(row)
    finally:
        client.delete(f"/api/bark-targets/{tid}")


def test_add_bark_target_validates_key_length():
    client = app.test_client()
    resp = client.post("/api/bark-targets", json={
        "label": "t", "server": "https://api.day.app", "bark_key": "short",
    })
    assert resp.status_code == 400


def test_retention_rejects_zero_or_negative():
    client = app.test_client()
    resp = client.post("/api/retention", json={"items_days": 0})
    assert resp.status_code == 400
    resp = client.post("/api/retention", json={"checks_keep": -5})
    assert resp.status_code == 400


def test_settings_rejects_bad_interval():
    client = app.test_client()
    resp = client.post("/api/settings", json={"interval_minutes": "abc"})
    assert resp.status_code == 400


def test_control_rejects_unknown_action():
    client = app.test_client()
    resp = client.post("/api/control", json={"action": "fly"})
    assert resp.status_code == 400


def test_product_toggle_and_delete():
    client = app.test_client()
    resp = client.post("/api/products", json={
        "keyword": "切换测试商品", "max_price": 1000, "min_price": 100,
    })
    assert resp.status_code == 200
    pid = resp.get_json()["id"]
    try:
        r = client.post(f"/api/products/{pid}/toggle")
        assert r.get_json()["enabled"] is False
        r = client.post(f"/api/products/{pid}/toggle")
        assert r.get_json()["enabled"] is True
    finally:
        client.delete(f"/api/products/{pid}")
