"""Step 6: LLM-generated business narratives for competitive gaps.

Productionized equivalent of ``reference/step6.py``'s *business-logic*
subset. The reference is a full "executive report notebook": it loads
Steps 1-5 outputs, builds several pandas display tables, generates LLM
narratives, then produces matplotlib charts, an Excel workbook, a CSV
export, and a printable markdown ``show_plan_analysis`` function.

Only a subset of that notebook is real, reusable business logic. This
module ports exactly that subset as pure functions (no pandas/matplotlib/
IPython):

- the report-record shaping helpers (``build_report_record``,
  ``build_no_match_report``, ``build_executive_summary``)
- the narrative-eligibility orchestration (``generate_narrative_report``),
  which calls into ``market_pulse.llm.narrative_generator`` for the actual
  LLM work

Dropped entirely (notebook-only Jupyter dashboard/export scaffolding, with
no production equivalent):
- all ``display()``/``Markdown`` calls and matplotlib charting
  (risk-distribution bar chart, top-10 horizontal bar chart, threat-vs-
  exposure scatter matrix)
- the Step 1/Step 2 portfolio-overview inspection tables (``step1_df``,
  ``step1_summary``, ``step2_df``, ``step2_summary``) -- pure inspection
  views with no downstream consumers; a future "portfolio overview"
  endpoint would be a separate API concern, not this stage's job
- the full ``step3_df`` display table (only its "no match" subset is real,
  useful output -- ported as ``build_no_match_report``, built directly from
  ``step3_matches`` without needing the full display table)
- ``report_df.head()``/``print(len(...))`` diagnostics
- ``top_risk_df``/``final_top_risk_df`` sorting + priority-numbering and the
  associated ``Top_Risks``/management-columns display views -- a
  presentation/sorting concern for a UI or report-export layer, not this
  stage's job
- ``gap_analysis_df``/``excel_gap_analysis`` column-subsetting -- the
  ``records`` list returned by ``generate_narrative_report`` already
  contains the full field set; a consumer can filter columns itself
- ``show_plan_analysis(...)`` printable markdown demo function
- ``high_risk_report`` filtering display -- a trivial filter a consumer can
  do themselves
- the ``PERFORMANCE_DATA_LABEL = "MOCK"`` constant and the executive
  summary's "Exposure data source" row -- tied to the mock-performance-data
  concept that was deliberately dropped in Step 5; there is nothing
  meaningful to report here in production, so it is simply omitted
- the ``ExcelWriter``/openpyxl workbook export, CSV export, and the
  ``NARRATIVE_OUTPUT`` JSON file write -- local-disk export concerns for a
  future reporting/export layer, out of scope for the ``narrative_generation``
  stage itself (``docs/architecture.md``'s stage-result storage model only
  requires JSON-serializable stage results)

The reference's final notebook assertions (every ``SCORED`` record has a
non-null risk score; risk scores are within ``[0, 100]``; the LLM narrative
step never overwrites/recomputes calculated fields) are real, valuable
validations -- they are preserved as automated regression tests under
``tests/unit/test_narrative_service.py`` rather than left as bare ``assert``
statements in production code.

``get_plan_role``/``get_product_type`` below are Step 6's OWN copies (with
the ``llm_enrichment`` ``or {}`` guard, matching Steps 4/5's versions, NOT
Step 3's unguarded version) -- intentionally not imported from any other
step's service module, consistent with this codebase's established
per-step-duplication convention. They are not called by any of the
retained report-shaping logic below (the reference only used them to build
the dropped Step 1/Step 2 portfolio-overview tables), but are kept available
here for any future portfolio-overview endpoint.

The functions below intentionally mirror the reference implementation's
behavior exactly. Do not change formulas, weights, thresholds, or narrative
eligibility logic.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Optional

from market_pulse.llm.narrative_generator import generate_narrative

logger = logging.getLogger(__name__)

Plan = dict[str, Any]

# ---------------------------------------------------------------------------
# Basic cleaning helpers -- Step 6's own copies.
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


def clean_id(value: Any) -> str:
    """Coerce a raw id value to a stripped string, or ``""`` for ``None``."""

    if value is None:
        return ""

    return str(value).strip()


# ---------------------------------------------------------------------------
# Classification helpers (three-tier precedence) -- Step 6's own copies.
# Not called by the retained report-shaping logic below; see module
# docstring.
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

    Note the ``or {}`` guard on ``llm_enrichment`` -- this is Step 6's own
    copy and matches Steps 4/5's version (not Step 3's unguarded version).
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

    product_type = clean_text(plan.get("product_type")).upper()

    if product_type in VALID_PRODUCT_TYPES:
        return product_type

    llm = plan.get("llm_enrichment", {}) or {}

    semantic = clean_text(
        llm.get("semantic_product_type") or llm.get("product_type")
    ).upper()

    if semantic in VALID_PRODUCT_TYPES:
        return semantic

    return "OTHER"


# ---------------------------------------------------------------------------
# Step 4-shape readers
# ---------------------------------------------------------------------------

METRICS = ["price", "data", "voice", "idd", "sms", "validity"]


def metric_details(step4_item: dict[str, Any], metric: str) -> dict[str, Any]:
    """Safe nested getter for ``step4_item["metric_gaps"][metric]``."""

    return (step4_item.get("metric_gaps", {}) or {}).get(metric, {}) or {}


def competitor_advantage_metrics(step4_item: dict[str, Any]) -> list[str]:
    return [
        metric.upper()
        for metric in METRICS
        if metric_details(step4_item, metric).get("position") == "COMPETITOR_ADVANTAGE"
    ]


def omantel_advantage_metrics(step4_item: dict[str, Any]) -> list[str]:
    return [
        metric.upper()
        for metric in METRICS
        if metric_details(step4_item, metric).get("position") == "OMANTEL_ADVANTAGE"
    ]


def capability_gap_names(step4_item: dict[str, Any]) -> list[str]:
    gaps = step4_item.get("capability_gaps", []) or []

    return [
        clean_text(item.get("capability")).upper()
        for item in gaps
        if clean_text(item.get("capability"))
    ]


def primary_attention_area(step4_item: dict[str, Any]) -> str:
    """The single worst measured issue for a matched pair.

    Precedence (must be preserved exactly):
    1. The most-negative weighted commercial-metric contribution, if any.
    2. Otherwise, the first capability gap, if any.
    3. Otherwise, ``"BALANCED"``.
    """

    weighted = step4_item.get("weighted_position", {}) or {}
    contributions = weighted.get("weighted_contributions", {}) or {}

    negative: dict[str, float] = {}

    for metric, value in contributions.items():
        value = clean_number(value)

        if value is not None and value < 0:
            negative[metric.upper()] = value

    if negative:
        return min(negative, key=negative.get)

    capabilities = capability_gap_names(step4_item)

    if capabilities:
        return capabilities[0]

    return "BALANCED"


def format_capability_gaps(step4_item: dict[str, Any]) -> str:
    gaps = step4_item.get("capability_gaps", []) or []

    parts = []

    for item in gaps:
        capability = clean_text(item.get("capability")).upper()

        if not capability:
            continue

        addon_exists = bool(item.get("separate_omantel_offer_exists"))

        if addon_exists:
            parts.append(f"{capability} (missing natively; separate addon exists)")
        else:
            parts.append(f"{capability} (missing natively; no addon found)")

    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Step 4 <-> Step 5 join key
# ---------------------------------------------------------------------------


def report_key(record: dict[str, Any]) -> tuple[str, str, str, str]:
    """Join key shared by Step 4 and Step 5 records for the same matched pair.

    Works because Step 5's output carries these same 4 field names
    pass-through from Step 4 (``competitor_plan_id``, ``competitor_plan``,
    ``omantel_plan_id``, ``omantel_plan``).
    """

    return (
        clean_id(record.get("competitor_plan_id")),
        clean_text(record.get("competitor_plan")),
        clean_id(record.get("omantel_plan_id")),
        clean_text(record.get("omantel_plan")),
    )


# ---------------------------------------------------------------------------
# Report record builders
# ---------------------------------------------------------------------------


def build_report_record(
    step4_item: dict[str, Any],
    step5_lookup: dict[tuple[str, str, str, str], dict[str, Any]],
) -> dict[str, Any]:
    """Build the full flattened per-match report row.

    Mirrors the reference's ``build_report_record`` exactly (field names and
    values), except ``step5_lookup`` is threaded through explicitly rather
    than closed over from a notebook global.
    """

    step5_item = step5_lookup.get(report_key(step4_item), {}) or {}

    weighted_position = step4_item.get("weighted_position", {}) or {}

    record: dict[str, Any] = {
        "Competitor Plan ID": clean_id(step4_item.get("competitor_plan_id")),
        "Competitor Plan": step4_item.get("competitor_plan"),
        "Omantel Plan ID": clean_id(step4_item.get("omantel_plan_id")),
        "Omantel Plan": step4_item.get("omantel_plan"),
        "Category": step4_item.get("category"),
        "Product Type": step4_item.get("product_type"),
        "Similarity": step4_item.get("similarity_score"),
        "Match Confidence": step4_item.get("match_confidence"),
        "Gap Analysis Status": step4_item.get("gap_analysis_status"),
        "Commercial Position Score": weighted_position.get("commercial_position_score"),
        "Commercial Position": weighted_position.get("overall_position"),
        "Primary Attention Area": primary_attention_area(step4_item),
        "Competitor Advantages": ", ".join(competitor_advantage_metrics(step4_item)),
        "Omantel Advantages": ", ".join(omantel_advantage_metrics(step4_item)),
        "Capability Gaps": format_capability_gaps(step4_item),
        "Competitive Threat": step5_item.get("competitive_threat_score"),
        "Avg Active Users 6M": step5_item.get("avg_active_users_6m"),
        "Avg Product ARPU 6M": step5_item.get("avg_product_arpu_6m"),
        "Avg Monthly Revenue 6M": step5_item.get("avg_monthly_revenue_6m"),
        "Customer Exposure": step5_item.get("customer_exposure_score"),
        "Revenue Exposure": step5_item.get("revenue_exposure_score"),
        "Business Exposure": step5_item.get("business_exposure_score"),
        "Risk Score": step5_item.get("risk_score"),
        "Risk Level": step5_item.get("risk_level"),
        "Risk Status": step5_item.get("risk_status"),
        "Risk Reasons": "; ".join(step5_item.get("risk_reasons", []) or []),
    }

    for metric in METRICS:
        details = metric_details(step4_item, metric)
        title = metric.upper()

        record[f"{title} Competitor"] = details.get("competitor")
        record[f"{title} Omantel"] = details.get("omantel")
        record[f"{title} Gap %"] = details.get("gap_pct")
        record[f"{title} Position"] = details.get("position")

    return record


def build_no_match_report(step3_matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Report rows for Step 3 records with no ("good") comparable match.

    Mirrors the reference's ``step3_df`` construction, filtered to
    ``Match Status != "MATCHED"`` (the "Potential Portfolio Gaps / No Good
    Comparable Match" report section) -- built directly from
    ``step3_matches`` without needing to build the full display table first.
    """

    rows = []

    for item in step3_matches:
        if item.get("match_status") == "MATCHED":
            continue

        selected = item.get("selected_match") or {}

        rows.append(
            {
                "Competitor Plan": item.get("competitor_plan_name"),
                "Category": item.get("category"),
                "Role": item.get("plan_role"),
                "Product Type": item.get("product_type"),
                "Omantel Match": selected.get("omantel_plan_name"),
                "Similarity": selected.get("similarity_score"),
                "Match Confidence": item.get("match_confidence"),
                "Match Status": item.get("match_status"),
                "Reason": item.get("selection_reason"),
            }
        )

    return rows


