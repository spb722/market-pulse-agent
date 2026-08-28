"""LLM-based gap-narrative generation for Step 6 (narrative_generation).

This is the only module that constructs an LLM client for Step 6's business
narrative generation. Deterministic report-shaping logic (``build_report_record``,
``build_no_match_report``, ``build_executive_summary``, and the eligibility
gating / batch orchestration of ``generate_narrative_report``) must not live
here; it belongs in ``market_pulse.services.narrative_service``.

Preserves, verbatim, the reference implementation's:
- narrative prompt (system + human text, including all 11 numbered strict
  rules and field definitions)
- structured output schema (``GapNarrative``) and invocation method
  (``method="json_schema"``, without ``strict=True`` -- matches Steps 2/3's
  pattern, differs from Step 1)
- the deterministic fallback text-construction logic (``fallback_narrative``),
  used whenever the LLM call fails, so a failed LLM call still produces a
  complete, usable narrative rather than a missing/error record

The reference implementation experimented with a local Ollama-backed
``ChatOpenAI`` client (``model="gpt-oss:20b"``, ``base_url="http://localhost:11434/v1"``).
Production instead always uses ``langchain_openai.ChatOpenAI`` against a
configurable OpenAI-compatible endpoint (see ``market_pulse.config.settings``),
reusing the same convention established for Steps 1-3's LLM modules.

Narrative source label: the reference hardcodes ``"GPT-OSS"`` as the success
label. That literal names a specific model tied to the notebook's Groq/Ollama
experiment and is no longer accurate once the provider/model is configurable
via ``Settings`` -- production uses ``"LLM_GENERATED"`` instead. The failure
label ``"DETERMINISTIC_FALLBACK"`` is unchanged (still accurate).

Local helper duplication: ``build_llm_facts``/``fallback_narrative`` need the
same small set of pure Step 4/5-shape readers (``metric_details``,
``competitor_advantage_metrics``, ``omantel_advantage_metrics``,
``capability_gap_names``, ``primary_attention_area``, ``clean_text``,
``clean_number``) that also live in
``market_pulse.services.narrative_service``. They are NOT imported from
there: ``narrative_service`` imports ``generate_narrative`` from this module
(services depend on llm, matching every other step's convention -- see
``plan_matching_service`` importing from ``llm.plan_matcher``), so an
``llm -> services`` import back would be circular. This module therefore
keeps its own small private copies, consistent with the project's
established per-module duplication convention (see e.g. Steps 4/5's own
copies of ``clean_text``/``clean_number``/``get_plan_role``/``get_product_type``).
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any, Callable, Optional

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from market_pulse.config.settings import Settings, get_settings
from market_pulse.schemas.narrative import GapNarrative

logger = logging.getLogger(__name__)

Step4Item = dict[str, Any]
Step5Item = dict[str, Any]

# ReportChain.invoke({"facts": str}) -> GapNarrative
ReportChain = Callable[[dict[str, str]], GapNarrative]


# ---------------------------------------------------------------------------
# Local helper copies (see module docstring for why these are not imported
# from narrative_service).
# ---------------------------------------------------------------------------


def _clean_text(value: Any) -> str:
    if value is None:
        return ""

    return str(value).strip()


def _clean_number(value: Any) -> Optional[float]:
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


METRICS = ["price", "data", "voice", "idd", "sms", "validity"]


def _metric_details(step4_item: Step4Item, metric: str) -> dict[str, Any]:
    return (step4_item.get("metric_gaps", {}) or {}).get(metric, {}) or {}


def _competitor_advantage_metrics(step4_item: Step4Item) -> list[str]:
    return [
        metric.upper()
        for metric in METRICS
        if _metric_details(step4_item, metric).get("position") == "COMPETITOR_ADVANTAGE"
    ]


def _omantel_advantage_metrics(step4_item: Step4Item) -> list[str]:
    return [
        metric.upper()
        for metric in METRICS
        if _metric_details(step4_item, metric).get("position") == "OMANTEL_ADVANTAGE"
    ]


def _capability_gap_names(step4_item: Step4Item) -> list[str]:
    gaps = step4_item.get("capability_gaps", []) or []

    return [
        _clean_text(item.get("capability")).upper()
        for item in gaps
        if _clean_text(item.get("capability"))
    ]


def _primary_attention_area(step4_item: Step4Item) -> str:
    weighted = step4_item.get("weighted_position", {}) or {}
    contributions = weighted.get("weighted_contributions", {}) or {}

    negative: dict[str, float] = {}

    for metric, value in contributions.items():
        value = _clean_number(value)

        if value is not None and value < 0:
            negative[metric.upper()] = value

    if negative:
        return min(negative, key=negative.get)

    capabilities = _capability_gap_names(step4_item)

    if capabilities:
        return capabilities[0]

    return "BALANCED"


# ---------------------------------------------------------------------------
# Prompt / structured output
# ---------------------------------------------------------------------------

report_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
You are the reporting explanation layer for
Omantel's Market Pulse Agent.

You receive factual, already-calculated results
comparing one Ooredoo plan with one matched
Omantel plan.

Your job is ONLY to explain the findings clearly
for a telecom product/campaign team.

STRICT RULES:

1. Use only the facts provided.
2. Do not recalculate scores.
3. Do not change risk levels.
4. Do not invent missing benefits.
5. Do not invent internal Omantel business reasons.
6. Do not claim why Omantel originally designed
   or priced a product in a certain way.
7. You may explain WHAT measurable factors drive
   the competitive gap.
8. Mention Omantel advantages as well as
   competitor advantages.
9. Explain why the issue matters using customer
   or revenue exposure when provided.
10. A separate addon does not mean the matched
    product has native parity.
11. Keep each field concise and business-friendly.

Definitions:

gap_summary:
One short overall comparison.

key_issue:
The main measurable competitive issue.

business_explanation:
A short explanation of what drives the gap and
why it matters commercially.
""",
        ),
        (
            "human",
            """
FACTUAL ANALYSIS:

{facts}
""",
        ),
    ]
)


