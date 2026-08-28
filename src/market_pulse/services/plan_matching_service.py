"""Step 3: competitor <-> Omantel comparable-plan matching + capability insights.

Productionized equivalent of ``reference/step3.py``. This module contains
all deterministic logic (structured similarity scoring, category/role/type
candidate filtering, capability-gap detection) plus the orchestration that
wires in the LLM matching decision (``market_pulse.llm.plan_matcher``). No
LLM client is constructed here.

Structural change from the reference (required for production): the
reference notebook closes over a module-level global ``omantel_plans`` list
from ``find_separate_omantel_addons``, ``attach_capability_insights``, and
the main per-competitor loop. A stateless service cannot rely on notebook
globals -- the Omantel reference plan set differs per run -- so
``omantel_plans`` is threaded through explicitly as a parameter everywhere
the reference implicitly relied on the global. No business behavior is
changed by this.

The functions below intentionally mirror the reference implementation's
behavior (formulas, weights, thresholds, precedence order) exactly. Do not
change formulas, weights, thresholds, or matching rules.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Optional

from market_pulse.llm.plan_matcher import decide_match

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

    if value == "prepaid":
        return "prepaid"

    if value == "postpaid":
        return "postpaid"

    return "unknown"


# ---------------------------------------------------------------------------
# Classification helpers (three-tier precedence)
# ---------------------------------------------------------------------------

_PLAN_ROLE_VALUES = ("MASTER", "BASE_PLAN", "ADDON")

# Step 1 Ooredoo raw crawler ``type`` -> normalized plan_role
_RAW_TYPE_ROLE_MAP: dict[str, str] = {
    "MASTER": "MASTER",
    "BASIC_PLAN": "BASE_PLAN",
    "BASE_PLAN": "BASE_PLAN",
    "ADDON": "ADDON",
}


def get_plan_role(plan: Plan) -> str:
    """Resolve a plan's role using the reference's exact 3-tier precedence.

    1. Step 2 Omantel structured ``plan_role`` field.
    2. Step 1 Ooredoo raw crawler ``type`` field, mapped.
    3. LLM enrichment ``plan_role`` fallback (only used when 1 and 2 are
       both unavailable/invalid).
    4. ``"UNKNOWN"`` otherwise.
    """

    # Step 2 Omantel
    role = clean_text(plan.get("plan_role")).upper()

    if role in _PLAN_ROLE_VALUES:
        return role

    # Step 1 Ooredoo raw crawler type
    raw_type = clean_text(plan.get("type")).upper()

    if raw_type in _RAW_TYPE_ROLE_MAP:
        return _RAW_TYPE_ROLE_MAP[raw_type]

    # Only use LLM as fallback
    llm = plan.get("llm_enrichment", {}) or {}

    llm_role = clean_text(llm.get("plan_role")).upper()

    if llm_role in _PLAN_ROLE_VALUES:
        return llm_role

    return "UNKNOWN"


VALID_PRODUCT_TYPES = {"COMBO", "DATA", "VOICE", "IDD", "ROAMING", "SMS"}


def get_product_type(plan: Plan) -> str:
    """Resolve a plan's product type, preferring the structured field over the LLM."""

    # Prefer normalized structured product_type
    source_type = clean_text(plan.get("product_type")).upper()

    if source_type in VALID_PRODUCT_TYPES:
        return source_type

    # Only use LLM when structured type is unavailable
    llm = plan.get("llm_enrichment", {}) or {}

    semantic = clean_text(
        llm.get("semantic_product_type") or llm.get("product_type")
    ).upper()

    if semantic in VALID_PRODUCT_TYPES:
        return semantic

    return "OTHER"


def has_unlimited_data(plan: Plan) -> bool:
    """Detect unlimited-data plans via explicit flag or free-text keywords."""

    if plan.get("unlimited_data") is True:
        return True

    text = " ".join(
        [
            clean_text(plan.get("plan_name")),
            clean_text(plan.get("extra_benefits")),
            clean_text(plan.get("message_english")),
        ]
    ).lower()

    keywords = [
        "unlimited data",
        "unlimited local internet",
        "unlimited internet",
    ]

    return any(keyword in text for keyword in keywords)


def has_unlimited_voice(plan: Plan) -> bool:
    """Detect unlimited-voice plans via explicit flag or free-text keywords."""

    if plan.get("unlimited_calls") is True:
        return True

    text = " ".join(
        [
            clean_text(plan.get("plan_name")),
            clean_text(plan.get("extra_benefits")),
            clean_text(plan.get("message_english")),
        ]
    ).lower()

    keywords = [
        "unlimited local minutes",
        "unlimited calls",
        "unlimited minutes",
    ]

    return any(keyword in text for keyword in keywords)