def build_executive_summary(
    competitor: str,
    ooredoo_plans: list[dict[str, Any]],
    omantel_plans: list[dict[str, Any]],
    step3_matches: list[dict[str, Any]],
    step4_results: list[dict[str, Any]],
    step5_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run-level executive summary counts for one competitor.

    Mirrors the reference's executive-summary counts exactly, EXCEPT the
    "Ooredoo plans analyzed" label is generalized to "competitor_plans_analyzed"
    (with a dynamic "competitor" field identifying which one) -- the
    reference hardcoded "Ooredoo" because that notebook only ever analyzed
    one competitor, but this system is explicitly multi-competitor
    (CLAUDE.md's Multi-Competitor Rule), so a literal "ooredoo_" key would be
    actively misleading when this run is for Vodafone, Friendi, etc.
    ``competitor`` is the caller-supplied name from the submission (e.g.
    ``POST /runs/{run_id}/competitors``'s ``"competitor"`` field) -- the
    authoritative source, not inferred from plan data (which may be missing
    or inconsistent). This is a field-naming/labeling fix, not a
    business-logic change -- the counted values themselves are unchanged.

    Unlike the reference, does NOT include an "Exposure data source: MOCK"
    field -- that concept does not exist in production Step 5 (see module
    docstring).
    """

    comparable_plans_matched = sum(
        item.get("match_status") == "MATCHED" for item in step3_matches
    )

    no_direct_omantel_match = sum(
        item.get("match_status") == "NO_DIRECT_MATCH" for item in step3_matches
    )

    gap_analyses_completed = sum(
        item.get("gap_analysis_status") == "ANALYZED" for item in step4_results
    )

    risk_scores_completed = sum(
        item.get("risk_status") == "SCORED" for item in step5_results
    )

    high_risk = sum(item.get("risk_level") == "HIGH" for item in step5_results)
    medium_risk = sum(item.get("risk_level") == "MEDIUM" for item in step5_results)
    low_risk = sum(item.get("risk_level") == "LOW" for item in step5_results)

    return {
        "competitor": competitor,
        "competitor_plans_analyzed": len(ooredoo_plans),
        "omantel_atl_products": len(omantel_plans),
        "comparable_plans_matched": comparable_plans_matched,
        "no_direct_omantel_match": no_direct_omantel_match,
        "gap_analyses_completed": gap_analyses_completed,
        "risk_scores_completed": risk_scores_completed,
        "high_risk": high_risk,
        "medium_risk": medium_risk,
        "low_risk": low_risk,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

_NARRATIVE_FIELDS = ("gap_summary", "key_issue", "business_explanation", "narrative_source")


def generate_narrative_report(
    competitor: str,
    ooredoo_plans: list[dict[str, Any]],
    omantel_plans: list[dict[str, Any]],
    step3_matches: list[dict[str, Any]],
    step4_results: list[dict[str, Any]],
    step5_results: list[dict[str, Any]],
    chain: Any = None,
) -> dict[str, Any]:
    """Build the full Step 6 (``narrative_generation``) stage result.

    ``competitor`` is the caller-supplied competitor name (e.g. "vodafone")
    -- passed through to ``build_executive_summary`` so the executive
    summary is labeled with the actual competitor instead of a hardcoded
    one. See ``build_executive_summary`` for why this must come from the
    caller rather than being inferred from plan data.

    Returns a dict with:
    - ``"records"``: one entry per ``step4_results`` item, built as
      ``build_report_record`` merged with narrative fields. Narrative fields
      (``gap_summary``, ``key_issue``, ``business_explanation``,
      ``narrative_source``) are only generated for records where the Step 4
      item's ``gap_analysis_status == "ANALYZED"`` AND the matched Step 5
      record's ``risk_status == "SCORED"``; for every other record those
      fields are ``None`` (mirroring the reference's left-join-with-nulls
      behavior for non-eligible rows).
    - ``"no_match_report"``: see ``build_no_match_report``.
    - ``"executive_summary"``: see ``build_executive_summary``.

    ``chain`` is an optional pre-built narrative chain (see
    ``market_pulse.llm.narrative_generator.get_report_chain``), primarily for
    test injection; when omitted, ``generate_narrative`` builds its own
    default chain lazily per call.

    A single record's narrative-generation failure is isolated: it is
    logged and the record's narrative fields are left ``None`` rather than
    aborting the whole batch. This is an EXTRA defensive layer beyond the
    reference -- the primary resilience mechanism is
    ``generate_narrative``'s own internal try/except + deterministic
    fallback, which should make this outer catch rare in practice.
    """

    step5_lookup = {report_key(item): item for item in step5_results}

    records: list[dict[str, Any]] = []

    for index, step4_item in enumerate(step4_results, start=1):
        record = build_report_record(step4_item, step5_lookup)

        step5_item = step5_lookup.get(report_key(step4_item), {}) or {}

        eligible = (
            step4_item.get("gap_analysis_status") == "ANALYZED"
            and step5_item.get("risk_status") == "SCORED"
        )

        if eligible:
            logger.debug(
                "Generating narrative %d/%d: %s",
                index,
                len(step4_results),
                step4_item.get("competitor_plan"),
            )

            try:
                narrative_dict, narrative_source = generate_narrative(
                    step4_item, step5_item, chain=chain
                )

                record["gap_summary"] = narrative_dict.get("gap_summary")
                record["key_issue"] = narrative_dict.get("key_issue")
                record["business_explanation"] = narrative_dict.get("business_explanation")
                record["narrative_source"] = narrative_source

            except Exception as exc:  # noqa: BLE001 - intentional isolation boundary
                logger.warning(
                    "Narrative generation failed for %r: %s",
                    step4_item.get("competitor_plan"),
                    exc,
                )

                for field in _NARRATIVE_FIELDS:
                    record[field] = None
        else:
            for field in _NARRATIVE_FIELDS:
                record[field] = None

        records.append(record)

    logger.info("Narrative report complete: %d records processed", len(records))

    return {
        "records": records,
        "no_match_report": build_no_match_report(step3_matches),
        "executive_summary": build_executive_summary(
            competitor, ooredoo_plans, omantel_plans, step3_matches, step4_results, step5_results
        ),
    }