def get_llm_client(settings: Optional[Settings] = None) -> ChatOpenAI:
    """Construct the OpenAI-compatible chat client used for narrative generation.

    Configuration (model name, temperature, retries, API key, base URL) is
    sourced from ``market_pulse.config.settings`` -- never hardcoded or
    prompted for interactively. This reuses the same Settings-driven
    convention as Steps 1-3's LLM modules, rather than the reference
    notebook's local Ollama experiment.
    """

    settings = settings or get_settings()

    return ChatOpenAI(
        model=settings.openai_model,
        temperature=settings.openai_temperature,
        max_retries=settings.openai_max_retries,
        api_key=settings.openai_api_key or None,
        base_url=settings.openai_base_url,
    )


def get_report_chain(llm: Optional[ChatOpenAI] = None) -> ReportChain:
    """Build the narrative chain: prompt | structured-output LLM.

    Note: like Steps 2/3's chains, the reference does NOT pass ``strict=True``
    here. Preserve that difference.
    """

    llm = llm or get_llm_client()

    structured_llm = llm.with_structured_output(GapNarrative, method="json_schema")

    return report_prompt | structured_llm


# ---------------------------------------------------------------------------
# Facts payload / deterministic fallback
# ---------------------------------------------------------------------------


def build_llm_facts(step4_item: Step4Item, step5_item: Step5Item) -> dict[str, Any]:
    """Build the JSON-serializable facts payload sent to the LLM.

    Mirrors the reference's ``build_llm_facts`` exactly.
    """

    metrics: dict[str, Any] = {}

    for metric in METRICS:
        details = _metric_details(step4_item, metric)

        metrics[metric] = {
            "competitor": details.get("competitor"),
            "omantel": details.get("omantel"),
            "gap_pct": details.get("gap_pct"),
            "position": details.get("position"),
        }

    capability_gaps = []

    for item in step4_item.get("capability_gaps", []) or []:
        capability_gaps.append(
            {
                "capability": item.get("capability"),
                "status": item.get("status"),
                "separate_omantel_offer_exists": item.get("separate_omantel_offer_exists"),
            }
        )

    weighted_position = step4_item.get("weighted_position", {}) or {}

    return {
        "competitor_plan": step4_item.get("competitor_plan"),
        "omantel_plan": step4_item.get("omantel_plan"),
        "product_type": step4_item.get("product_type"),
        "metric_gaps": metrics,
        "competitor_advantages": _competitor_advantage_metrics(step4_item),
        "omantel_advantages": _omantel_advantage_metrics(step4_item),
        "primary_attention_area": _primary_attention_area(step4_item),
        "capability_gaps": capability_gaps,
        "commercial_position_score": weighted_position.get("commercial_position_score"),
        "commercial_position": weighted_position.get("overall_position"),
        "competitive_threat_score": step5_item.get("competitive_threat_score"),
        "customer_exposure_score": step5_item.get("customer_exposure_score"),
        "revenue_exposure_score": step5_item.get("revenue_exposure_score"),
        "business_exposure_score": step5_item.get("business_exposure_score"),
        "risk_score": step5_item.get("risk_score"),
        "risk_level": step5_item.get("risk_level"),
    }


