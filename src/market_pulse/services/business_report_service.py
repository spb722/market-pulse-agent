"""Shared report assembly for the CLI and run-based report API.

Reads completed stage results only; competitor processing and risk scoring are
never invoked here. Executive advice uses the existing cached/traced service.
"""

from __future__ import annotations

import json
import logging
import os
import re
import statistics
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Callable

from market_pulse.config.settings import Settings, get_settings
from market_pulse.llm.langfuse_metrics import flush_langfuse
from market_pulse.schemas.portfolio import PortfolioSegmentAdvice
from market_pulse.schemas.runs import STAGE_NAMES, ReportJob, utcnow
from market_pulse.services.portfolio_analysis_service import build_portfolio_analysis
from market_pulse.storage.file_repository import FileRunRepository

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_PATH = REPO_ROOT / "scripts" / "report_template.html"
logger = logging.getLogger(__name__)

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


def _stage_result(
    run_id: str, competitor_run_id: str, stage: str, repo: FileRunRepository
) -> Any:
    result = repo.get_stage_result(run_id, competitor_run_id, stage)
    if result is None or result.status != "COMPLETED" or result.result is None:
        raise ValueError(f"Stage {stage!r} for {competitor_run_id!r} is not ready for reporting.")
    return result.result


def _omantel_count(run_id: str, repo: FileRunRepository) -> int:
    stage = repo.get_omantel_stage_result(run_id)
    if stage is None or stage.status != "COMPLETED" or stage.result is None:
        raise ValueError("Omantel reference is not ready for reporting.")
    result = stage.result
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


def discover_run_specs(
    run_id: str, repo: FileRunRepository | None = None
) -> list[tuple[str, str, str]]:
    """Validate all saved inputs and discover the completed competitors."""
    if not re.fullmatch(r"RUN-[A-Za-z0-9_-]+", run_id):
        raise ValueError("Invalid run ID.")
    repo = repo or FileRunRepository(get_settings().runs_dir)
    if repo.get_run(run_id) is None:
        raise FileNotFoundError(f"Run {run_id!r} not found.")
    competitors = repo.list_competitor_runs(run_id)
    if not competitors:
        raise ValueError(f"Run {run_id!r} has no completed competitors.")
    unfinished = [cr.competitor_run_id for cr in competitors if cr.status != "COMPLETED"]
    if unfinished:
        raise ValueError("Unfinished competitor runs: " + ", ".join(unfinished))
    _omantel_count(run_id, repo)
    for cr in competitors:
        for stage in STAGE_NAMES:
            _stage_result(run_id, cr.competitor_run_id, stage, repo)
    return sorted(
        [(run_id, cr.competitor_run_id, cr.competitor.title()) for cr in competitors],
        key=lambda spec: (spec[2].lower(), spec[1]),
    )


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


def build_competitor_dataset(
    run_id: str, competitor_run_id: str, name: str, *, repo: FileRunRepository
) -> tuple[dict, list[dict], list[dict]]:
    comp_norm = _stage_result(run_id, competitor_run_id, "competitor_normalization", repo)
    matching = _stage_result(run_id, competitor_run_id, "plan_matching", repo)
    gap = _stage_result(run_id, competitor_run_id, "gap_analysis", repo)
    risk = _stage_result(run_id, competitor_run_id, "risk_analysis", repo)
    narrative = _stage_result(run_id, competitor_run_id, "narrative_generation", repo)

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
    repo: FileRunRepository | None = None,
) -> dict[str, Any]:
    """Assemble deterministic report data and run-level executive advice."""

    settings = settings or get_settings()
    repo = repo or FileRunRepository(settings.runs_dir)
    competitors = []
    all_records: list[dict] = []
    all_no_match: list[dict] = []
    omantel_products = None

    for run_id, competitor_run_id, name in run_specs:
        summary, records, no_match = build_competitor_dataset(run_id, competitor_run_id, name, repo=repo)
        competitors.append(summary)
        all_records.extend(records)
        all_no_match.extend(no_match)
        count = _omantel_count(run_id, repo)
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


