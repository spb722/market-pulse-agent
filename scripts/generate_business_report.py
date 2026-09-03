"""Build the self-contained business-review HTML report from real run data.

Reads one or more completed competitor runs from local file storage
(``runs/<run_id>/...``), assembles a single JSON dataset (per-competitor
summaries + a flat per-plan record list + the no-match list), and injects it
into ``scripts/report_template.html`` to produce
``reports/market_pulse_business_report.html`` -- a single file with no
external dependencies, safe to open directly (``file://``) or host anywhere.

Usage:
    python scripts/generate_business_report.py
    python scripts/generate_business_report.py --run-id RUN-XXXX
    python scripts/generate_business_report.py --run RUN-XXXX:CR-YYYY:CompetitorName ...

With no arguments, regenerates the report from the two real runs already
captured in this repo (Ooredoo, Vodafone).
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from market_pulse.config.settings import Settings, get_settings
from market_pulse.llm.langfuse_metrics import flush_langfuse
from market_pulse.schemas.portfolio import PortfolioSegmentAdvice
from market_pulse.services.portfolio_analysis_service import build_portfolio_analysis
from market_pulse.storage.file_repository import FileRunRepository

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = REPO_ROOT / "runs"
TEMPLATE_PATH = Path(__file__).resolve().parent / "report_template.html"
OUTPUT_PATH = REPO_ROOT / "reports" / "market_pulse_business_report.html"

DEFAULT_RUNS = [
    ("RUN-6F797955", "CR-8C843A48", "Ooredoo"),
    ("RUN-8AF606BE", "CR-00DD16F0", "Vodafone"),
]

# narrative_generation.json's per-plan records use the reference's Title-Case
# column names (see build_report_record in narrative_service.py) -- map to
# snake_case for straightforward JS access in the report.
RECORD_FIELD_MAP = {
    "Competitor Plan ID": "competitor_plan_id",
    "Competitor Plan": "competitor_plan",
    "Omantel Plan ID": "omantel_plan_id",
    "Omantel Plan": "omantel_plan",
    "Category": "category",
    "Product Type": "product_type",
    "Similarity": "similarity_score",
    "Match Confidence": "match_confidence",
    "Gap Analysis Status": "gap_analysis_status",
    "Commercial Position Score": "commercial_position_score",
    "Commercial Position": "overall_position",
    "Primary Attention Area": "primary_attention_area",
    "Competitor Advantages": "competitor_advantages",
    "Omantel Advantages": "omantel_advantages",
    "Capability Gaps": "capability_gaps",
    "Competitive Threat": "competitive_threat_score",
    "Avg Active Users 6M": "avg_active_users_6m",
    "Avg Product ARPU 6M": "avg_product_arpu_6m",
    "Avg Monthly Revenue 6M": "avg_monthly_revenue_6m",
    "Customer Exposure": "customer_exposure_score",
    "Revenue Exposure": "revenue_exposure_score",
    "Business Exposure": "business_exposure_score",
    "Risk Score": "risk_score",
    "Risk Level": "risk_level",
    "Risk Status": "risk_status",
    "Risk Reasons": "risk_reasons",
    "gap_summary": "gap_summary",
    "key_issue": "key_issue",
    "business_explanation": "business_explanation",
    "narrative_source": "narrative_source",
}

METRICS = ["PRICE", "DATA", "VOICE", "IDD", "SMS", "VALIDITY"]

NO_MATCH_FIELD_MAP = {
    "Competitor Plan": "competitor_plan",
    "Category": "category",
    "Role": "role",
    "Product Type": "product_type",
    "Omantel Match": "omantel_match",
    "Similarity": "similarity_score",
    "Match Confidence": "match_confidence",
    "Match Status": "match_status",
    "Reason": "reason",
}


def _stage_result(run_id: str, competitor_run_id: str, stage: str) -> Any:
    path = RUNS_DIR / run_id / "competitors" / competitor_run_id / "stages" / f"{stage}.json"
    return json.loads(path.read_text(encoding="utf-8"))["result"]


def _omantel_count(run_id: str) -> int:
    path = RUNS_DIR / run_id / "omantel" / "stage_result.json"
    result = json.loads(path.read_text(encoding="utf-8"))["result"]
    enriched_plans = result[0] if isinstance(result, list) else result.get("enriched_plans", [])
    return len(enriched_plans)


def _count_by(items: list[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = item.get(key)
        if value is None:
            continue
        counts[value] = counts.get(value, 0) + 1
    return counts


def discover_run_specs(run_id: str) -> list[tuple[str, str, str]]:
    """Discover every completed competitor under one parent run."""

    competitors_dir = RUNS_DIR / run_id / "competitors"
    if not competitors_dir.is_dir():
        raise ValueError(f"Run {run_id!r} has no competitors directory.")

    specs: list[tuple[str, str, str]] = []
    unfinished: list[str] = []
    for metadata_path in sorted(competitors_dir.glob("*/competitor_run.json")):
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        competitor_run_id = str(metadata.get("competitor_run_id") or metadata_path.parent.name)
        status = metadata.get("status")
        if status != "COMPLETED":
            unfinished.append(f"{competitor_run_id} ({status or 'UNKNOWN'})")
            continue
        competitor = str(metadata.get("competitor") or competitor_run_id)
        specs.append((run_id, competitor_run_id, competitor.title()))

    if unfinished:
        raise ValueError(
            f"Run {run_id!r} is not ready for reporting; unfinished competitor runs: "
            + ", ".join(unfinished)
        )
    if not specs:
        raise ValueError(f"Run {run_id!r} has no completed competitors.")
    specs.sort(key=lambda spec: (spec[2].lower(), spec[1]))
    return specs


def _map_record(raw: dict[str, Any], competitor: str) -> dict[str, Any]:
    record = {"competitor": competitor}
    for src_key, dst_key in RECORD_FIELD_MAP.items():
        record[dst_key] = raw.get(src_key)
    record["metric_gaps"] = {
        metric.lower(): {
            "competitor": raw.get(f"{metric} Competitor"),
            "omantel": raw.get(f"{metric} Omantel"),
            "gap_pct": raw.get(f"{metric} Gap %"),
            "position": raw.get(f"{metric} Position"),
        }
        for metric in METRICS
    }
    return record


def _map_no_match(raw: dict[str, Any], competitor: str) -> dict[str, Any]:
    record = {"competitor": competitor}
    for src_key, dst_key in NO_MATCH_FIELD_MAP.items():
        record[dst_key] = raw.get(src_key)
    return record


def build_competitor_dataset(run_id: str, competitor_run_id: str, name: str) -> tuple[dict, list[dict], list[dict]]:
    comp_norm = _stage_result(run_id, competitor_run_id, "competitor_normalization")
    matching = _stage_result(run_id, competitor_run_id, "plan_matching")
    gap = _stage_result(run_id, competitor_run_id, "gap_analysis")
    risk = _stage_result(run_id, competitor_run_id, "risk_analysis")
    narrative = _stage_result(run_id, competitor_run_id, "narrative_generation")

    enriched_plans = comp_norm["enriched_plans"]

    analyzed = [r for r in gap if r.get("gap_analysis_status") == "ANALYZED"]
    scored = [r for r in risk if r.get("risk_status") == "SCORED"]

    commercial_scores = [
        r["weighted_position"]["commercial_position_score"]
        for r in analyzed
        if r.get("weighted_position", {}).get("commercial_position_score") is not None
    ]
    risk_scores = [r["risk_score"] for r in scored if r.get("risk_score") is not None]

    records = [_map_record(r, name) for r in narrative["records"]]
    no_match = [_map_no_match(r, name) for r in narrative["no_match_report"]]

    summary = {
        "name": name,
        "run_id": run_id,
        "competitor_run_id": competitor_run_id,
        "total_plans": len(enriched_plans),
        "category_counts": _count_by(enriched_plans, "category"),
        "product_type_counts": _count_by(matching, "product_type"),
        "match_status_counts": _count_by(matching, "match_status"),
        "overall_position_counts": _count_by(
            [{"pos": r["weighted_position"]["overall_position"]} for r in analyzed], "pos"
        ),
        "risk_level_counts": _count_by(scored, "risk_level"),
        "analyzed_count": len(analyzed),
        "scored_count": len(scored),
        "narrative_generated_count": sum(
            1 for r in narrative["records"] if r.get("narrative_source") == "LLM_GENERATED"
        ),
        "avg_commercial_position_score": (
            round(statistics.mean(commercial_scores), 2) if commercial_scores else None
        ),
        "min_commercial_position_score": (
            round(min(commercial_scores), 2) if commercial_scores else None
        ),
        "avg_risk_score": round(statistics.mean(risk_scores), 2) if risk_scores else None,
        "max_risk_score": round(max(risk_scores), 2) if risk_scores else None,
    }

    return summary, records, no_match


def build_report_dataset(
    run_specs: list[tuple[str, str, str]],
    *,
    analysis_run_id: str,
    advisor: Callable[[dict[str, Any]], PortfolioSegmentAdvice] | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Assemble deterministic report data and run-level executive advice."""

    settings = settings or get_settings()
    competitors = []
    all_records: list[dict] = []
    all_no_match: list[dict] = []
    omantel_products = None

    for run_id, competitor_run_id, name in run_specs:
        summary, records, no_match = build_competitor_dataset(run_id, competitor_run_id, name)
        competitors.append(summary)
        all_records.extend(records)
        all_no_match.extend(no_match)
        count = _omantel_count(run_id)
        omantel_products = count if omantel_products is None else max(omantel_products, count)

    portfolio_kwargs: dict[str, Any] = {}
    if advisor is not None:
        portfolio_kwargs["advisor"] = advisor
    portfolio_analysis = build_portfolio_analysis(
        all_records,
        run_id=analysis_run_id,
        minimum_risk=settings.portfolio_analysis_minimum_risk,
        settings=settings,
        **portfolio_kwargs,
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": analysis_run_id,
        "omantel_atl_products": omantel_products,
        "competitors": competitors,
        "portfolio_analysis": portfolio_analysis,
        "records": all_records,
        "no_match": all_no_match,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--run-id",
        help="Discover and include every completed competitor under this parent run.",
    )
    source.add_argument(
        "--run",
        action="append",
        dest="runs",
        metavar="RUN_ID:COMPETITOR_RUN_ID:NAME",
        help="A completed competitor run to include. Repeatable. Defaults to the two real runs already in this repo.",
    )
    args = parser.parse_args()

    if args.run_id:
        run_specs = discover_run_specs(args.run_id)
        analysis_run_id = args.run_id
    elif args.runs:
        run_specs = []
        for spec in args.runs:
            run_id, competitor_run_id, name = spec.split(":", 2)
            run_specs.append((run_id, competitor_run_id, name))
        distinct_run_ids = sorted({spec[0] for spec in run_specs})
        analysis_run_id = (
            distinct_run_ids[0]
            if len(distinct_run_ids) == 1
            else "MULTI-RUN:" + ",".join(distinct_run_ids)
        )
    else:
        run_specs = DEFAULT_RUNS
        analysis_run_id = "MULTI-RUN:" + ",".join(sorted({spec[0] for spec in run_specs}))

    settings = get_settings()
    try:
        dataset = build_report_dataset(
            run_specs,
            analysis_run_id=analysis_run_id,
            settings=settings,
        )

        if args.run_id:
            FileRunRepository(RUNS_DIR).save_portfolio_analysis(
                args.run_id,
                {
                    "run_id": args.run_id,
                    "generated_at": dataset["generated_at"],
                    "competitor_run_ids": [spec[1] for spec in run_specs],
                    "result": dataset["portfolio_analysis"],
                },
            )

        template = TEMPLATE_PATH.read_text(encoding="utf-8")
        output = template.replace(
            "/*__MARKET_PULSE_DATA__*/null", json.dumps(dataset, ensure_ascii=False)
        )

        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text(output, encoding="utf-8")
    finally:
        flush_langfuse(settings)

    portfolio = dataset["portfolio_analysis"]
    print(
        f"Wrote {OUTPUT_PATH} ({len(dataset['records'])} records, "
        f"{len(dataset['no_match'])} no-match, {len(dataset['competitors'])} competitors, "
        f"{portfolio['affected_omantel_plans']} executive decisions across "
        f"{portfolio['risky_segments']} risky segments)"
    )


if __name__ == "__main__":
    main()
