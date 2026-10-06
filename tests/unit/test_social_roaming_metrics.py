"""Tests for the social_data / roaming gap metrics and Step 2's new columns."""

from __future__ import annotations

import pytest

from market_pulse.config.formula_config import get_gap_analysis_config
from market_pulse.schemas.omantel import NormalizedOmantelPlan
from market_pulse.services.gap_analysis_service import (
    build_metric_gaps,
    compute_weighted_position,
    get_roaming_value,
)
from market_pulse.services.omantel_normalization_service import normalize_omantel_row


def _plan(**kw):
    base = {"product_type": "COMBO", "category": "prepaid"}
    base.update(kw)
    return base


def _row(**kw):
    base = {
        "type": "Master",
        "product_type": "COMBO",
        "product_id": 1,
        "product_name": "Plan",
        "price": 5,
        "validity_in_days": 30,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# Step 2
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("category", ["PREPAID", "POSTPAID"])
def test_step2_zero_stays_zero_blank_is_none(category):
    zero = normalize_omantel_row(_row(data_social=0, data_roaming=0), category)
    assert zero["social_pass_gb"] == 0.0
    assert zero["roaming_data_gb"] == 0.0

    blank = normalize_omantel_row(
        _row(data_social=float("nan"), data_roaming=None), category
    )
    assert blank["social_pass_gb"] is None
    assert blank["roaming_data_gb"] is None

    val = normalize_omantel_row(_row(data_social=2.5, data_roaming=1), category)
    assert val["social_pass_gb"] == 2.5
    assert val["roaming_data_gb"] == 1.0


def test_step2_extra_benefits_passthrough_and_missing_columns():
    r = normalize_omantel_row(_row(extra_benefits="A; B"), "POSTPAID")
    assert r["extra_benefits"] == "A; B"

    assert normalize_omantel_row(_row(extra_benefits=""), "PREPAID")["extra_benefits"] is None
    assert normalize_omantel_row(_row(extra_benefits=float("nan")), "PREPAID")["extra_benefits"] is None

    missing = normalize_omantel_row(_row(), "PREPAID")
    assert missing["social_pass_gb"] is None
    assert missing["roaming_data_gb"] is None
    assert missing["extra_benefits"] is None


def test_normalized_plan_schema_accepts_new_fields():
    row = normalize_omantel_row(
        _row(data_social=0, data_roaming=3, extra_benefits="X"), "PREPAID"
    )
    plan = NormalizedOmantelPlan(**row)
    assert plan.roaming_data_gb == 3.0
    assert plan.social_pass_gb == 0.0
    assert plan.extra_benefits == "X"


# ---------------------------------------------------------------------------
# Step 4 metrics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key,metric", [("social_pass_gb", "social_data"), ("roaming_data_gb", "roaming")]
)
def test_zero_vs_value_and_blank(key, metric):
    gaps = build_metric_gaps(_plan(**{key: 25}), _plan(**{key: 0}))
    assert gaps[metric]["position"] == "COMPETITOR_ADVANTAGE"
    assert gaps[metric]["normalized_advantage"] == -1.0

    for comp, om in [(None, 0), (25, None), (None, None)]:
        gaps = build_metric_gaps(_plan(**{key: comp}), _plan(**{key: om}))
        assert gaps[metric]["position"] == "NOT_SCORED"
        assert gaps[metric]["normalized_advantage"] is None


def test_roam_like_home_uses_data_gb():
    comp = _plan(roaming_included=True, data_gb=10, roaming_data_gb=None)
    assert get_roaming_value(comp) == 10.0

    gaps = build_metric_gaps(comp, _plan(roaming_data_gb=5))
    assert gaps["roaming"]["competitor"] == 10.0
    assert gaps["roaming"]["position"] == "COMPETITOR_ADVANTAGE"

    # Explicit roaming GB wins over data_gb; flag false -> no fallback.
    assert get_roaming_value(_plan(roaming_included=True, data_gb=10, roaming_data_gb=2)) == 2.0
    assert get_roaming_value(_plan(roaming_included=False, data_gb=10)) is None


def test_roaming_product_not_scored_for_roaming_metric():
    comp = _plan(product_type="ROAMING", roaming_data_gb=5, data_gb=5)
    om = _plan(product_type="ROAMING", roaming_data_gb=1, data_gb=1)

    gaps = build_metric_gaps(comp, om)

    assert gaps["roaming"]["position"] == "NOT_SCORED"
    assert gaps["roaming"]["normalized_advantage"] is None
    assert gaps["roaming"]["note"]
    # Data metric still compares the roaming data.
    assert gaps["data"]["position"] == "COMPETITOR_ADVANTAGE"


# ---------------------------------------------------------------------------
# Weighted position
# ---------------------------------------------------------------------------


def _combo_pair(**extra):
    comp = _plan(price_omr=10, data_gb=10, voice_minutes=100, intl_minutes=10,
                 validity_days=30, **extra.get("comp", {}))
    om = _plan(price_omr=8, data_gb=5, voice_minutes=200, intl_minutes=20,
               validity_days=30, **extra.get("om", {}))
    return comp, om


def test_combo_regression_when_new_metrics_not_scored():
    config = get_gap_analysis_config()
    comp, om = _combo_pair()
    gaps = build_metric_gaps(comp, om)
    assert gaps["social_data"]["position"] == "NOT_SCORED"

    result = compute_weighted_position(comp, gaps, config=config)

    old_weights = {"price": 0.30, "data": 0.30, "voice": 0.20, "idd": 0.10, "validity": 0.10}
    expected = sum(
        gaps[m]["normalized_advantage"] * w for m, w in old_weights.items()
    ) * 100
    assert result["commercial_position_score"] == round(expected, 2)
    assert set(result["effective_weights"]) == set(old_weights)


def test_combo_new_metrics_participate_with_effective_weight():
    config = get_gap_analysis_config()
    comp, om = _combo_pair(
        comp={"social_pass_gb": 10, "roaming_data_gb": 10},
        om={"social_pass_gb": 0, "roaming_data_gb": 0},
    )
    gaps = build_metric_gaps(comp, om)

    result = compute_weighted_position(comp, gaps, config=config)

    assert result["effective_weights"]["social_data"] == round(0.05 / 1.10, 4)
    assert result["effective_weights"]["roaming"] == round(0.05 / 1.10, 4)
    assert result["weighted_contributions"]["social_data"] == round(-1.0 * 0.05 / 1.10 * 100, 2)
