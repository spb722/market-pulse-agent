"""Step 4: competitive gap analysis (competitor vs matched Omantel plan).

Productionized equivalent of ``reference/step4.py``. This step is purely
deterministic -- no LLM calls at all, so there is no ``llm/`` module for it.

Structural change from the reference (required for production): the
reference notebook closes over module-level globals ``ooredoo_plans`` and
``omantel_plans`` inside ``analyze_match``. A stateless service cannot rely
on notebook globals -- the competitor/Omantel plan sets differ per run -- so
both are threaded through explicitly as parameters wherever the reference
implicitly relied on the globals. No business behavior is changed by this.

Important: ``get_plan_role``/``get_product_type`` below are Step 4's OWN
copies, intentionally NOT reused from
``market_pulse.services.plan_matching_service`` (Step 3). The reference's
Step 4 versions guard ``plan.get("llm_enrichment", {})`` with ``or {}``
(so an explicit ``"llm_enrichment": None`` doesn't crash), while Step 3's
versions do not have that guard. This is a subtle, deliberate per-step
difference in the reference and must be preserved rather than unified.

The functions below intentionally mirror the reference implementation's
behavior (formulas, weights, thresholds, precedence order) exactly. Do not
change formulas, weights, thresholds, or gap/position logic.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Callable, Optional

from market_pulse.config.formula_config import GapAnalysisConfig, get_gap_analysis_config

logger = logging.getLogger(__name__)

Plan = dict[str, Any]

# ---------------------------------------------------------------------------
# Basic cleaning / normalization helpers
# ---------------------------------------------------------------------------


def clean_text(value: Any) -> str:
    """Coerce a raw value to a stripped string, or ``""`` for ``None``."""

    if value is None:
        return ""

    return str(value).strip()


def clean_number(value: Any) -> Optional[float]:
    """Coerce a raw value to a float, or ``None`` if unusable/NaN."""

    if value is None:
        return None

    try:
        value = float(value)

        if math.isnan(value):
            return None

        return value

    except Exception as exc:  # noqa: BLE001 - mirrors reference's defensive catch
        logger.debug("clean_number error: %s", exc)
        return None


def normalize_category(value: Any) -> str:
    """Normalize a raw category value to ``"prepaid"``/``"postpaid"``/``"unknown"``."""

    value = clean_text(value).lower()

    if value in ["prepaid", "postpaid"]:
        return value

    return "unknown"


# ---------------------------------------------------------------------------
# Classification helpers (three-tier precedence) -- Step 4's own copies.
# ---------------------------------------------------------------------------

_PLAN_ROLE_VALUES = ["MASTER", "BASE_PLAN", "ADDON"]

_RAW_TYPE_ROLE_MAP: dict[str, str] = {
    "MASTER": "MASTER",
    "BASIC_PLAN": "BASE_PLAN",
    "BASE_PLAN": "BASE_PLAN",
    "ADDON": "ADDON",
}


def get_plan_role(plan: Plan) -> str:
    """Resolve a plan's role using the reference's exact 3-tier precedence.

    Note the ``or {}`` guard on ``llm_enrichment`` -- this is Step 4's own
    copy and intentionally differs from Step 3's version (see module
    docstring).
    """

    role = clean_text(plan.get("plan_role")).upper()

    if role in _PLAN_ROLE_VALUES:
        return role

    raw_type = clean_text(plan.get("type")).upper()

    if raw_type in _RAW_TYPE_ROLE_MAP:
        return _RAW_TYPE_ROLE_MAP[raw_type]

    llm = plan.get("llm_enrichment", {}) or {}

    llm_role = clean_text(llm.get("plan_role")).upper()

    if llm_role in _PLAN_ROLE_VALUES:
        return llm_role

    return "UNKNOWN"


VALID_PRODUCT_TYPES = {"COMBO", "DATA", "VOICE", "IDD", "ROAMING", "SMS"}


def get_product_type(plan: Plan) -> str:
    """Resolve a plan's product type, preferring the structured field over the LLM.

    Note the ``or {}`` guard on ``llm_enrichment`` -- see module docstring.
    """

    source_type = clean_text(plan.get("product_type")).upper()

    if source_type in VALID_PRODUCT_TYPES:
        return source_type

    llm = plan.get("llm_enrichment", {}) or {}

    semantic = clean_text(
        llm.get("semantic_product_type") or llm.get("product_type")
    ).upper()

    if semantic in VALID_PRODUCT_TYPES:
        return semantic

    return "OTHER"


# ---------------------------------------------------------------------------
# Plan lookup
# ---------------------------------------------------------------------------


def find_plan(
    plans: list[Plan],
    plan_id: Any = None,
    plan_name: Any = None,
) -> Optional[Plan]:
    """Resolve a full plan record by id/name, preferring the most specific
    unambiguous match.

    Precedence (each tier only used if it yields exactly one match):
    1. id + name (both must match)
    2. id alone
    3. name alone

    An ambiguous match at any tier (2+ results) falls through instead of
    picking arbitrarily; if no tier yields a unique match, returns ``None``.
    """

    plan_id_str = str(plan_id) if plan_id is not None else None
    plan_name_str = clean_text(plan_name)

    # Best option: ID + name
    if plan_id_str and plan_name_str:
        exact = [
            p
            for p in plans
            if str(p.get("plan_id")) == plan_id_str
            and clean_text(p.get("plan_name")) == plan_name_str
        ]

        if len(exact) == 1:
            return exact[0]

    # ID only, but only if unique
    if plan_id_str:
        by_id = [p for p in plans if str(p.get("plan_id")) == plan_id_str]

        if len(by_id) == 1:
            return by_id[0]

    # Name only, but only if unique
    if plan_name_str:
        by_name = [p for p in plans if clean_text(p.get("plan_name")) == plan_name_str]

        if len(by_name) == 1:
            return by_name[0]

    return None


# ---------------------------------------------------------------------------
# Text / unlimited-keyword helpers
# ---------------------------------------------------------------------------


def plan_text(plan: Plan) -> str:
    parts = [
        plan.get("plan_name"),
        plan.get("extra_benefits"),
        plan.get("message_english"),
    ]

    return " ".join(clean_text(x) for x in parts).lower()


def has_unlimited_data(plan: Plan) -> bool:
    if plan.get("unlimited_data") is True:
        return True

    text = plan_text(plan)

    keywords = [
        "unlimited data",
        "unlimited local internet",
        "unlimited internet",
    ]

    return any(keyword in text for keyword in keywords)


def has_unlimited_voice(plan: Plan) -> bool:
    if plan.get("unlimited_calls") is True:
        return True

    text = plan_text(plan)

    keywords = [
        "unlimited local minutes",
        "unlimited calls",
        "unlimited minutes",
    ]

    return any(keyword in text for keyword in keywords)


def has_unlimited_sms(plan: Plan) -> bool:
    if plan.get("unlimited_sms") is True:
        return True

    text = plan_text(plan)

    keywords = [
        "unlimited sms",
        "unlimited national sms",
    ]

    return any(keyword in text for keyword in keywords)


# ---------------------------------------------------------------------------
# Metric value getters
# ---------------------------------------------------------------------------


def get_data_value(plan: Plan) -> Optional[float]:
    product_type = get_product_type(plan)

    if product_type == "ROAMING":
        roaming_data = clean_number(plan.get("roaming_data_gb"))

        if roaming_data is not None:
            return roaming_data

    return clean_number(plan.get("data_gb"))


def get_voice_value(plan: Plan) -> Optional[float]:
    return clean_number(plan.get("voice_minutes"))


def get_idd_value(plan: Plan) -> Optional[float]:
    value = clean_number(plan.get("intl_minutes"))

    # Some IDD products store their minutes inside voice_minutes.
    if value is None and get_product_type(plan) == "IDD":
        value = clean_number(plan.get("voice_minutes"))

    return value


def get_sms_value(plan: Plan) -> Optional[float]:
    return clean_number(plan.get("sms_count"))


# ---------------------------------------------------------------------------
# Gap-scoring core
# ---------------------------------------------------------------------------
#
# The parity threshold, overall-position threshold, and per-product-type
# metric weights used to be hardcoded module constants here (``PARITY_
# THRESHOLD``/``WEIGHTS``, matching reference/step4.py exactly). They now
# live solely in ``config/risk_scoring.yaml`` (see
# ``market_pulse.config.formula_config``), to avoid two sources of truth
# that could drift apart. Every function below that needs one of these
# values takes an optional ``config: Optional[GapAnalysisConfig] = None``
# parameter; when not supplied, it resolves from the shared cached config.


def bounded_advantage(
    competitor: Any,
    omantel: Any,
    lower_is_better: bool = False,
) -> Optional[float]:
    """Normalized advantage in ``[-1, 1]``: positive favors Omantel."""

    competitor = clean_number(competitor)
    omantel = clean_number(omantel)

    if competitor is None or omantel is None:
        return None

    if competitor == 0 and omantel == 0:
        return 0.0

    denominator = max(abs(competitor), abs(omantel))

    if denominator == 0:
        return 0.0

    # Price: lower = better
    if lower_is_better:
        score = (competitor - omantel) / denominator

    # Data/minutes/etc: higher = better
    else:
        score = (omantel - competitor) / denominator

    return max(-1.0, min(1.0, score))


def percentage_gap(competitor: Any, omantel: Any) -> Optional[float]:
    competitor = clean_number(competitor)
    omantel = clean_number(omantel)

    if competitor is None or omantel is None or competitor == 0:
        return None

    return round(((omantel - competitor) / competitor) * 100, 2)


def get_position(
    normalized_advantage: Optional[float],
    config: Optional[GapAnalysisConfig] = None,
) -> str:
    config = config or get_gap_analysis_config()

    if normalized_advantage is None:
        return "NOT_SCORED"

    if abs(normalized_advantage) <= config.parity_threshold:
        return "PARITY"

    if normalized_advantage > 0:
        return "OMANTEL_ADVANTAGE"

    return "COMPETITOR_ADVANTAGE"


def finite_metric_gap(
    competitor: Any,
    omantel: Any,
    lower_is_better: bool = False,
    config: Optional[GapAnalysisConfig] = None,
) -> dict[str, Any]:
    competitor = clean_number(competitor)
    omantel = clean_number(omantel)

    if competitor is None or omantel is None:
        return {
            "competitor": competitor,
            "omantel": omantel,
            "difference": None,
            "gap_pct": None,
            "normalized_advantage": None,
            "position": "NOT_SCORED",
        }

    advantage = bounded_advantage(competitor, omantel, lower_is_better)

    return {
        "competitor": competitor,
        "omantel": omantel,
        # Always: Omantel - competitor
        "difference": round(omantel - competitor, 4),
        "gap_pct": percentage_gap(competitor, omantel),
        "normalized_advantage": round(advantage, 4),
        "position": get_position(advantage, config=config),
    }


def unlimited_metric_gap(
    competitor_plan: Plan,
    omantel_plan: Plan,
    value_getter: Callable[[Plan], Optional[float]],
    unlimited_getter: Callable[[Plan], bool],
    config: Optional[GapAnalysisConfig] = None,
) -> dict[str, Any]:
    competitor_unlimited = unlimited_getter(competitor_plan)
    omantel_unlimited = unlimited_getter(omantel_plan)

    competitor_value = value_getter(competitor_plan)
    omantel_value = value_getter(omantel_plan)

    # Both unlimited
    if competitor_unlimited and omantel_unlimited:
        return {
            "competitor": "UNLIMITED",
            "omantel": "UNLIMITED",
            "difference": None,
            "gap_pct": None,
            "normalized_advantage": 0.0,
            "position": "PARITY",
        }

    # Competitor unlimited
    if competitor_unlimited and not omantel_unlimited:
        return {
            "competitor": "UNLIMITED",
            "omantel": omantel_value,
            "difference": None,
            "gap_pct": None,
            "normalized_advantage": -1.0,
            "position": "COMPETITOR_ADVANTAGE",
        }

    # Omantel unlimited
    if omantel_unlimited and not competitor_unlimited:
        return {
            "competitor": competitor_value,
            "omantel": "UNLIMITED",
            "difference": None,
            "gap_pct": None,
            "normalized_advantage": 1.0,
            "position": "OMANTEL_ADVANTAGE",
        }

    # Both finite
    return finite_metric_gap(competitor_value, omantel_value, config=config)


def validity_gap(
    competitor_plan: Plan,
    omantel_plan: Plan,
    config: Optional[GapAnalysisConfig] = None,
) -> dict[str, Any]:
    if (
        normalize_category(competitor_plan.get("category")) == "postpaid"
        and normalize_category(omantel_plan.get("category")) == "postpaid"
        and get_plan_role(competitor_plan) == "BASE_PLAN"
        and get_plan_role(omantel_plan) == "BASE_PLAN"
    ):
        return {
            "competitor": clean_number(competitor_plan.get("validity_days")),
            "omantel": clean_number(omantel_plan.get("validity_days")),
            "difference": None,
            "gap_pct": None,
            "normalized_advantage": None,
            "position": "NOT_SCORED",
            "note": "Recurring postpaid base plans",
        }

    return finite_metric_gap(
        competitor_plan.get("validity_days"),
        omantel_plan.get("validity_days"),
        config=config,
    )


def build_metric_gaps(
    competitor_plan: Plan,
    omantel_plan: Plan,
    config: Optional[GapAnalysisConfig] = None,
) -> dict[str, Any]:
    return {
        "price": finite_metric_gap(
            competitor_plan.get("price_omr"),
            omantel_plan.get("price_omr"),
            lower_is_better=True,
            config=config,
        ),
        "data": unlimited_metric_gap(
            competitor_plan, omantel_plan, get_data_value, has_unlimited_data, config=config
        ),
        "voice": unlimited_metric_gap(
            competitor_plan, omantel_plan, get_voice_value, has_unlimited_voice, config=config
        ),
        "idd": finite_metric_gap(
            get_idd_value(competitor_plan),
            get_idd_value(omantel_plan),
            config=config,
        ),
        "sms": unlimited_metric_gap(
            competitor_plan, omantel_plan, get_sms_value, has_unlimited_sms, config=config
        ),
        "validity": validity_gap(competitor_plan, omantel_plan, config=config),
    }


def compute_weighted_position(
    competitor_plan: Plan,
    metric_gaps: dict[str, Any],
    config: Optional[GapAnalysisConfig] = None,
) -> dict[str, Any]:
    config = config or get_gap_analysis_config()

    product_type = get_product_type(competitor_plan)

    base_weights = config.weights.get(product_type, config.weights["OTHER"])

    # Only dimensions that actually have reliable values
    available_weights = {
        metric: weight
        for metric, weight in base_weights.items()
        if metric_gaps.get(metric, {}).get("normalized_advantage") is not None
    }

    if not available_weights:
        return {
            "product_type": product_type,
            "commercial_position_score": None,
            "overall_position": "NOT_SCORED",
            "effective_weights": {},
            "weighted_contributions": {},
        }

    total_weight = sum(available_weights.values())

    # Re-normalize to 100%
    effective_weights = {
        metric: weight / total_weight for metric, weight in available_weights.items()
    }

    contributions = {}

    for metric, weight in effective_weights.items():
        advantage = metric_gaps[metric]["normalized_advantage"]
        contributions[metric] = advantage * weight * 100

    final_score = sum(contributions.values())

    if final_score > config.overall_position_threshold:
        overall = "OMANTEL_ADVANTAGE"
    elif final_score < -config.overall_position_threshold:
        overall = "COMPETITOR_ADVANTAGE"
    else:
        overall = "BALANCED"

    return {
        "product_type": product_type,
        "commercial_position_score": round(final_score, 2),
        "overall_position": overall,
        "effective_weights": {
            metric: round(weight, 4) for metric, weight in effective_weights.items()
        },
        "weighted_contributions": {
            metric: round(value, 2) for metric, value in contributions.items()
        },
    }


def capability_gaps_from_match(match_record: dict[str, Any]) -> list[dict[str, Any]]:
    results = []

    for item in match_record.get("capability_insights", []) or []:
        addon_exists = bool(item.get("separate_omantel_offer_exists"))

        if addon_exists:
            status = "NATIVE_GAP_ADDON_EXISTS"
        else:
            status = "NATIVE_GAP_NO_ADDON_FOUND"

        results.append(
            {
                "capability": item.get("capability"),
                "position": "COMPETITOR_ADVANTAGE",
                "status": status,
                "separate_omantel_offer_exists": addon_exists,
                "separate_omantel_offers": item.get("separate_omantel_offers", []),
            }
        )

    return results


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def analyze_match(
    match_record: dict[str, Any],
    competitor_plans: list[Plan],
    omantel_plans: list[Plan],
    config: Optional[GapAnalysisConfig] = None,
) -> dict[str, Any]:
    """Analyze the competitive gaps for a single Step 3 match record.

    ``competitor_plans``/``omantel_plans`` are threaded through explicitly
    rather than closed over from notebook globals -- see module docstring.
    """

    config = config or get_gap_analysis_config()

    result: dict[str, Any] = {
        "competitor_plan_id": match_record.get("competitor_plan_id"),
        "competitor_plan": match_record.get("competitor_plan_name"),
        "category": match_record.get("category"),
        "plan_role": match_record.get("plan_role"),
        "product_type": match_record.get("product_type"),
        "step3_match_status": match_record.get("match_status"),
        "match_confidence": match_record.get("match_confidence"),
    }

    # Don't force analysis where Step 3 did not find a match.
    if match_record.get("match_status") != "MATCHED" or not match_record.get(
        "selected_match"
    ):
        result.update(
            {
                "gap_analysis_status": "NOT_ANALYZED",
                "reason": "No matched Omantel plan from Step 3.",
            }
        )

        return result

    selected = match_record["selected_match"]

    # Full Step 1 competitor record
    competitor_plan = find_plan(
        competitor_plans,
        match_record.get("competitor_plan_id"),
        match_record.get("competitor_plan_name"),
    )

    # Full Step 2 Omantel record
    omantel_plan = find_plan(
        omantel_plans,
        selected.get("omantel_plan_id"),
        selected.get("omantel_plan_name"),
    )

    if competitor_plan is None or omantel_plan is None:
        result.update(
            {
                "gap_analysis_status": "REVIEW_REQUIRED",
                "reason": "Could not uniquely resolve the full Step 1 or Step 2 plan record.",
            }
        )

        return result

    metric_gaps = build_metric_gaps(competitor_plan, omantel_plan, config=config)

    weighted_position = compute_weighted_position(competitor_plan, metric_gaps, config=config)

    result.update(
        {
            "omantel_plan_id": omantel_plan.get("plan_id"),
            "omantel_plan": omantel_plan.get("plan_name"),
            "similarity_score": selected.get("similarity_score"),
            "gap_analysis_status": "ANALYZED",
            "metric_gaps": metric_gaps,
            "weighted_position": weighted_position,
            "capability_gaps": capability_gaps_from_match(match_record),
        }
    )

    return result


def analyze_matches(
    step3_matches: list[dict[str, Any]],
    competitor_plans: list[Plan],
    omantel_plans: list[Plan],
    config: Optional[GapAnalysisConfig] = None,
) -> list[dict[str, Any]]:
    """Analyze a batch of Step 3 match records.

    A single match record's analysis failure is isolated: it is recorded as
    a ``PROCESSING_ERROR`` result rather than aborting the whole batch,
    mirroring the reference's per-item ``try/except`` in its main loop.
    """

    config = config or get_gap_analysis_config()

    results: list[dict[str, Any]] = []

    for index, match_record in enumerate(step3_matches, start=1):
        logger.debug(
            "Analyzing gap %d/%d: %s",
            index,
            len(step3_matches),
            match_record.get("competitor_plan_name"),
        )

        try:
            result = analyze_match(match_record, competitor_plans, omantel_plans, config=config)

        except Exception as exc:  # noqa: BLE001 - intentional isolation boundary
            plan_name = match_record.get("competitor_plan_name")
            logger.warning("Gap analysis failed for %r: %s", plan_name, exc)

            result = {
                "competitor_plan_id": match_record.get("competitor_plan_id"),
                "competitor_plan": plan_name,
                "gap_analysis_status": "PROCESSING_ERROR",
                "error": str(exc),
            }

        results.append(result)

    logger.info("Gap analysis complete: %d matches processed", len(results))

    return results


# ---------------------------------------------------------------------------
# JSON-safety utility
# ---------------------------------------------------------------------------


def make_json_safe(value: Any) -> Any:
    """Recursively replace NaN floats with ``None`` so results are always
    valid JSON (``json.dumps(..., allow_nan=False)`` safe)."""

    if isinstance(value, dict):
        return {key: make_json_safe(val) for key, val in value.items()}

    if isinstance(value, list):
        return [make_json_safe(x) for x in value]

    if isinstance(value, float):
        if math.isnan(value):
            return None

    return value
