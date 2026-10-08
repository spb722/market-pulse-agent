"""Shared report assembly for the CLI and run-based report API.

Reads completed stage results only; competitor processing and risk scoring are
never invoked here. Executive advice uses the existing cached/traced service.
"""

from __future__ import annotations

import csv
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
from market_pulse.llm.langfuse_metrics import flush_langfuse, llm_trace
from market_pulse.schemas.portfolio import PortfolioSegmentAdvice
from market_pulse.schemas.runs import STAGE_NAMES, ReportJob, utcnow
from market_pulse.services.gap_analysis_service import clean_text, find_plan
from market_pulse.services.narrative_service import report_key
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

METRICS = ["PRICE", "DATA", "VOICE", "IDD", "SMS", "VALIDITY", "SOCIAL_DATA", "ROAMING"]

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


def _omantel_plans(
    run_id: str, repo: FileRunRepository, offer_scope: str = "ATL"
) -> list[dict]:
    if offer_scope == "ATL":
        # Keep the pre-BTL call shape for the default scope.
        stage = repo.get_omantel_stage_result(run_id)
    else:
        stage = repo.get_omantel_stage_result(run_id, offer_scope)
    if stage is None or stage.status != "COMPLETED" or stage.result is None:
        raise ValueError(f"Omantel {offer_scope} reference is not ready for reporting.")
    result = stage.result
    return result[0] if isinstance(result, list) else result.get("enriched_plans", [])


def _omantel_count(run_id: str, repo: FileRunRepository, offer_scope: str = "ATL") -> int:
    return len(_omantel_plans(run_id, repo, offer_scope))


def _scope_of(spec: tuple) -> str:
    """Offer scope of a run spec; 3-tuples (legacy/CLI) are ATL."""
    return spec[3] if len(spec) > 3 and spec[3] else "ATL"


def _extra_benefits(plans: list[dict], plan_id: Any, plan_name: Any) -> str | None:
    """Display-only lookup, resolving the plan exactly as Step 4 did."""
    if not plan_id and not plan_name:
        return None
    plan = find_plan(plans, plan_id or None, plan_name)
    if plan is None:
        return None
    return clean_text(plan.get("extra_benefits")) or None


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
) -> list[tuple[str, str, str, str]]:
    """Validate all saved inputs and select the competitors to report on.

    Rule: the latest COMPLETED submission per (competitor name, offer scope).
    Older submissions in the group are ignored (they stay stored). Any
    CREATED/PROCESSING submission in a group, or a group without a COMPLETED
    submission, is an error. Only the Omantel references for scopes present
    among the selected competitors are required.
    """
    if not re.fullmatch(r"RUN-[A-Za-z0-9_-]+", run_id):
        raise ValueError("Invalid run ID.")
    repo = repo or FileRunRepository(get_settings().runs_dir)
    if repo.get_run(run_id) is None:
        raise FileNotFoundError(f"Run {run_id!r} not found.")
    competitors = repo.list_competitor_runs(run_id)
    if not competitors:
        raise ValueError(f"Run {run_id!r} has no completed competitors.")

    groups: dict[tuple[str, str], list] = {}
    for cr in competitors:
        groups.setdefault((cr.competitor.lower().strip(), cr.offer_scope), []).append(cr)

    unfinished = [
        cr.competitor_run_id
        for group in groups.values()
        for cr in group
        if cr.status in ("CREATED", "PROCESSING")
    ]
    if unfinished:
        raise ValueError("Unfinished competitor runs: " + ", ".join(sorted(unfinished)))

    selected = []
    no_completed = []
    for group in groups.values():
        completed = [cr for cr in group if cr.status == "COMPLETED"]
        if not completed:
            no_completed.extend(cr.competitor_run_id for cr in group)
            continue
        selected.append(max(completed, key=lambda cr: (cr.created_at, cr.competitor_run_id)))
    if no_completed:
        raise ValueError(
            "No completed submission for competitor runs: " + ", ".join(sorted(no_completed))
        )

    for scope in sorted({cr.offer_scope for cr in selected}):
        _omantel_count(run_id, repo, scope)
    for cr in selected:
        for stage in STAGE_NAMES:
            _stage_result(run_id, cr.competitor_run_id, stage, repo)
    return sorted(
        [
            (run_id, cr.competitor_run_id, cr.competitor.title(), cr.offer_scope)
            for cr in selected
        ],
        key=lambda spec: (spec[2].lower(), spec[3], spec[1]),
    )


def _map_record(
    raw: dict[str, Any],
    competitor: str,
    *,
    performance_window_months: int | None = None,
) -> dict[str, Any]:
    record = {"competitor": competitor}
    for src_key, dst_key in RECORD_FIELD_MAP.items():
        record[dst_key] = raw.get(src_key)
    record["offer_scope"] = raw.get("offer_scope") or "ATL"
    record["omantel_offer_scope"] = raw.get("omantel_offer_scope") or "ATL"
    record["performance_window_months"] = performance_window_months
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
    record["offer_scope"] = raw.get("offer_scope") or "ATL"
    return record


def _performance_window(
    raw: dict[str, Any], risk_windows: dict[tuple, Any], offer_scope: str
) -> int | None:
    """Window from the narrative record, else the matching risk record; old runs: 6 (ATL)."""
    value = raw.get("performance_window_months")
    if value is None:
        value = risk_windows.get(report_key(_snake_key_view(raw)))
    if value is None and offer_scope == "ATL":
        return 6
    return value


