"""Step 5: competitive threat / business exposure / risk scoring.

Productionized equivalent of ``reference/step5.py``. This step is purely
deterministic -- no LLM calls at all, so there is no ``llm/`` module for it.

Architectural change from the reference (deliberate, already decided --
see ``docs/architecture.md`` and the Step 5 task description, do not
re-litigate): the reference notebook *synthesizes* 12 months of mock Omantel
product-performance data (active users, ARPU, revenue) using an
``hashlib.md5``-seeded ``random.Random`` generator, because no real
performance-data source existed when the notebook was written. That mock
generator is dropped entirely from production. This service instead requires
the caller to supply real performance records
(``schemas.risk_analysis.ProductPerformanceRecord``). If no performance data
is available for a matched Omantel plan, the result's ``risk_status`` is
``"REVIEW_REQUIRED"`` -- this reuses the reference's *existing*
"no performance data found" branch (``exposure is None``), so no new status
was introduced. A real performance-data source now exists (see
``load_performance_records_from_csv`` below, wired in by
``orchestration/pipeline.py``'s risk_analysis stage), so this branch no
longer fires routinely -- it now mainly covers plans genuinely absent from
the performance dataset, or the pipeline's defensive fallback to an empty
list when the performance CSV can't be loaded for a given run.

Everything from the reference's ``perf_df = pd.read_csv(...)`` cell onward --
coercion, the 6-month window, the ``product_summary_df`` groupby/aggregation,
relative exposure scoring, and the full risk formula -- is real business
logic and is preserved exactly, just fed by real input records instead of a
re-read mock CSV.

Structural change (same pattern as Steps 3-4): the reference closes over
module-level globals ``performance_lookup`` and ``latest_month`` inside
``analyze_step5_record``. A stateless service threads both through explicitly
as parameters instead.

The functions below intentionally mirror the reference implementation's
behavior (formulas, weights, thresholds) exactly. Do not change formulas,
weights, thresholds, or risk logic.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Optional, Sequence, Union

import pandas as pd

from market_pulse.config.formula_config import RiskAnalysisConfig, get_risk_analysis_config
from market_pulse.schemas.risk_analysis import ProductPerformanceRecord

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Real Omantel product-performance CSV loading
# ---------------------------------------------------------------------------
#
# Kept separate from the pure aggregation logic below (same precedent as
# ``omantel_normalization_service.load_omantel_catalogues_from_csv``) so
# tests can exercise aggregation/scoring without requiring a real file on
# disk. Column mapping (confirmed against the real
# ``data/omantel/PRODUCT_PERFORMANCE.csv`` file):
#
#   product_id        -> omantel_plan_id
#   product_name       -> omantel_plan
#   month               -> month (already "YYYY-MM" strings; pass through)
#   unique_customers    -> active_users
#   arpu                -> product_arpu
#   total_revenue       -> monthly_revenue_omr
#   price, number_of_purchases -> not used by the risk formula, ignored.


def load_performance_records_from_csv(path: str | Path) -> list[dict[str, Any]]:
    """Load real Omantel product-performance records from ``path``.

    Returns a list of plain dicts shaped like ``ProductPerformanceRecord``
    (field names, not yet pydantic-validated -- ``analyze_step5_records``'s
    ``_coerce_records`` validates/coerces each one when the batch is
    aggregated). ``month`` values in the real file are already ``"YYYY-MM"``
    strings, matching what the schema expects, so no reformatting is done
    here.

    Raises whatever ``pandas.read_csv`` raises (e.g. ``FileNotFoundError``)
    if ``path`` doesn't exist or can't be parsed -- this function does not
    swallow errors; callers decide how to handle a missing/broken file (see
    ``orchestration/pipeline.py``'s risk_analysis stage for the production
    fallback-to-empty-list behavior).
    """

    df = pd.read_csv(path)

    expected_columns = [
        "product_id",
        "product_name",
        "month",
        "unique_customers",
        "arpu",
        "total_revenue",
    ]

    missing = [c for c in expected_columns if c not in df.columns]

    if missing:
        raise ValueError(
            f"Performance CSV at {path} is missing expected column(s): {missing}. "
            f"Found columns: {list(df.columns)}."
        )

    records: list[dict[str, Any]] = []

    for _, row in df.iterrows():
        records.append(
            {
                "omantel_plan_id": row.get("product_id"),
                "omantel_plan": row.get("product_name"),
                "month": row.get("month"),
                "active_users": row.get("unique_customers"),
                "product_arpu": row.get("arpu"),
                "monthly_revenue_omr": row.get("total_revenue"),
            }
        )

    return records


# ---------------------------------------------------------------------------
# Basic cleaning helpers -- Step 5's own copies (no llm_enrichment involved
# in this step at all, so these are simpler than Steps 3/4's versions).
# ---------------------------------------------------------------------------


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


def safe_round(value: Any, digits: int = 2) -> Optional[float]:
    value = clean_number(value)

    if value is None:
        return None

    return round(value, digits)


# ---------------------------------------------------------------------------
# Performance-data aggregation
# ---------------------------------------------------------------------------
#
# Revenue-fallback semantics (production adaptation, documented per the task
# instructions): the reference performs an all-or-nothing *column presence*
# check on a pandas DataFrame (`"monthly_revenue_omr" not in perf_df.columns`).
# For a list-of-records input there is no single "column" to check for
# presence, so the equivalent dataset-level semantics used here are: if *any*
# input record carries a non-None ``monthly_revenue_omr``, the field is
# treated as "supplied" for the whole batch and each record's own value is
# used (coerced numerically, same as the reference's `pd.to_numeric` branch
# -- a record that didn't supply a value still gets ``None``, matching a
# NaN cell in an existing column). If *no* record in the batch supplies a
# value, ``monthly_revenue_omr`` is computed for every record as
# ``active_users * product_arpu``, matching the reference's fallback branch.


def _shift_months(value: date, delta: int) -> date:
    """Shift a normalized (day=1) date by ``delta`` calendar months."""

    total_months = value.year * 12 + (value.month - 1) + delta

    return date(total_months // 12, total_months % 12 + 1, 1)


def _coerce_records(
    performance_records: Sequence[Union[ProductPerformanceRecord, dict]],
) -> list[ProductPerformanceRecord]:
    records: list[ProductPerformanceRecord] = []

    for record in performance_records:
        if isinstance(record, ProductPerformanceRecord):
            records.append(record)
        else:
            records.append(ProductPerformanceRecord.model_validate(record))

    return records


def score_relative_to_max(values: Sequence[Any]) -> list[float]:
    """Score each value relative to the max of ``values``, in ``[0, 100]``.

    Mirrors the reference's ``score_relative_to_max`` exactly: values are
    coerced to numeric with NaN/unusable -> 0; if the max is <= 0 every score
    is ``0.0``; otherwise each score is ``value / max * 100``, clipped to
    ``[0, 100]``.

    This is computed relative to the max across *all* products in the
    current batch being scored -- not an absolute/global scale, and not
    per-product-in-isolation.
    """

    numeric = [clean_number(v) for v in values]
    numeric = [v if v is not None else 0.0 for v in numeric]

    if not numeric:
        return []

    max_value = max(numeric)

    if max_value <= 0:
        return [0.0] * len(numeric)

    return [max(0.0, min(100.0, (v / max_value) * 100)) for v in numeric]


def aggregate_performance_records(
    performance_records: Sequence[Union[ProductPerformanceRecord, dict]],
    config: Optional[RiskAnalysisConfig] = None,
) -> tuple[dict[tuple[str, str], dict[str, Any]], Optional[date]]:
    """Aggregate raw performance records into a ``performance_lookup`` dict.

    Mirrors the reference's ``perf_df`` coercion + revenue fallback + 6-month
    window filter + ``product_summary_df`` groupby/agg + exposure scoring,
    exactly, just fed by real input records instead of a re-read mock CSV.

    Returns ``(performance_lookup, latest_month)``. If ``performance_records``
    is empty, returns ``({}, None)`` -- callers should not raise for this;
    each Step 5 record simply falls through to the existing
    ``REVIEW_REQUIRED`` / "no performance data found" branch.
    """

    config = config or get_risk_analysis_config()

    records = _coerce_records(performance_records)

    if not records:
        return {}, None

    revenue_supplied = any(r.monthly_revenue_omr is not None for r in records)

    enriched: list[tuple[ProductPerformanceRecord, Optional[float]]] = []

    for record in records:
        if revenue_supplied:
            revenue = record.monthly_revenue_omr
        elif record.active_users is None or record.product_arpu is None:
            revenue = None
        else:
            revenue = record.active_users * record.product_arpu

        enriched.append((record, revenue))

    latest_month = max(record.month for record, _ in enriched)

    six_month_start = _shift_months(latest_month, -5)

    windowed = [
        (record, revenue)
        for record, revenue in enriched
        if six_month_start <= record.month <= latest_month
    ]

    groups: dict[tuple[str, str], list[tuple[ProductPerformanceRecord, Optional[float]]]] = (
        defaultdict(list)
    )

    for record, revenue in windowed:
        key = (str(record.omantel_plan_id), str(record.omantel_plan))
        groups[key].append((record, revenue))

    summaries: dict[tuple[str, str], dict[str, Any]] = {}

    for key, items in groups.items():
        months_used = len({record.month for record, _ in items})

        active_users_vals = [record.active_users for record, _ in items if record.active_users is not None]
        arpu_vals = [record.product_arpu for record, _ in items if record.product_arpu is not None]
        revenue_vals = [revenue for _, revenue in items if revenue is not None]

        avg_active_users = (
            sum(active_users_vals) / len(active_users_vals) if active_users_vals else None
        )
        avg_arpu = sum(arpu_vals) / len(arpu_vals) if arpu_vals else None
        avg_revenue = sum(revenue_vals) / len(revenue_vals) if revenue_vals else None

        summaries[key] = {
            "omantel_plan_id": key[0],
            "omantel_plan": key[1],
            "months_used": months_used,
            "avg_active_users_6m": avg_active_users,
            "avg_product_arpu_6m": avg_arpu,
            "avg_monthly_revenue_6m": avg_revenue,
        }

    # Exposure scores are relative to the max across ALL products in this
    # batch -- compute after the full summaries dict is built.
    keys = list(summaries.keys())

    customer_scores = score_relative_to_max([summaries[k]["avg_active_users_6m"] for k in keys])
    revenue_scores = score_relative_to_max([summaries[k]["avg_monthly_revenue_6m"] for k in keys])

    for key, customer_score, revenue_score in zip(keys, customer_scores, revenue_scores):
        business_exposure = (
            config.business_exposure_weights.customer_weight * customer_score
            + config.business_exposure_weights.revenue_weight * revenue_score
        )

        summaries[key]["customer_exposure_score"] = customer_score
        summaries[key]["revenue_exposure_score"] = revenue_score
        summaries[key]["business_exposure_score"] = business_exposure

    return summaries, latest_month


# ---------------------------------------------------------------------------
# Risk formula functions
# ---------------------------------------------------------------------------


def competitive_threat_from_step4(
    commercial_position_score: Any,
    config: Optional[RiskAnalysisConfig] = None,
) -> Optional[float]:
    config = config or get_risk_analysis_config()

    score = clean_number(commercial_position_score)

    if score is None:
        return None

    # Balanced or Omantel advantage
    if score >= -config.competitive_threat_threshold:
        return 0.0

    # Negative Step 4 score becomes positive threat
    threat = abs(score)

    return round(min(100.0, threat), 2)


def get_risk_level(risk_score: Any, config: Optional[RiskAnalysisConfig] = None) -> str:
    config = config or get_risk_analysis_config()

    score = clean_number(risk_score)

    if score is None:
        return "NOT_SCORED"

    if score < config.risk_level_thresholds.medium:
        return "LOW"

    if score < config.risk_level_thresholds.high:
        return "MEDIUM"

    return "HIGH"


def build_risk_reasons(
    step4_item: dict[str, Any], exposure_row: dict[str, Any]
) -> list[str]:
    reasons: list[str] = []

    metric_gaps = step4_item.get("metric_gaps", {}) or {}

    competitor_advantages = [
        metric.upper()
        for metric, details in metric_gaps.items()
        if isinstance(details, dict) and details.get("position") == "COMPETITOR_ADVANTAGE"
    ]

    if competitor_advantages:
        reasons.append("Competitor advantage in: " + ", ".join(competitor_advantages))

    capability_gaps = step4_item.get("capability_gaps", []) or []

    missing_capabilities = [
        item.get("capability") for item in capability_gaps if item.get("capability")
    ]

    if missing_capabilities:
        reasons.append("Native capability gap: " + ", ".join(missing_capabilities))

    customer_score = clean_number(exposure_row.get("customer_exposure_score"))
    revenue_score = clean_number(exposure_row.get("revenue_exposure_score"))

    if customer_score is not None and customer_score >= 70:
        reasons.append("High customer exposure.")
    elif customer_score is not None and customer_score >= 40:
        reasons.append("Moderate customer exposure.")

    if revenue_score is not None and revenue_score >= 70:
        reasons.append("High revenue exposure.")
    elif revenue_score is not None and revenue_score >= 40:
        reasons.append("Moderate revenue exposure.")

    return reasons[:4]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def analyze_step5_record(
    step4_item: dict[str, Any],
    performance_lookup: dict[tuple[str, str], dict[str, Any]],
    latest_month: Optional[date],
    config: Optional[RiskAnalysisConfig] = None,
) -> dict[str, Any]:
    """Analyze the risk for a single Step 4 matched-pair record.

    ``performance_lookup``/``latest_month`` are threaded through explicitly
    rather than closed over from notebook globals -- see module docstring.
    """

    config = config or get_risk_analysis_config()

    result: dict[str, Any] = {
        "competitor_plan_id": step4_item.get("competitor_plan_id"),
        "competitor_plan": step4_item.get("competitor_plan"),
        "omantel_plan_id": step4_item.get("omantel_plan_id"),
        "omantel_plan": step4_item.get("omantel_plan"),
        "product_type": step4_item.get("product_type"),
        "similarity_score": step4_item.get("similarity_score"),
        "match_confidence": step4_item.get("match_confidence"),
        "step4_gap_analysis_status": step4_item.get("gap_analysis_status"),
    }

    # Step 4 was not analyzed
    if step4_item.get("gap_analysis_status") != "ANALYZED":
        result.update(
            {
                "risk_status": "NOT_ANALYZED",
                "reason": "Step 4 did not produce an analyzed matched pair.",
            }
        )

        return result

    key = (
        str(step4_item.get("omantel_plan_id")),
        str(step4_item.get("omantel_plan")),
    )

    exposure = performance_lookup.get(key)

    if exposure is None:
        result.update(
            {
                "risk_status": "REVIEW_REQUIRED",
                "reason": "No product performance data found for the matched Omantel plan.",
            }
        )

        return result

    months_used = int(exposure.get("months_used", 0))

    # We agreed to use 6 months
    if months_used < 6:
        result.update(
            {
                "risk_status": "REVIEW_REQUIRED",
                "reason": f"Only {months_used} months of product performance data are available.",
            }
        )

        return result

    avg_users = clean_number(exposure.get("avg_active_users_6m"))
    avg_arpu = clean_number(exposure.get("avg_product_arpu_6m"))
    avg_revenue = clean_number(exposure.get("avg_monthly_revenue_6m"))

    if avg_users is None or avg_arpu is None or avg_revenue is None:
        result.update(
            {
                "risk_status": "REVIEW_REQUIRED",
                "reason": "Required product-performance values are missing.",
            }
        )

        return result

    weighted_position = step4_item.get("weighted_position", {}) or {}

    commercial_score = clean_number(weighted_position.get("commercial_position_score"))

    competitive_threat = competitive_threat_from_step4(commercial_score, config=config)

    customer_exposure = clean_number(exposure.get("customer_exposure_score"))
    revenue_exposure = clean_number(exposure.get("revenue_exposure_score"))
    business_exposure = clean_number(exposure.get("business_exposure_score"))

    if competitive_threat is None or business_exposure is None:
        result.update(
            {
                "risk_status": "REVIEW_REQUIRED",
                "reason": "Competitive threat or business exposure could not be calculated.",
            }
        )

        return result

    # -----------------------------
    # FINAL RISK FORMULA
    # -----------------------------

    risk_score = competitive_threat * (business_exposure / 100)

    result.update(
        {
            "risk_status": "SCORED",
            "latest_month_used": latest_month.strftime("%Y-%m") if latest_month else None,
            "months_used": months_used,
            "avg_active_users_6m": safe_round(avg_users, 2),
            "avg_product_arpu_6m": safe_round(avg_arpu, 3),
            "avg_monthly_revenue_6m": safe_round(avg_revenue, 2),
            "step4_commercial_position_score": safe_round(commercial_score, 2),
            "competitive_threat_score": safe_round(competitive_threat, 2),
            "customer_exposure_score": safe_round(customer_exposure, 2),
            "revenue_exposure_score": safe_round(revenue_exposure, 2),
            "business_exposure_score": safe_round(business_exposure, 2),
            "risk_score": safe_round(risk_score, 2),
            "risk_level": get_risk_level(risk_score, config=config),
            "risk_reasons": build_risk_reasons(step4_item, exposure),
        }
    )

    return result


def analyze_step5_records(
    step4_results: list[dict[str, Any]],
    performance_records: Sequence[Union[ProductPerformanceRecord, dict]],
    config: Optional[RiskAnalysisConfig] = None,
) -> list[dict[str, Any]]:
    """Analyze a batch of Step 4 matched-pair records for competitive risk.

    A single record's analysis failure is isolated: it is recorded as a
    ``PROCESSING_ERROR`` result rather than aborting the whole batch,
    mirroring the reference's per-item ``try/except`` in its main loop.

    An empty ``performance_records`` input does not raise -- unlike the
    reference (which raised ``ValueError`` purely to gate the now-removed
    mock-data generation step), every ``ANALYZED`` Step 4 item simply falls
    through to the existing ``REVIEW_REQUIRED`` "no performance data found"
    branch.
    """

    config = config or get_risk_analysis_config()

    performance_lookup, latest_month = aggregate_performance_records(
        performance_records, config=config
    )

    results: list[dict[str, Any]] = []

    for index, item in enumerate(step4_results, start=1):
        logger.debug(
            "Analyzing risk %d/%d: %s",
            index,
            len(step4_results),
            item.get("competitor_plan"),
        )

        try:
            result = analyze_step5_record(item, performance_lookup, latest_month, config=config)

        except Exception as exc:  # noqa: BLE001 - intentional isolation boundary
            plan_name = item.get("competitor_plan")
            logger.warning("Risk analysis failed for %r: %s", plan_name, exc)

            result = {
                "competitor_plan_id": item.get("competitor_plan_id"),
                "competitor_plan": plan_name,
                "omantel_plan": item.get("omantel_plan"),
                "risk_status": "PROCESSING_ERROR",
                "error": str(exc),
            }

        results.append(result)

    logger.info("Risk analysis complete: %d records processed", len(results))

    return make_json_safe(results)


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
