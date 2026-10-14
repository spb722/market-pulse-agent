"""Tests for the GCC/intl/other roaming and total_data metrics and field migration."""

from __future__ import annotations

from pathlib import Path

import pytest

from market_pulse.config.formula_config import get_gap_analysis_config
from market_pulse.schemas.omantel import NormalizedOmantelPlan
from market_pulse.services.competitor_normalization_service import add_rule_features
from market_pulse.services.gap_analysis_service import (
    build_metric_gaps,
    compute_weighted_position,
    get_gcc_roaming_minutes_value,
)
from market_pulse.services.omantel_normalization_service import (
    load_catalogue_csv,
    normalize_omantel_row,
)
from market_pulse.services.plan_matching_service import get_capabilities
from market_pulse.services.total_data import compute_total_data

REPO = Path(__file__).resolve().parents[2]


def _plan(**kw):
    base = {"product_type": "COMBO", "category": "prepaid"}
    base.update(kw)
    return base


def _row(**kw):
    base = {"type": "Master", "product_type": "COMBO", "product_id": 1,
            "product_name": "Plan", "price": 5, "validity_in_days": 30}
    base.update(kw)
    return base


# ---------------------------------------------------------------- Omantel step 2


@pytest.mark.parametrize("category", ["PREPAID", "POSTPAID"])
@pytest.mark.parametrize(
    "col,out",
    [
        ("gcc_roaming_data_gb", "gcc_roaming_data_gb"),
        ("gcc_roaming_minutes", "gcc_roaming_minutes"),
        ("intl_roaming_data_gb", "intl_roaming_data_gb"),
        ("other_roaming_minutes", "other_roaming_minutes"),
        ("entertainment_gb", "entertainment_gb"),
    ],
)
def test_new_columns_zero_blank_missing(category, col, out):
    assert normalize_omantel_row(_row(**{col: 0}), category)[out] == 0.0
    assert normalize_omantel_row(_row(**{col: 20}), category)[out] == 20.0
    assert normalize_omantel_row(_row(**{col: None}), category)[out] is None
    assert normalize_omantel_row(_row(**{col: float("nan")}), category)[out] is None
    assert normalize_omantel_row(_row(), category)[out] is None


def test_legacy_columns_fallback_and_new_wins():
    r = normalize_omantel_row(_row(data_roaming=3, roaming_minutes=7), "PREPAID")
    assert r["gcc_roaming_data_gb"] == 3.0
    assert r["gcc_roaming_minutes"] == 7.0
    r = normalize_omantel_row(
        _row(data_roaming=3, gcc_roaming_data_gb=1, roaming_minutes=7, gcc_roaming_minutes=2),
        "PREPAID",
    )
    assert r["gcc_roaming_data_gb"] == 1.0
    assert r["gcc_roaming_minutes"] == 2.0


def test_old_keys_and_message_fields_gone():
    r = normalize_omantel_row(
        _row(message_english="Welcome", message_arabic="x"), "PREPAID"
    )
    for k in ("roaming_data_gb", "roaming_minutes", "message_english", "message_arabic"):
        assert k not in r
    plan = NormalizedOmantelPlan(**r)
    assert plan.total_data is None or isinstance(plan.total_data, float)
    assert not hasattr(plan, "message_english")


@pytest.mark.parametrize(
    "data,social,ent,expected",
    [
        (10, 5, 2, 17.0),
        (10, None, None, 10.0),
        (10, 0, 3, 13.0),
        (None, 5, 2, None),
        (0, None, 1, 1.0),
    ],
)
def test_total_data_formula(data, social, ent, expected):
    assert compute_total_data(data, social, ent) == expected
    row = _row(unit_in_mb=None if data is None else data * 1024,
               data_social=social, entertainment_gb=ent)
    assert normalize_omantel_row(row, "PREPAID")["total_data"] == expected


# ---------------------------------------------------------------- competitor


def test_competitor_new_fields():
    out = add_rule_features({
        "plan_id": "X", "plan_name": "P", "price_omr": 5, "data_gb": 10,
        "social_pass_gb": 2, "entertainment_gb": 1, "total_data": 99,
        "intl_roaming_data_gb": 1, "gcc_roaming_minutes": 20, "other_roaming_minutes": 5,
    })
    assert out["total_data"] == 13.0
    assert out["source_total_data"] == 99
    assert out["intl_roaming_data_gb"] == 1
    assert out["gcc_roaming_minutes"] == 20
    assert out["other_roaming_minutes"] == 5
    assert out["has_roaming"] is True


def test_competitor_legacy_fields_mapped():
    out = add_rule_features({
        "plan_id": "X", "plan_name": "P", "price_omr": 5, "data_gb": 10,
        "roaming_data_gb": 4, "roaming_minutes": 15,
    })
    assert out["intl_roaming_data_gb"] == 4
    assert out["gcc_roaming_minutes"] == 15
    assert out["other_roaming_minutes"] is None
    assert out["entertainment_gb"] is None
    assert out["total_data"] == 10.0
    assert "source_total_data" not in out
    assert out["has_roaming"] is True


@pytest.mark.parametrize("key", ["gcc_roaming_minutes", "other_roaming_minutes", "intl_roaming_data_gb"])
def test_has_roaming_per_field(key):
    assert add_rule_features({key: 3})["has_roaming"] is True
    assert add_rule_features({key: 0})["has_roaming"] is False


def test_capability_detection():
    for key in ("gcc_roaming_minutes", "other_roaming_minutes", "intl_roaming_data_gb", "gcc_roaming_data_gb"):
        assert "ROAMING" in get_capabilities(_plan(**{key: 20}))
        assert "ROAMING" not in get_capabilities(_plan(**{key: 0}))