def _snake_key_view(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "competitor_plan_id": raw.get("Competitor Plan ID"),
        "competitor_plan": raw.get("Competitor Plan"),
        "omantel_plan_id": raw.get("Omantel Plan ID"),
        "omantel_plan": raw.get("Omantel Plan"),
    }


def build_competitor_dataset(
    run_id: str,
    competitor_run_id: str,
    name: str,
    *,
    repo: FileRunRepository,
    offer_scope: str = "ATL",
    label: str | None = None,
) -> tuple[dict, list[dict], list[dict]]:
    """``name`` is the base competitor name; ``label`` (default ``name``) is the
    display name used for ``name``/``competitor`` fields."""
    label = label or name
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

    risk_windows = {
        report_key(r): r.get("performance_window_months")
        for r in risk
        if r.get("performance_window_months") is not None
    }
    records = [
        _map_record(
            r, label,
            performance_window_months=_performance_window(
                r, risk_windows, r.get("offer_scope") or "ATL"
            ),
        )
        for r in narrative["records"]
    ]
    omantel_plans = _omantel_plans(run_id, repo, offer_scope)
    for record in records:
        record["competitor_extra_benefits"] = _extra_benefits(
            enriched_plans, record["competitor_plan_id"], record["competitor_plan"]
        )
        record["omantel_extra_benefits"] = _extra_benefits(
            omantel_plans, record["omantel_plan_id"], record["omantel_plan"]
        )
    no_match = [_map_no_match(r, label) for r in narrative["no_match_report"]]

    summary = {
        "name": label,
        "label": label,
        "offer_scope": offer_scope,
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


def _load_btl_free_offers(settings: Settings) -> list[dict[str, Any]]:
    """Informational Omantel BTL free offers (D10). Never scored; missing file -> []."""
    offers_path = Path(settings.omantel_btl_free_offers_csv_path)
    perf_path = Path(settings.omantel_btl_free_offers_performance_csv_path)
    if not offers_path.is_absolute():
        offers_path = REPO_ROOT / offers_path
    if not perf_path.is_absolute():
        perf_path = REPO_ROOT / perf_path
    if not offers_path.exists():
        logger.warning("BTL free offers file not found: %s", offers_path)
        return []

    def _num(value: Any) -> float | None:
        try:
            return None if value in (None, "") else float(value)
        except (TypeError, ValueError):
            return None

    months: dict[str, dict[str, float]] = {}
    if perf_path.exists():
        with perf_path.open(newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                customers = _num(row.get("unique_customers"))
                month = (row.get("month") or "").strip()
                pid = str(row.get("product_id") or "").strip()
                if pid and month and customers is not None:
                    months.setdefault(pid, {})[month] = customers
    else:
        logger.warning("BTL free offers performance file not found: %s", perf_path)

    offers = []
    with offers_path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            pid = str(row.get("product_id") or "").strip()
            by_month = months.get(pid, {})
            offers.append({
                "product_id": pid,
                "product_name": row.get("product_name"),
                "product_type": row.get("product_type"),
                "data_gb": _num(row.get("data")),
                "minutes": _num(row.get("units_minutes")),
                "validity_days": _num(row.get("validity_in_days")),
                "avg_unique_customers": (
                    round(statistics.mean(by_month.values()), 2) if by_month else None
                ),
                "months": sorted(by_month),
            })
    return offers


def build_report_dataset(
    run_specs: list[tuple],
    *,
    analysis_run_id: str,
    advisor: Callable[[dict[str, Any]], PortfolioSegmentAdvice] | None = None,
    settings: Settings | None = None,
    repo: FileRunRepository | None = None,
) -> dict[str, Any]:
    """Assemble deterministic report data and run-level executive advice.

    Spec tuples are ``(run_id, competitor_run_id, name[, offer_scope])``; a
    missing scope means ATL. ``omantel_atl_products`` is None when the report
    has no ATL competitor.
    """

    settings = settings or get_settings()
    repo = repo or FileRunRepository(settings.runs_dir)
    competitors = []
    all_records: list[dict] = []
    all_no_match: list[dict] = []
    omantel_products: int | None = None
    omantel_btl_products: int | None = None
    has_btl = any(_scope_of(spec) == "BTL" for spec in run_specs)

    for spec in run_specs:
        run_id, competitor_run_id, name = spec[0], spec[1], spec[2]
        scope = _scope_of(spec)
        label = f"{name} · {scope}" if has_btl else name
        summary, records, no_match = build_competitor_dataset(
            run_id, competitor_run_id, name, repo=repo, offer_scope=scope, label=label
        )
        competitors.append(summary)
        all_records.extend(records)
        all_no_match.extend(no_match)
        count = _omantel_count(run_id, repo, scope)
        if scope == "BTL":
            omantel_btl_products = (
                count if omantel_btl_products is None else max(omantel_btl_products, count)
            )
        else:
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

    dataset = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": analysis_run_id,
        "omantel_atl_products": omantel_products,
        "omantel_btl_products": omantel_btl_products if has_btl else None,
        "competitors": competitors,
        "portfolio_analysis": portfolio_analysis,
        "records": all_records,
        "no_match": all_no_match,
    }
    if has_btl:
        dataset["btl_free_offers"] = _load_btl_free_offers(settings)
    return dataset


def write_business_report(
    run_specs: list[tuple],
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
        with llm_trace(name="run_report", settings=settings, run_id=analysis_run_id):
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
