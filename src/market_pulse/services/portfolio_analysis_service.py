"""Run-level executive comparison and batched portfolio recommendations."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Callable

from market_pulse.config.settings import Settings, get_settings
from market_pulse.llm.langfuse_metrics import llm_workflow_span, update_workflow_span
from market_pulse.llm.portfolio_advisor import generate_segment_advice
from market_pulse.schemas.portfolio import PortfolioRecommendation, PortfolioSegmentAdvice

logger = logging.getLogger(__name__)

Advisor = Callable[[dict[str, Any]], PortfolioSegmentAdvice]
ANALYSIS_VERSION = "portfolio-advice-v3"


def _number(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _segment_key(record: dict[str, Any]) -> tuple[str, str]:
    return (
        str(record.get("category") or "uncategorized").lower(),
        str(record.get("product_type") or "OTHER").upper(),
    )


def _is_positive_risk(record: dict[str, Any], minimum_risk: float) -> bool:
    risk = _number(record.get("risk_score"))
    return (
        record.get("risk_status") == "SCORED"
        and bool(record.get("omantel_plan_id"))
        and risk is not None
        and risk > minimum_risk
    )


def _comparison_fact(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "competitor": record.get("competitor"),
        "competitor_plan_id": record.get("competitor_plan_id"),
        "competitor_plan": record.get("competitor_plan"),
        "similarity_score": record.get("similarity_score"),
        "risk_score": record.get("risk_score"),
        "risk_level": record.get("risk_level"),
        "primary_attention_area": record.get("primary_attention_area"),
        "competitor_advantages": record.get("competitor_advantages"),
        "omantel_advantages": record.get("omantel_advantages"),
        "capability_gaps": record.get("capability_gaps"),
        "customer_exposure_score": record.get("customer_exposure_score"),
        "revenue_exposure_score": record.get("revenue_exposure_score"),
        "business_exposure_score": record.get("business_exposure_score"),
        "avg_active_users_6m": record.get("avg_active_users_6m"),
        "avg_monthly_revenue_6m": record.get("avg_monthly_revenue_6m"),
        "metric_gaps": record.get("metric_gaps") or {},
    }


def _main_gap_names(comparisons: list[dict[str, Any]]) -> list[str]:
    gaps: set[str] = set()
    for comparison in comparisons:
        if (_number(comparison.get("risk_score")) or 0) <= 0:
            continue
        for metric, detail in (comparison.get("metric_gaps") or {}).items():
            if (detail or {}).get("position") == "COMPETITOR_ADVANTAGE":
                gaps.add(str(metric).upper())
        capability_text = str(comparison.get("capability_gaps") or "")
        for capability in capability_text.split(","):
            capability = capability.strip().split(" ", 1)[0].upper()
            if capability:
                gaps.add(capability)
    return sorted(gaps)


def _evidence_confidence(comparisons: list[dict[str, Any]]) -> str:
    similarities = [
        similarity
        for item in comparisons
        if (_number(item.get("risk_score")) or 0) > 0
        for similarity in [_number(item.get("similarity_score"))]
        if similarity is not None
    ]
    strongest = max(similarities, default=0)
    if strongest >= 0.8:
        return "HIGH"
    if strongest >= 0.65:
        return "MEDIUM"
    return "LOW"


def _fallback_recommendation(plan: dict[str, Any]) -> PortfolioRecommendation:
    gaps = plan["main_gaps"]
    gap_text = ", ".join(gaps) if gaps else "the measured proposition"
    if plan["evidence_confidence"] == "LOW":
        decision = "INVESTIGATE"
        action = (
            f"Validate whether customers treat the listed offers as substitutes before changing "
            f"{plan['omantel_plan']}; then review {gap_text.lower()} if the comparison is confirmed."
        )
    elif gaps == ["PRICE"]:
        decision = "REPRICE"
        action = (
            f"Review the price positioning of {plan['omantel_plan']} against the listed offers "
            "while preserving its measured advantages."
        )
    elif len(gaps) > 1:
        decision = "REPACKAGE"
        action = (
            f"Review the {gap_text.lower()} bundle for {plan['omantel_plan']} against the listed "
            "offers while preserving Omantel's measured advantages."
        )
    elif gaps:
        decision = "ENHANCE"
        action = (
            f"Review the {gap_text.lower()} allowance or capability in {plan['omantel_plan']} "
            "against the listed competitor offers."
        )
    else:
        decision = "MONITOR"
        action = f"Monitor {plan['omantel_plan']} and validate the measured risk before changing it."

    return PortfolioRecommendation(
        omantel_plan_id=plan["omantel_plan_id"],
        decision=decision,
        suggested_action=action,
    )


def _calculated_rationale(plan: dict[str, Any]) -> str:
    """Explain materiality using calculated fields only, without model inference."""

    competitors = sorted(
        {
            str(item.get("competitor"))
            for item in plan["comparisons"]
            if (_number(item.get("risk_score")) or 0) > 0 and item.get("competitor")
        }
    )
    if not competitors:
        competitor_text = "The compared competitor"
        competitor_verb = "has"
    elif len(competitors) == 1:
        competitor_text = competitors[0]
        competitor_verb = "has"
    else:
        competitor_text = ", ".join(competitors[:-1]) + f" and {competitors[-1]}"
        competitor_verb = "have"
    risk_text = f'{plan["headline_risk_score"]:.2f}'.rstrip("0").rstrip(".")
    gaps = plan["main_gaps"]
    gap_text = ", ".join(gaps) if gaps else "no named metric gap"
    return (
        f"{competitor_text} {competitor_verb} a positive calculated risk for this plan. "
        "The highest pairwise "
        f"score is {risk_text} ({plan['headline_risk_level']}); measured gaps: {gap_text}. "
        f"Evidence confidence is {plan['evidence_confidence'].lower()}."
    )


def _plan_fact(
    omantel_plan_id: str,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    comparisons = [_comparison_fact(record) for record in records]
    comparisons.sort(
        key=lambda item: (
            str(item.get("competitor") or ""),
            -(_number(item.get("risk_score")) or 0),
            str(item.get("competitor_plan") or ""),
        )
    )
    positive = [item for item in comparisons if (_number(item.get("risk_score")) or 0) > 0]
    headline = max(positive, key=lambda item: _number(item.get("risk_score")) or 0)
    return {
        "omantel_plan_id": omantel_plan_id,
        "omantel_plan": next(
            (record.get("omantel_plan") for record in records if record.get("omantel_plan")),
            omantel_plan_id,
        ),
        "headline_risk_score": _number(headline.get("risk_score")) or 0,
        "headline_risk_level": headline.get("risk_level") or "NOT_SCORED",
        "evidence_confidence": _evidence_confidence(comparisons),
        "main_gaps": _main_gap_names(comparisons),
        "comparisons": comparisons,
    }


def _segment_facts(
    segment: tuple[str, str],
    affected_ids: set[str],
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    category, product_type = segment
    records_by_plan: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if (
            _segment_key(record) == segment
            and record.get("omantel_plan_id") in affected_ids
            and record.get("gap_analysis_status") == "ANALYZED"
        ):
            records_by_plan[str(record["omantel_plan_id"])].append(record)

    plans = [
        _plan_fact(plan_id, records_by_plan[plan_id])
        for plan_id in sorted(records_by_plan)
    ]
    plans.sort(key=lambda plan: (-plan["headline_risk_score"], plan["omantel_plan_id"]))
    return {
        "segment_key": f"{category}:{product_type}",
        "category": category,
        "product_type": product_type,
        "plans": plans,
    }


def build_portfolio_analysis(
    records: list[dict[str, Any]],
    *,
    run_id: str,
    minimum_risk: float = 0,
    advisor: Advisor = generate_segment_advice,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Build an executive decision dataset with one LLM call per risky segment."""

    settings = settings or get_settings()
    affected_by_segment: dict[tuple[str, str], set[str]] = defaultdict(set)
    positive_records = [record for record in records if _is_positive_risk(record, minimum_risk)]
    for record in positive_records:
        affected_by_segment[_segment_key(record)].add(str(record["omantel_plan_id"]))

    segment_payloads = [
        _segment_facts(segment, affected_by_segment[segment], records)
        for segment in sorted(affected_by_segment)
    ]
    rows: list[dict[str, Any]] = []
    group_summaries: list[dict[str, Any]] = []

    with llm_workflow_span(
        name="portfolio_analysis",
        settings=settings,
        metadata={"run_id": run_id, "analysis_version": ANALYSIS_VERSION},
        input_payload={
            "segments": [payload["segment_key"] for payload in segment_payloads],
            "positive_risk_comparisons": len(positive_records),
        },
    ) as workflow_span:
        for payload in segment_payloads:
            expected_ids = {plan["omantel_plan_id"] for plan in payload["plans"]}
            advice: PortfolioSegmentAdvice | None = None
            try:
                advice = advisor(payload)
            except Exception as exc:  # noqa: BLE001 - report generation must degrade gracefully
                logger.warning(
                    "Portfolio advice fallback used for segment %s: %s",
                    payload["segment_key"],
                    exc,
                )

            recommendations: dict[str, PortfolioRecommendation] = {}
            if advice is not None:
                for recommendation in advice.recommendations:
                    plan_id = recommendation.omantel_plan_id
                    if plan_id not in expected_ids or plan_id in recommendations:
                        logger.warning(
                            "Ignoring invalid or duplicate portfolio recommendation id %r for %s",
                            plan_id,
                            payload["segment_key"],
                        )
                        continue
                    recommendations[plan_id] = recommendation

            group_source = "LLM_GENERATED" if advice is not None else "DETERMINISTIC_FALLBACK"
            group_summaries.append(
                {
                    "segment_key": payload["segment_key"],
                    "category": payload["category"],
                    "product_type": payload["product_type"],
                    "summary": (
                        advice.segment_summary
                        if advice is not None
                        else (
                            f"{len(payload['plans'])} Omantel plan(s) in this segment have "
                            "positive calculated risk."
                        )
                    ),
                    "source": group_source,
                }
            )

            for plan in payload["plans"]:
                recommendation = recommendations.get(plan["omantel_plan_id"])
                source = "LLM_GENERATED"
                if recommendation is None:
                    recommendation = _fallback_recommendation(plan)
                    source = "DETERMINISTIC_FALLBACK"
                rows.append(
                    {
                        "segment_key": payload["segment_key"],
                        "category": payload["category"],
                        "product_type": payload["product_type"],
                        **plan,
                        **recommendation.model_dump(exclude={"omantel_plan_id"}),
                        "why_it_matters": _calculated_rationale(plan),
                        "analysis_source": source,
                    }
                )

        rows.sort(key=lambda row: (-row["headline_risk_score"], row["omantel_plan_id"]))
        for priority, row in enumerate(rows, start=1):
            row["priority"] = priority

        result = {
            "run_id": run_id,
            "analysis_version": ANALYSIS_VERSION,
            "minimum_risk": minimum_risk,
            "positive_risk_comparisons": len(positive_records),
            "affected_omantel_plans": len(rows),
            "risky_segments": len(segment_payloads),
            "groups": group_summaries,
            "rows": rows,
        }
        update_workflow_span(
            workflow_span,
            {
                "risky_segments": len(segment_payloads),
                "affected_omantel_plans": len(rows),
                "recommendations": [
                    {
                        "omantel_plan_id": row["omantel_plan_id"],
                        "decision": row["decision"],
                        "why_it_matters": row["why_it_matters"],
                        "suggested_action": row["suggested_action"],
                        "analysis_source": row["analysis_source"],
                    }
                    for row in rows
                ],
            },
            settings=settings,
        )
        return result