def fallback_narrative(step4_item: Step4Item, step5_item: Step5Item) -> dict[str, str]:
    """Deterministic, LLM-free narrative construction.

    Mirrors the reference's ``fallback_narrative`` exactly (including the
    exact conditional-append structure and punctuation/spacing) -- used
    whenever the LLM call fails, so a failed LLM call still produces a
    complete, usable narrative.
    """

    competitor = _clean_text(step4_item.get("competitor_plan"))
    omantel = _clean_text(step4_item.get("omantel_plan"))

    competitor_gaps = _competitor_advantage_metrics(step4_item)
    omantel_gaps = _omantel_advantage_metrics(step4_item)
    capabilities = _capability_gap_names(step4_item)

    issues = competitor_gaps + capabilities

    if issues:
        issue_text = ", ".join(issues)
    else:
        issue_text = "no major measured gap"

    if omantel_gaps:
        oman_strength = (
            " Omantel retains an advantage in " + ", ".join(omantel_gaps) + "."
        )
    else:
        oman_strength = ""

    risk_level = _clean_text(step5_item.get("risk_level"))
    exposure = _clean_number(step5_item.get("business_exposure_score"))

    gap_summary = (
        f"{competitor} is compared with {omantel}; the main measured issue is "
        f"{issue_text}."
    )

    key_issue = _primary_attention_area(step4_item)

    business_explanation = f"The competitive position is driven by {issue_text}." + oman_strength

    if risk_level:
        business_explanation += f" The current risk level is {risk_level}."

    if exposure is not None:
        business_explanation += f" Business exposure is {exposure:.1f}/100."

    return {
        "gap_summary": gap_summary,
        "key_issue": key_issue,
        "business_explanation": business_explanation,
    }


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def generate_narrative(
    step4_item: Step4Item,
    step5_item: Step5Item,
    chain: Optional[ReportChain] = None,
) -> tuple[dict[str, str], str]:
    """Generate a gap narrative for one matched competitor/Omantel pair.

    Tries the LLM chain first; on ANY failure (network, schema validation,
    provider error, etc.) falls back to ``fallback_narrative`` so this
    function always returns a complete, usable narrative -- never raises for
    an LLM failure. This graceful-degradation behavior mirrors the
    reference's ``try/except`` in its main narrative-generation loop exactly.

    Returns ``(narrative_dict, narrative_source)`` where ``narrative_source``
    is ``"LLM_GENERATED"`` on success or ``"DETERMINISTIC_FALLBACK"`` on
    failure (see module docstring for the ``"GPT-OSS"`` -> ``"LLM_GENERATED"``
    rename rationale).
    """

    facts = build_llm_facts(step4_item, step5_item)

    try:
        chain = chain or get_report_chain()

        narrative = chain.invoke(
            {"facts": json.dumps(facts, ensure_ascii=False, indent=2)}
        )

        return narrative.model_dump(), "LLM_GENERATED"

    except Exception as exc:  # noqa: BLE001 - intentional graceful-degradation boundary
        logger.warning("LLM fallback used: %s", exc)

        return fallback_narrative(step4_item, step5_item), "DETERMINISTIC_FALLBACK"