def write_business_report(
    run_specs: list[tuple[str, str, str]],
    *,
    analysis_run_id: str,
    repo: FileRunRepository,
    settings: Settings,
    output_path: Path,
    persist_portfolio: bool = False,
    advisor: Callable[[dict[str, Any]], PortfolioSegmentAdvice] | None = None,
) -> dict[str, Any]:
    """Render saved results atomically, sharing the existing LLM/cache path."""
    try:
        dataset = build_report_dataset(
            run_specs, analysis_run_id=analysis_run_id, repo=repo,
            settings=settings, advisor=advisor,
        )
        template = TEMPLATE_PATH.read_text(encoding="utf-8")
        marker = "/*__MARKET_PULSE_DATA__*/null"
        if marker not in template:
            raise ValueError("Report template is missing its data placeholder.")
        # Keep user-supplied plan names from terminating the inline script.
        data_json = json.dumps(dataset, ensure_ascii=False).replace("<", "\\u003c")
        output = template.replace(marker, data_json)
        if persist_portfolio:
            repo.save_portfolio_analysis(analysis_run_id, {
                "run_id": analysis_run_id,
                "generated_at": dataset["generated_at"],
                "competitor_run_ids": [spec[1] for spec in run_specs],
                "result": dataset["portfolio_analysis"],
            })
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=output_path.parent, prefix=".report-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(output)
            os.replace(temporary, output_path)
        except BaseException:
            if os.path.exists(temporary):
                os.remove(temporary)
            raise
        return dataset
    finally:
        flush_langfuse(settings)


def reserve_report_generation(
    run_id: str, repo: FileRunRepository
) -> tuple[ReportJob, BinaryIO | None]:
    """Validate readiness and reserve one build, or return the active job."""
    if not re.fullmatch(r"RUN-[A-Za-z0-9_-]+", run_id):
        raise ValueError("Invalid run ID.")
    if repo.get_run(run_id) is None:
        raise FileNotFoundError(f"Run {run_id!r} not found.")
    lock = repo.acquire_report_lock(run_id)
    if lock is None:
        # Another process/thread owns the build, including its brief start/end
        # transitions. Never return an old completed path as this job's result.
        return ReportJob(run_id=run_id, report_status="PROCESSING"), None
    try:
        discover_run_specs(run_id, repo)
        job = ReportJob(run_id=run_id, report_status="PROCESSING", started_at=utcnow())
        repo.save_report_job(job)
        return job, lock
    except BaseException:
        lock.close()
        raise


def generate_run_report(
    run_id: str,
    repo: FileRunRepository,
    settings: Settings,
    *,
    job: ReportJob | None = None,
    lock: BinaryIO | None = None,
) -> ReportJob:
    """Run synchronously for the CLI or as an API background task.

    The lock is also released on process exit, so resubmission after an
    interrupted server run can safely start a replacement build.
    """
    if lock is None:
        job, lock = reserve_report_generation(run_id, repo)
        if lock is None:
            return job
    assert job is not None
    with lock:
        try:
            specs = discover_run_specs(run_id, repo)
            output_path = (
                Path(settings.reports_dir) / run_id / "market_pulse_business_report.html"
            ).resolve()
            write_business_report(
                specs, analysis_run_id=run_id, repo=repo, settings=settings,
                output_path=output_path, persist_portfolio=True,
            )
            job.report_status = "COMPLETED"
            job.report_path = str(output_path)
        except Exception:
            logger.exception("run=%s | Report generation failed", run_id)
            job.report_status = "FAILED"
            job.report_path = None
            job.report_error = "Report generation failed. Check the server logs and retry."
        job.completed_at = utcnow()
        repo.save_report_job(job)
        return job
