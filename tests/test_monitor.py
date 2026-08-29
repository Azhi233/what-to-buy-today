import pytest

from monitor import (
    credit_score,
    detect_scam,
    evaluate_item,
    matches_keyword,
    parse_price,
    parse_price_extended,
)
from monitor_service import _iqr_trim


# ── parse_price ────────────────────────────────────────────────

def test_parse_price_extracts_number():
    assert parse_price("¥ 3999") == 3999.0
    assert parse_price("3999元") == 3999.0
    assert parse_price("888.5") == pytest.approx(888.5)
    assert parse_price("> 2999") == 2999.0


def test_parse_price_empty_or_garbage_returns_none():
    assert parse_price("") is None
    assert parse_price(None) is None
    assert parse_price("abc") is None
    assert parse_price("商品下架") is None


# ── parse_price_extended（万元缩写）────────────────────────────

def test_parse_price_extended_low_budget_keeps_literal():
    """低预算监控（如配件类）下 X.YY 保持字面价，不误伤低价商品。"""
    price, scaled = parse_price_extended("¥3.5", "3", ".5", True, max_price=50)
    assert price == pytest.approx(3.5)
    assert not scaled


def test_parse_price_extended_high_budget_promotes_to_wan():
    """监控上限接近万元时，X.YY 识别为万元缩写（回归：29999 曾误识别为 2.99 元）。"""
    price, scaled = parse_price_extended("¥2.99", "2", ".99", True, max_price=29999)
    assert price == pytest.approx(29900)
    assert scaled
    price, scaled = parse_price_extended("¥3.20", "3", ".20", True, max_price=29999)
    assert price == pytest.approx(32000)
    assert scaled


def test_parse_price_extended_explicit_wan():
    price, scaled = parse_price_extended("¥3.20万", "3", ".20", True, max_price=5000)
    assert price == pytest.approx(32000)
    assert scaled


def test_parse_price_extended_plain_integer():
    price, scaled = parse_price_extended("¥2400", "2400", "", True, max_price=5000)
    assert price == pytest.approx(2400)
    assert not scaled


def test_parse_price_extended_wan_without_decimal():
    """回归：闲鱼"3万"拆成 number='3'、无 decimal 时，不得解析成 3 元。"""
    # 显式"万"
    price, scaled = parse_price_extended("¥3万", "3", "", True, max_price=30000)
    assert price == pytest.approx(30000)
    assert scaled
    # 纯缩写"¥3"（无"万"字）+ 高监测区间 → 按万元（实为 3 万）
    price, scaled = parse_price_extended("¥3", "3", "", True, max_price=30000,
                                         min_price=26666)
    assert price == pytest.approx(30000)
    assert scaled
    # 低监测区间（配件类）→ 保持字面价，不误伤
    price, scaled = parse_price_extended("¥3", "3", "", True, max_price=50)
    assert price == pytest.approx(3)
    assert not scaled
    # 普通整数不受影响
    price, scaled = parse_price_extended("¥30", "30", "", True, max_price=30000)
    assert price == pytest.approx(30)
    assert not scaled


# ── matches_keyword ────────────────────────────────────────────

def test_matches_keyword_requires_model_suffixes():
    assert matches_keyword("iPhone 15 Pro Max", "iPhone 15 Pro Max")
    assert not matches_keyword("iPhone 15 256G", "iPhone 15 Pro Max")


def test_matches_keyword_empty_input():
    assert not matches_keyword("", "iPhone 15")
    assert not matches_keyword("二手iPhone", "")


# ── evaluate_item：排除词 ─────────────────────────────────────

def test_evaluate_item_exclude_keyword_filters_full_title():
    """完整标题含排除词必须过滤（回归：屏蔽词不生效）。"""
    v = evaluate_item(
        {"title": "DGX spark 全新未拆 详情咨询 私聊", "price": 29900},
        max_price=30000, min_price=25000,
        exclude_keywords=["咨询", "私聊"],
    )
    assert v["pass"] is False
    assert any("排除词" in r for r in v["hard"])


def test_evaluate_item_exclude_keyword_keeps_normal_title():
    """标题不含排除词时正常通过价格区间校验。"""
    v = evaluate_item(
        {"title": "DGX spark 全新未拆封", "price": 28800},
        max_price=30000, min_price=25000,
        exclude_keywords=["咨询", "私聊"],
    )
    assert v["pass"] is True