# ---------------------------------------------------------------------------
# Similarity scoring
# ---------------------------------------------------------------------------


def numeric_similarity(a: Any, b: Any) -> Optional[float]:
    """Symmetric relative-difference similarity in ``[0, 1]``, or ``None``."""

    a = clean_number(a)
    b = clean_number(b)

    if a is None or b is None:
        return None

    if a == 0 and b == 0:
        return 1.0

    maximum = max(abs(a), abs(b))

    if maximum == 0:
        return 1.0

    difference = abs(a - b)

    score = 1 - (difference / maximum)

    return max(0.0, min(1.0, score))


def data_similarity(comp: Plan, oman: Plan) -> Optional[float]:
    comp_unlimited = has_unlimited_data(comp)
    oman_unlimited = has_unlimited_data(oman)

    if comp_unlimited and oman_unlimited:
        return 1.0

    if comp_unlimited != oman_unlimited:
        return 0.0

    return numeric_similarity(comp.get("data_gb"), oman.get("data_gb"))


def voice_similarity(comp: Plan, oman: Plan) -> Optional[float]:
    comp_unlimited = has_unlimited_voice(comp)
    oman_unlimited = has_unlimited_voice(oman)

    if comp_unlimited and oman_unlimited:
        return 1.0

    if comp_unlimited != oman_unlimited:
        return 0.0

    return numeric_similarity(comp.get("voice_minutes"), oman.get("voice_minutes"))


def idd_similarity(comp: Plan, oman: Plan) -> Optional[float]:
    comp_minutes = clean_number(comp.get("intl_minutes"))
    oman_minutes = clean_number(oman.get("intl_minutes"))

    # fallback when IDD is stored in generic voice field
    if comp_minutes is None:
        comp_minutes = clean_number(comp.get("voice_minutes"))

    if oman_minutes is None:
        oman_minutes = clean_number(oman.get("voice_minutes"))

    return numeric_similarity(comp_minutes, oman_minutes)


def sms_similarity(comp: Plan, oman: Plan) -> Optional[float]:
    comp_unlimited = comp.get("unlimited_sms") is True
    oman_unlimited = oman.get("unlimited_sms") is True

    if comp_unlimited and oman_unlimited:
        return 1.0

    if comp_unlimited != oman_unlimited:
        return 0.0

    return numeric_similarity(comp.get("sms_count"), oman.get("sms_count"))


def validity_similarity(comp: Plan, oman: Plan) -> Optional[float]:
    comp_category = normalize_category(comp.get("category"))
    oman_category = normalize_category(oman.get("category"))

    comp_role = get_plan_role(comp)
    oman_role = get_plan_role(oman)

    # Both are recurring postpaid base plans.
    # Do not punish Omantel for validity_days = 0.
    if (
        comp_category == "postpaid"
        and oman_category == "postpaid"
        and comp_role == "BASE_PLAN"
        and oman_role == "BASE_PLAN"
    ):
        return 1.0

    return numeric_similarity(comp.get("validity_days"), oman.get("validity_days"))


def weighted_score(metrics: list[tuple[Optional[float], float]]) -> float:
    """Weighted average of the ``(score, weight)`` pairs with a score available."""

    available = [(score, weight) for score, weight in metrics if score is not None]

    if not available:
        return 0.0

    total_weight = sum(weight for _, weight in available)

    score = sum(value * weight for value, weight in available) / total_weight

    return round(score, 4)