def test_message_english_ignored():
    assert "ROAMING" not in get_capabilities(_plan(message_english="free roaming"))


# ---------------------------------------------------------------- gap metrics


@pytest.mark.parametrize(
    "metric,key,legacy",
    [
        ("intl_roaming_data", "intl_roaming_data_gb", "roaming_data_gb"),
        ("gcc_roaming_minutes", "gcc_roaming_minutes", "roaming_minutes"),
        ("other_roaming_minutes", "other_roaming_minutes", None),
    ],
)
def test_roaming_metric_scored_missing_and_not_scored(metric, key, legacy):
    g = build_metric_gaps(_plan(**{key: 30}), _plan(**{key: 10}))[metric]
    assert g["position"] == "COMPETITOR_ADVANTAGE"
    g = build_metric_gaps(_plan(**{key: 10}), _plan(**{key: 30}))[metric]
    assert g["position"] == "OMANTEL_ADVANTAGE"
    g = build_metric_gaps(_plan(**{key: 20}), _plan(**{key: 20}))[metric]
    assert g["position"] == "PARITY"
    g = build_metric_gaps(_plan(**{key: 25}), _plan(**{key: 0}))[metric]
    assert g["normalized_advantage"] == -1.0

    for comp, om in [(None, 20), (20, None), (None, None)]:
        g = build_metric_gaps(_plan(**{key: comp}), _plan(**{key: om}))[metric]
        assert g["position"] == "NOT_SCORED"
        assert g["normalized_advantage"] is None

    g = build_metric_gaps(_plan(product_type="ROAMING", **{key: 30}), _plan(**{key: 10}))[metric]
    assert g["position"] == "NOT_SCORED"
    assert g["note"]

    if legacy:
        g = build_metric_gaps(_plan(**{legacy: 30}), _plan(**{key: 10}))[metric]
        assert g["position"] == "COMPETITOR_ADVANTAGE"


def test_no_roam_like_home_fallback_for_minutes():
    comp = _plan(roaming_included=True, voice_minutes=100, data_gb=10)
    assert get_gcc_roaming_minutes_value(comp) is None
    g = build_metric_gaps(comp, _plan(gcc_roaming_minutes=20))["gcc_roaming_minutes"]
    assert g["position"] == "NOT_SCORED"


def test_total_data_metric():
    g = build_metric_gaps(_plan(total_data=30), _plan(total_data=10))["total_data"]
    assert g["position"] == "COMPETITOR_ADVANTAGE"
    g = build_metric_gaps(_plan(total_data=None), _plan(total_data=10))["total_data"]
    assert g["position"] == "NOT_SCORED"
    # Unlimited-aware, like ``data``.
    g = build_metric_gaps(_plan(unlimited_data=True, total_data=None), _plan(total_data=10))["total_data"]
    assert g["competitor"] == "UNLIMITED"
    assert g["position"] == "COMPETITOR_ADVANTAGE"
    g = build_metric_gaps(_plan(total_data=10), _plan(unlimited_data=True))["total_data"]
    assert g["position"] == "OMANTEL_ADVANTAGE"
    g = build_metric_gaps(_plan(unlimited_data=True), _plan(unlimited_data=True))["total_data"]
    assert g["position"] == "PARITY"


def test_old_metric_keys_removed():
    gaps = build_metric_gaps(_plan(), _plan())
    assert "roaming" not in gaps and "roaming_minutes" not in gaps


# ---------------------------------------------------------------- config


def test_combo_weights_config():
    w = get_gap_analysis_config().weights["COMBO"]
    assert w == {
        "price": 0.30, "data": 0.30, "voice": 0.20, "idd": 0.10, "validity": 0.10,
        "social_data": 0.05, "intl_roaming_data": 0.05, "gcc_roaming_minutes": 0.05,
        "other_roaming_minutes": 0.05, "total_data": 0.05,
    }
    for t, tw in get_gap_analysis_config().weights.items():
        if t != "COMBO":
            for m in ("intl_roaming_data", "gcc_roaming_minutes", "other_roaming_minutes", "total_data"):
                assert m not in tw


def test_renormalisation_with_new_metrics():
    config = get_gap_analysis_config()
    comp = _plan(price_omr=10, data_gb=10, voice_minutes=100, intl_minutes=10, validity_days=30,
                 total_data=10, other_roaming_minutes=10)
    om = _plan(price_omr=8, data_gb=5, voice_minutes=200, intl_minutes=20, validity_days=30,
               total_data=5, other_roaming_minutes=0)
    res = compute_weighted_position(comp, build_metric_gaps(comp, om), config=config)
    assert res["effective_weights"]["total_data"] == round(0.05 / 1.10, 4)
    assert res["effective_weights"]["other_roaming_minutes"] == round(0.05 / 1.10, 4)
    assert "gcc_roaming_minutes" not in res["effective_weights"]


# ---------------------------------------------------------------- real data


def test_real_prepaid_catalogue_hayyak_plus():
    df = load_catalogue_csv(REPO / "data" / "omantel" / "PREPAID_PRODUCT_CATALOG.csv")
    assert "message_english" not in df.columns and "data_roaming" not in df.columns
    expected = {"8": 20, "11": 20, "13": 20, "16": 30, "21": 30}
    found = {}
    for _, r in df.iterrows():
        name = str(r["product_name"]).strip().lower()
        for n in expected:
            if name == f"hayyak plus {n}":
                found[n] = normalize_omantel_row(r.to_dict(), "PREPAID")
    assert set(found) == set(expected)
    for n, mins in expected.items():
        assert found[n]["gcc_roaming_minutes"] == mins
        assert found[n]["intl_roaming_data_gb"] == 0.0