def test_evaluate_item_price_range_filter():
    """价格区间：万元解析后的 29900 在 [25000, 30000] 内应通过。"""
    v = evaluate_item(
        {"title": "DGX spark", "price": 29900},
        max_price=30000, min_price=25000,
        exclude_keywords=[], must_include=[],
    )
    assert v["pass"] is True


# ── credit_score ───────────────────────────────────────────────

def test_credit_score_level_mapping():
    assert credit_score("卖家信用极好") == 5
    assert credit_score("卖家信用优秀") == 4.5
    assert credit_score("百分百好评") == 4.5
    assert credit_score("卖家信用较好") == 4
    assert credit_score("卖家信用一般") == 3
    assert credit_score("初出茅庐") == 2
    assert credit_score("卖家信用较差") == 1
    assert credit_score("卖家信用极差") == 0


def test_credit_score_unknown_or_empty_returns_none():
    assert credit_score("") is None
    assert credit_score(None) is None
    assert credit_score("某种未知等级") is None


# ── detect_scam ────────────────────────────────────────────────

RULES = {
    "站外导流": {"keywords": ["加微信"], "mode": "exclude"},
    "夸张承诺": {"keywords": ["假一赔十"], "mode": "mark"},
}


def test_detect_scam_exclude_and_mark_modes():
    hard, warn = detect_scam(
        {"title": "iPhone 加微信 假一赔十", "price": 3000},
        median_price=3000,
        scam_rules=RULES,
    )
    assert any("站外导流" in r for r in hard)
    assert any("夸张承诺" in r for r in warn)


def test_detect_scam_phone_number_is_marked_not_filtered():
    hard, warn = detect_scam(
        {"title": "自用iPhone 13800000000", "price": 3000},
        scam_rules=RULES,
    )
    assert hard == []
    assert any("手机号" in w for w in warn)


def test_detect_scam_price_anomaly_marks_low_price():
    hard, warn = detect_scam(
        {"title": "iPhone 15", "price": 1000},
        median_price=5000,
        scam_rules=RULES,
        anomaly_ratio=0.5,
    )
    assert any("价格仅为市场中位价" in w for w in warn)


def test_detect_scam_empty_rules():
    hard, warn = detect_scam({"title": "iPhone 15", "price": 3000})
    assert hard == []
    assert warn == []


# ── _iqr_trim ──────────────────────────────────────────────────

def test_iqr_trim_leaves_small_sample_unchanged():
    prices = [10.0, 20.0, 30.0, 40.0]
    assert _iqr_trim(prices) == prices


def test_iqr_trim_removes_outliers():
    prices = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 100.0]
    trimmed = _iqr_trim(prices)
    assert 100.0 not in trimmed
    assert len(trimmed) == 10


def test_iqr_trim_leaves_unchanged_when_iqr_zero():
    prices = [5.0, 5.0, 5.0, 5.0, 5.0]
    assert _iqr_trim(prices) == prices


# ── evaluate_item ──────────────────────────────────────────────

def test_evaluate_item_filters_scam_and_marks_unknown_credit():
    verdict = evaluate_item(
        {"title": "iPhone 15 Pro Max 加微信", "price": 3000},
        max_price=5000,
        min_price=500,
        exclude_keywords=[],
        must_include=[],
        min_seller_credit="信用一般",
        strict_unknown_credit=False,
        scam_rules={"站外导流": {"keywords": ["加微信"], "mode": "exclude"}},
    )
    assert not verdict["pass"]
    assert any("站外导流" in reason for reason in verdict["hard"])
    assert "卖家信用未知" in verdict["warn"]


def test_evaluate_item_anomaly_ratio_triggers_price_warning():
    """PRICE_ANOMALY_RATIO 传入 evaluate_item 时，低价应触发「价格异常」标记。"""
    verdict = evaluate_item(
        {"title": "iPhone 15", "price": 1000, "seller_credit": "信用极好"},
        max_price=5000,
        min_price=100,
        exclude_keywords=[],
        must_include=[],
        min_seller_credit="信用一般",
        median_price=5000,
        scam_rules={},
        anomaly_ratio=0.3,
    )
    assert verdict["pass"]
    assert any("价格仅为市场中位价" in w for w in verdict["warn"])


def test_evaluate_item_sets_anomaly_ratio_zero_disables_warning():
    verdict = evaluate_item(
        {"title": "iPhone 15", "price": 1000, "seller_credit": "信用极好"},
        max_price=5000,
        min_price=100,
        exclude_keywords=[],
        must_include=[],
        min_seller_credit="信用一般",
        median_price=5000,
        anomaly_ratio=0,
    )
    assert all("价格仅为市场中位价" not in w for w in verdict["warn"])