def calculate_similarity(comp: Plan, oman: Plan) -> dict[str, Optional[float]]:
    """Compute the weighted structured similarity between a competitor and Omantel plan.

    The weight set used depends on the competitor plan's category/role/
    product_type, branching in the exact order below. Do not reorder or
    change the weights -- see the reference for the documented rationale
    (e.g. postpaid BASE_PLAN intentionally excludes a voice component due
    to a known Step 2 data-completeness limitation).
    """

    category = normalize_category(comp.get("category"))
    role = get_plan_role(comp)
    product_type = get_product_type(comp)

    price_sim = numeric_similarity(comp.get("price_omr"), oman.get("price_omr"))
    data_sim = data_similarity(comp, oman)
    voice_sim = voice_similarity(comp, oman)
    idd_sim = idd_similarity(comp, oman)
    sms_sim = sms_similarity(comp, oman)
    validity_sim = validity_similarity(comp, oman)

    # Known Step 2 limitation:
    # some postpaid base-plan voice values are incomplete.
    if category == "postpaid" and role == "BASE_PLAN":
        final_score = weighted_score(
            [
                (price_sim, 0.45),
                (data_sim, 0.45),
                (validity_sim, 0.10),
            ]
        )

    elif product_type == "DATA":
        final_score = weighted_score(
            [
                (price_sim, 0.45),
                (data_sim, 0.45),
                (validity_sim, 0.10),
            ]
        )

    elif product_type == "VOICE":
        final_score = weighted_score(
            [
                (price_sim, 0.40),
                (voice_sim, 0.50),
                (validity_sim, 0.10),
            ]
        )

    elif product_type == "IDD":
        final_score = weighted_score(
            [
                (price_sim, 0.40),
                (idd_sim, 0.50),
                (validity_sim, 0.10),
            ]
        )

    elif product_type == "SMS":
        final_score = weighted_score(
            [
                (price_sim, 0.40),
                (sms_sim, 0.50),
                (validity_sim, 0.10),
            ]
        )

    else:
        # COMBO / ROAMING / OTHER
        final_score = weighted_score(
            [
                (price_sim, 0.35),
                (data_sim, 0.35),
                (voice_sim, 0.20),
                (validity_sim, 0.10),
            ]
        )

    return {
        "similarity_score": final_score,
        "price_similarity": price_sim,
        "data_similarity": data_sim,
        "voice_similarity": voice_sim,
        "idd_similarity": idd_sim,
        "sms_similarity": sms_sim,
        "validity_similarity": validity_sim,
    }


# ---------------------------------------------------------------------------
# Candidate filtering / ranking
# ---------------------------------------------------------------------------


def get_candidates(competitor_plan: Plan, omantel_plans: list[Plan]) -> list[Plan]:
    """Strict category/role/product-type gate before any similarity scoring."""

    comp_category = normalize_category(competitor_plan.get("category"))
    comp_role = get_plan_role(competitor_plan)
    comp_type = get_product_type(competitor_plan)

    candidates = []

    for oman in omantel_plans:
        oman_category = normalize_category(oman.get("category"))
        oman_role = get_plan_role(oman)
        oman_type = get_product_type(oman)

        # PREPAID <-> PREPAID / POSTPAID <-> POSTPAID
        if oman_category != comp_category:
            continue

        # MASTER <-> MASTER / BASE_PLAN <-> BASE_PLAN / ADDON <-> ADDON
        if oman_role != comp_role:
            continue

        # DATA <-> DATA / COMBO <-> COMBO etc.
        if oman_type != comp_type:
            continue

        candidates.append(oman)

    return candidates


def find_top_candidates(
    competitor_plan: Plan, omantel_plans: list[Plan], top_n: int = 3
) -> list[dict[str, Any]]:
    """Score and rank the filtered candidates, returning the top ``top_n``."""

    candidates = get_candidates(competitor_plan, omantel_plans)

    scored = []

    for oman in candidates:
        score = calculate_similarity(competitor_plan, oman)

        scored.append(
            {
                "omantel_plan_id": oman.get("plan_id"),
                "omantel_plan_name": oman.get("plan_name"),
                "category": normalize_category(oman.get("category")),
                "plan_role": get_plan_role(oman),
                "product_type": get_product_type(oman),
                "price_omr": oman.get("price_omr"),
                "data_gb": oman.get("data_gb"),
                "voice_minutes": oman.get("voice_minutes"),
                "intl_minutes": oman.get("intl_minutes"),
                "validity_days": oman.get("validity_days"),
                "unlimited_data": has_unlimited_data(oman),
                "unlimited_calls": has_unlimited_voice(oman),
                **score,
            }
        )

    scored = sorted(scored, key=lambda x: x["similarity_score"], reverse=True)

    return scored[:top_n]


# ---------------------------------------------------------------------------
# Compact plan payload (for the LLM)
# ---------------------------------------------------------------------------


