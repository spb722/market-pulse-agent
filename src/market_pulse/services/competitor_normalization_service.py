"""Step 1: competitor normalization and enrichment.

Productionized equivalent of ``reference/step1.py``. This module contains
only pure, deterministic logic (plan extraction and rule-based feature
derivation) -- no LLM calls are made here.

The functions below intentionally mirror the reference implementation's
behavior (including boundary conditions for bucketing/banding) exactly.
Do not change formulas, thresholds, or field names.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

RawPlan = dict[str, Any]
RawPayload = list[dict[str, Any]]


def extract_plans(payload: RawPayload, category: str) -> list[RawPlan]:
    """Extract the flat list of plans for a given category from a raw payload.

    ``payload`` is the raw crawler output: a list containing a single root
    object at index 0 (``payload[0]``).

    - "prepaid" -> root.master_plans + root.addon_plans
    - "postpaid" -> root.basic_plans + root.addon_plans

    Raises:
        ValueError: if ``category`` is neither "prepaid" nor "postpaid".
    """

    root = payload[0]

    if category == "prepaid":
        plans = root.get("master_plans", []) + root.get("addon_plans", [])
    elif category == "postpaid":
        plans = root.get("basic_plans", []) + root.get("addon_plans", [])
    else:
        raise ValueError(f"Unknown category: {category}")

    return plans


def validity_bucket(days: Optional[float]) -> str:
    """Bucket a validity duration (in days) into a coarse category."""

    if days is None:
        return "UNKNOWN"

    if days <= 1:
        return "DAILY"

    if days <= 7:
        return "WEEKLY"

    if 28 <= days <= 31:
        return "MONTHLY"

    if days > 31:
        return "LONG_TERM"

    return "OTHER"


def price_band(price: Optional[float]) -> str:
    """Bucket a price (in OMR) into a coarse commercial band."""

    if price is None:
        return "UNKNOWN"

    if price <= 5:
        return "0_5"

    if price <= 10:
        return "5_10"

    if price <= 20:
        return "10_20"

    if price <= 30:
        return "20_30"

    if price <= 50:
        return "30_50"

    return "50_PLUS"


def detect_market_segment(plan: RawPlan) -> str:
    """Rule-based market segment detection.

    Simple rule first. Later a crawler upgrade may provide this directly.
    """

    name = (plan.get("plan_name") or "").lower()
    source = str(plan.get("source_url") or "").lower()

    if "business" in name or "/b2b" in source:
        return "BUSINESS"

    return "CONSUMER"


def add_rule_features(plan: RawPlan) -> RawPlan:
    """Return a copy of ``plan`` enriched with deterministic rule-based features."""

    p = plan.copy()

    p["validity_bucket"] = validity_bucket(p.get("validity_days"))

    p["price_band"] = price_band(p.get("price_omr"))

    p["market_segment_rule"] = detect_market_segment(p)

    p["has_social_data"] = bool(p.get("social_pass_gb") and p.get("social_pass_gb") > 0)

    p["has_bonus_data"] = bool(p.get("bonus_data_gb") and p.get("bonus_data_gb") > 0)

    p["has_roaming"] = bool(
        p.get("roaming_included")
        or (p.get("roaming_data_gb") and p.get("roaming_data_gb") > 0)
    )

    p["has_idd"] = bool(p.get("intl_minutes") and p.get("intl_minutes") > 0)

    p["has_entertainment"] = bool(
        p.get("entertainment_gb")
        or "starz" in str(p.get("extra_benefits", "")).lower()
        or "entertainment" in str(p.get("extra_benefits", "")).lower()
    )

    # Deterministic commercial metric
    price = p.get("price_omr")
    data = p.get("data_gb")

    if price and data is not None:
        p["data_gb_per_omr"] = round(data / price, 3)
    else:
        p["data_gb_per_omr"] = None

    return p


def normalize_all_plans(prepaid_raw: RawPayload, postpaid_raw: RawPayload) -> list[RawPlan]:
    """Extract and rule-normalize all prepaid + postpaid plans.

    Pure, deterministic. Returns a flat list of normalized plan dicts.
    """

    prepaid_plans = extract_plans(prepaid_raw, "prepaid")
    postpaid_plans = extract_plans(postpaid_raw, "postpaid")

    all_plans = prepaid_plans + postpaid_plans

    normalized_plans = [add_rule_features(plan) for plan in all_plans]

    logger.info(
        "Normalized %d plans (prepaid=%d, postpaid=%d)",
        len(normalized_plans),
        len(prepaid_plans),
        len(postpaid_plans),
    )

    return normalized_plans


def run_competitor_normalization(
    prepaid_raw: RawPayload, postpaid_raw: RawPayload
) -> tuple[list[RawPlan], list[dict[str, Any]]]:
    """Thin Step 1 orchestration: deterministic normalization + LLM classification.

    Composes ``normalize_all_plans`` (pure/deterministic, this module) with
    the batch LLM classifier (``market_pulse.llm.plan_classifier``). This is
    the only function in this module that transitively triggers LLM calls;
    it delegates entirely to the isolated ``llm`` module rather than
    constructing any LLM client itself.

    Returns:
        (enriched_plans, errors) -- matching the reference notebook's
        ``enriched_plans`` / ``errors`` batch loop outputs.
    """

    from market_pulse.llm.plan_classifier import classify_plans

    normalized_plans = normalize_all_plans(prepaid_raw, postpaid_raw)

    return classify_plans(normalized_plans)