def compact_plan(plan: Plan) -> dict[str, Any]:
    """Build the compact competitor-plan payload passed to the LLM matcher."""

    return {
        "plan_id": plan.get("plan_id"),
        "plan_name": plan.get("plan_name"),
        "category": normalize_category(plan.get("category")),
        "plan_role": get_plan_role(plan),
        "product_type": get_product_type(plan),
        "price_omr": clean_number(plan.get("price_omr")),
        "data_gb": clean_number(plan.get("data_gb")),
        "voice_minutes": clean_number(plan.get("voice_minutes")),
        "intl_minutes": clean_number(plan.get("intl_minutes")),
        "validity_days": clean_number(plan.get("validity_days")),
        "unlimited_data": has_unlimited_data(plan),
        "unlimited_calls": has_unlimited_voice(plan),
    }


# ---------------------------------------------------------------------------
# Capability-gap insights
# ---------------------------------------------------------------------------


def plan_text(plan: Plan) -> str:
    parts = [
        plan.get("plan_name"),
        plan.get("extra_benefits"),
        plan.get("message_english"),
    ]

    return " ".join(clean_text(x) for x in parts).lower()


def get_capabilities(plan: Plan) -> set[str]:
    """Detect the set of qualitative capabilities a plan offers."""

    capabilities: set[str] = set()

    product_type = get_product_type(plan)
    text = plan_text(plan)

    # ROAMING
    if (
        product_type == "ROAMING"
        or plan.get("roaming_included") is True
        or (clean_number(plan.get("roaming_data_gb")) or 0) > 0
        or "roaming" in text
        or "roam like home" in text
    ):
        capabilities.add("ROAMING")

    # IDD
    if (
        product_type == "IDD"
        or (clean_number(plan.get("intl_minutes")) or 0) > 0
        or "international minutes" in text
        or "international calls" in text
        or " idd " in f" {text} "
    ):
        capabilities.add("IDD")

    # SOCIAL DATA
    if (
        (clean_number(plan.get("social_pass_gb")) or 0) > 0
        or "social data" in text
        or "social pass" in text
    ):
        capabilities.add("SOCIAL_DATA")

    # ENTERTAINMENT
    entertainment_terms = ["starz", "entertainment", "osn", "shahid", "anghami"]

    if any(term in text for term in entertainment_terms):
        capabilities.add("ENTERTAINMENT")

    return capabilities


def find_separate_omantel_addons(
    omantel_plans: list[Plan], capability: str, category: str, limit: int = 5
) -> list[dict[str, Any]]:
    """Find standalone Omantel add-ons (in ``category``) offering ``capability``.

    ``omantel_plans`` is threaded explicitly here rather than closed over
    from a notebook global -- see the module docstring.
    """

    matches = []

    for plan in omantel_plans:
        if normalize_category(plan.get("category")) != category:
            continue

        if get_plan_role(plan) != "ADDON":
            continue

        if capability not in get_capabilities(plan):
            continue

        matches.append(
            {
                "plan_id": plan.get("plan_id"),
                "plan_name": plan.get("plan_name"),
                "product_type": get_product_type(plan),
                "price_omr": clean_number(plan.get("price_omr")),
            }
        )

    matches = sorted(
        matches,
        key=lambda x: (x["price_omr"] if x["price_omr"] is not None else float("inf")),
    )

    return matches[:limit]


def build_capability_insights(
    competitor_plan: Plan, matched_omantel_plan: Plan, omantel_plans: list[Plan]
) -> list[dict[str, Any]]:
    """Compute the capabilities present in the competitor plan but missing from the match."""

    competitor_caps = get_capabilities(competitor_plan)
    omantel_native_caps = get_capabilities(matched_omantel_plan)

    missing_caps = competitor_caps - omantel_native_caps

    insights = []

    for capability in sorted(missing_caps):
        separate_offers = find_separate_omantel_addons(
            omantel_plans,
            capability=capability,
            category=normalize_category(competitor_plan.get("category")),
        )

        insights.append(
            {
                "capability": capability,
                "status": "MISSING_FROM_MATCHED_PLAN",
                "separate_omantel_offer_exists": len(separate_offers) > 0,
                "separate_omantel_offers": separate_offers,
            }
        )

    return insights


def attach_capability_insights(
    match_result: dict[str, Any], competitor_plan: Plan, omantel_plans: list[Plan]
) -> dict[str, Any]:
    """Mutate ``match_result`` in place with a ``capability_insights`` field.

    Mirrors the reference exactly: mutates and returns the same dict.
    """

    selected = match_result.get("selected_match")

    if selected is None:
        match_result["capability_insights"] = []
        return match_result

    selected_id = str(selected["omantel_plan_id"])

    matched_full_plan = next(
        (plan for plan in omantel_plans if str(plan.get("plan_id")) == selected_id),
        None,
    )

    if matched_full_plan is None:
        match_result["capability_insights"] = []
        return match_result

    match_result["capability_insights"] = build_capability_insights(
        competitor_plan, matched_full_plan, omantel_plans
    )

    return match_result


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def match_competitor_plan(
    competitor_plan: Plan, omantel_plans: list[Plan]
) -> dict[str, Any]:
    """Match a single competitor plan against the Omantel reference catalogue.

    Structured filtering/scoring always runs; the LLM is only invoked when a
    plausible candidate family exists AND the best structured similarity is
    at least 0.40 (saving an LLM call for hopeless matches).
    """

    top_candidates = find_top_candidates(competitor_plan, omantel_plans, top_n=3)

    base_result: dict[str, Any] = {
        "competitor": competitor_plan.get("operator", "ooredoo"),
        "competitor_plan_id": competitor_plan.get("plan_id"),
        "competitor_plan_name": competitor_plan.get("plan_name"),
        "category": normalize_category(competitor_plan.get("category")),
        "plan_role": get_plan_role(competitor_plan),
        "product_type": get_product_type(competitor_plan),
        "top_candidates": top_candidates,
    }

    # No exact candidate family
    if not top_candidates:
        base_result.update(
            {
                "selected_match": None,
                "match_status": "NO_DIRECT_MATCH",
                "match_confidence": None,
                "selection_reason": (
                    "No Omantel plan passed the category, role and product-type filters."
                ),
            }
        )

        return base_result

    # Extremely weak structured match
    if top_candidates[0]["similarity_score"] < 0.40:
        base_result.update(
            {
                "selected_match": None,
                "match_status": "NO_GOOD_MATCH",
                "match_confidence": None,
                "selection_reason": "Best structured similarity was below 0.40.",
            }
        )

        return base_result

    decision = decide_match(compact_plan(competitor_plan), top_candidates)

    allowed_ids = {str(x["omantel_plan_id"]) for x in top_candidates}

    # If LLM says no good match
    if decision.match_status == "NO_GOOD_MATCH":
        base_result.update(
            {
                "selected_match": None,
                "match_status": "NO_GOOD_MATCH",
                "match_confidence": decision.match_confidence,
                "selection_reason": decision.reason,
            }
        )

        return base_result

    # Validate LLM-selected ID
    selected_id = (
        str(decision.selected_plan_id) if decision.selected_plan_id is not None else None
    )

    if selected_id not in allowed_ids:
        base_result.update(
            {
                "selected_match": None,
                "match_status": "REVIEW_REQUIRED",
                "match_confidence": decision.match_confidence,
                "selection_reason": "LLM returned an invalid candidate ID.",
            }
        )

        return base_result

    selected = next(
        x for x in top_candidates if str(x["omantel_plan_id"]) == selected_id
    )

    base_result.update(
        {
            "selected_match": selected,
            "match_status": "MATCHED",
            "match_confidence": decision.match_confidence,
            "selection_reason": decision.reason,
        }
    )

    return base_result


def match_competitor_plans(
    competitor_plans: list[Plan], omantel_plans: list[Plan]
) -> list[dict[str, Any]]:
    """Match a batch of competitor plans against the Omantel reference catalogue.

    A single plan's matching failure is isolated: it is recorded as a
    ``PROCESSING_ERROR`` result rather than aborting the whole batch,
    mirroring the reference's per-item ``try/except`` in its main loop.
    """

    all_matches: list[dict[str, Any]] = []

    for index, competitor_plan in enumerate(competitor_plans, start=1):
        logger.debug(
            "Matching plan %d/%d: %s",
            index,
            len(competitor_plans),
            competitor_plan.get("plan_name"),
        )

        try:
            result = match_competitor_plan(competitor_plan, omantel_plans)
            result = attach_capability_insights(result, competitor_plan, omantel_plans)
            all_matches.append(result)

        except Exception as exc:  # noqa: BLE001 - intentional isolation boundary
            plan_name = competitor_plan.get("plan_name")
            logger.warning("Plan matching failed for %r: %s", plan_name, exc)

            all_matches.append(
                {
                    "competitor_plan_id": competitor_plan.get("plan_id"),
                    "competitor_plan_name": plan_name,
                    "match_status": "PROCESSING_ERROR",
                    "error": str(exc),
                }
            )

    logger.info(
        "Plan matching complete: %d plans processed", len(all_matches)
    )

    return all_matches
