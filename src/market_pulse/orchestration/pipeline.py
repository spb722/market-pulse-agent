"""Run/competitor-run pipeline orchestration.

Wires the already-verified Steps 1-6 service functions into the run-oriented
processing model described in ``docs/architecture.md`` sections 3-5, 7, 11
and 17: create a run, prepare/reuse the shared Omantel reference once per
run, then process each competitor through
``competitor_normalization -> plan_matching -> gap_analysis -> risk_analysis
-> narrative_generation``, persisting a ``StageResult`` after every stage so
the UI can read intermediate results while processing is still in progress.

The ``risk_analysis`` stage loads the real Omantel product-performance CSV
(``settings.omantel_performance_csv_path``, see the Step 5 service's
``load_performance_records_from_csv``) fresh on every competitor's
risk_analysis call -- no caching/shared-stage-result treatment like the
Omantel product catalogue gets, since a ~1000-row CSV read is cheap and
stateless. The full loaded dataset (not filtered to this competitor's
matches) is passed to ``analyze_step5_records``, because its relative
exposure scoring is meant to be relative to Omantel's whole portfolio. If
loading the performance CSV fails (e.g. the configured path doesn't exist),
this does not fail the competitor pipeline: a WARNING is logged and
``risk_analysis`` falls back to ``performance_records=[]`` for that run,
which reproduces the previous behavior (every ``ANALYZED`` gap-analysis
record comes back ``REVIEW_REQUIRED``).

Logging is intentionally simple: every log line emitted directly by this
module includes ``run=... cr=... stage=...`` in the message text itself
(see ``_ctx``) rather than relying on any contextvar/filter propagation
machinery. Steps 1-6's own ``logging.getLogger(__name__)`` calls are
unmodified and do not carry this context -- that's an accepted, documented
simplification for this project.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter
from typing import Any
from uuid import uuid4

from market_pulse.config.settings import Settings
from market_pulse.schemas.runs import STAGE_NAMES, CompetitorRun, Run, StageResult, utcnow
from market_pulse.services.competitor_normalization_service import (
    RawPayload,
    run_competitor_normalization,
)
from market_pulse.services.gap_analysis_service import analyze_matches
from market_pulse.services.narrative_service import generate_narrative_report
from market_pulse.services.omantel_normalization_service import (
    load_omantel_catalogues_from_csv,
    run_omantel_normalization,
)
from market_pulse.services.plan_matching_service import match_competitor_plans
from market_pulse.services.risk_analysis_service import (
    analyze_step5_records,
    load_performance_records_from_csv,
)
from market_pulse.storage.file_repository import FileRunRepository

logger = logging.getLogger(__name__)

# Deliberately simple, coarse, process-wide lock (not per-run_id): at most
# one Omantel-reference preparation runs at a time across the whole server,
# even across unrelated runs. Accepted trade-off for simplicity -- this is a
# low-frequency, manually-triggered operation, not a high-throughput system.
_omantel_prep_lock = threading.Lock()


def generate_run_id() -> str:
    return f"RUN-{uuid4().hex[:8].upper()}"


def generate_competitor_run_id() -> str:
    return f"CR-{uuid4().hex[:8].upper()}"


def _ctx(run_id: str, competitor_run_id: str | None = None, stage: str | None = None) -> str:
    """Build a ``run=... cr=... stage=...`` prefix for a log message."""

    return f"run={run_id} cr={competitor_run_id or '-'} stage={stage or '-'}"


# ---------------------------------------------------------------------------
# Shared Omantel reference (Step 2)
# ---------------------------------------------------------------------------


def ensure_omantel_reference(
    run_id: str, repo: FileRunRepository, settings: Settings
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Prepare (or reuse) the shared Omantel reference for ``run_id``.

    Per ``docs/architecture.md`` section 5: "Do not unnecessarily rerun
    Step 2 separately for every competitor." If a COMPLETED omantel
    reference already exists for this run, it is reused without
    re-running Step 2.
    """

    with _omantel_prep_lock:
        existing = repo.get_omantel_stage_result(run_id)

        if existing is not None and existing.status == "COMPLETED":
            logger.info(
                "%s | Reusing previously prepared Omantel reference",
                _ctx(run_id, stage="omantel_normalization"),
            )

            enriched_plans, errors = existing.result

            return enriched_plans, errors

        started_at = utcnow()

        repo.save_omantel_stage_result(
            StageResult(
                run_id=run_id,
                competitor_run_id=None,
                stage="omantel_normalization",
                status="PROCESSING",
                started_at=started_at,
            )
        )

        logger.info(
            "%s | Preparing Omantel reference", _ctx(run_id, stage="omantel_normalization")
        )

        try:
            prepaid_df, postpaid_df = load_omantel_catalogues_from_csv(
                settings.omantel_prepaid_csv_path, settings.omantel_postpaid_csv_path
            )

            enriched_plans, errors = run_omantel_normalization(prepaid_df, postpaid_df)

        except Exception as exc:  # noqa: BLE001 - a broken Omantel reference must block this run
            logger.error(
                "%s | Failed to prepare Omantel reference: %s",
                _ctx(run_id, stage="omantel_normalization"),
                exc,
                exc_info=True,
            )

            repo.save_omantel_stage_result(
                StageResult(
                    run_id=run_id,
                    competitor_run_id=None,
                    stage="omantel_normalization",
                    status="FAILED",
                    started_at=started_at,
                    completed_at=utcnow(),
                    error=str(exc),
                )
            )

            run = repo.get_run(run_id)

            if run is not None:
                run.omantel_reference_status = "FAILED"
                repo.save_run(run)

            raise

        repo.save_omantel_stage_result(
            StageResult(
                run_id=run_id,
                competitor_run_id=None,
                stage="omantel_normalization",
                status="COMPLETED",
                result=[enriched_plans, errors],
                started_at=started_at,
                completed_at=utcnow(),
            )
        )

        run = repo.get_run(run_id)

        if run is not None:
            run.omantel_reference_status = "COMPLETED"
            repo.save_run(run)

        logger.info(
            "%s | %d Omantel products prepared, %d errors",
            _ctx(run_id, stage="omantel_normalization"),
            len(enriched_plans),
            len(errors),
        )

        return enriched_plans, errors


# ---------------------------------------------------------------------------
# Run aggregate status (docs/architecture.md section 11)
# ---------------------------------------------------------------------------


def compute_run_status(competitor_runs: list[CompetitorRun]) -> str:
    """Aggregate run status from its competitor runs' terminal states.

    - Any competitor still CREATED/PROCESSING -> "PROCESSING"
    - All COMPLETED -> "COMPLETED"
    - All FAILED -> "FAILED"
    - A mix of COMPLETED and FAILED (none pending) -> "PARTIAL"
    - No competitors at all -> "CREATED"
    """

    if not competitor_runs:
        return "CREATED"

    statuses = [cr.status for cr in competitor_runs]

    if any(s in ("CREATED", "PROCESSING") for s in statuses):
        return "PROCESSING"

    if all(s == "COMPLETED" for s in statuses):
        return "COMPLETED"

    if all(s == "FAILED" for s in statuses):
        return "FAILED"

    return "PARTIAL"


def _refresh_run_aggregate(run_id: str, repo: FileRunRepository) -> None:
    run = repo.get_run(run_id)

    if run is None:
        return

    competitor_runs = repo.list_competitor_runs(run_id)

    run.status = compute_run_status(competitor_runs)
    run.completed_competitor_count = sum(1 for cr in competitor_runs if cr.status == "COMPLETED")

    if run.status in ("COMPLETED", "FAILED", "PARTIAL") and run.completed_at is None:
        run.completed_at = utcnow()

    repo.save_run(run)


# ---------------------------------------------------------------------------
# Per-competitor pipeline
# ---------------------------------------------------------------------------


def _save_stage(
    repo: FileRunRepository,
    run_id: str,
    competitor_run_id: str,
    stage: str,
    status: str,
    started_at,
    result: Any = None,
    error: str | None = None,
) -> None:
    repo.save_stage_result(
        StageResult(
            run_id=run_id,
            competitor_run_id=competitor_run_id,
            stage=stage,
            status=status,
            result=result,
            started_at=started_at,
            completed_at=utcnow() if status in ("COMPLETED", "FAILED") else None,
            error=error,
        )
    )


def process_competitor(
    run_id: str,
    competitor_run_id: str,
    competitor: str,
    prepaid_raw: RawPayload,
    postpaid_raw: RawPayload,
    repo: FileRunRepository,
    settings: Settings,
) -> None:
    """Run the full per-competitor pipeline, persisting a stage result after every step.

    Never raises: a failure in this function must not affect other
    competitors or the run's Omantel reference. It runs in a background task
    where an uncaught exception would otherwise be silently swallowed, so it
    is caught, logged and recorded on the ``CompetitorRun``/``StageResult``
    instead.
    """

    cr = repo.get_competitor_run(run_id, competitor_run_id)

    if cr is None:
        logger.error(
            "%s | CompetitorRun not found; aborting",
            _ctx(run_id, competitor_run_id),
        )
        return

    cr.status = "PROCESSING"
    cr.started_at = utcnow()
    repo.save_competitor_run(cr)

    current_stage = "competitor_normalization"

    try:
        # --- Stage 1: competitor_normalization (Step 1) --------------------
        started_at = utcnow()
        _save_stage(repo, run_id, competitor_run_id, current_stage, "PROCESSING", started_at)

        enriched_plans, errors = run_competitor_normalization(prepaid_raw, postpaid_raw)

        if errors and not enriched_plans:
            raise RuntimeError(
                f"All {len(errors)} plan(s) failed classification for competitor "
                f"{competitor!r} -- check LLM connectivity/configuration."
            )

        _save_stage(
            repo,
            run_id,
            competitor_run_id,
            current_stage,
            "COMPLETED",
            started_at,
            result={"enriched_plans": enriched_plans, "errors": errors},
        )

        logger.info(
            "%s | Step 1 complete: %d plans enriched, %d errors",
            _ctx(run_id, competitor_run_id, current_stage),
            len(enriched_plans),
            len(errors),
        )

        # --- Shared Omantel reference (Step 2) ------------------------------
        # Not one of this competitor's 5 stages (see STAGE_NAMES); tracked
        # separately at the run level by ensure_omantel_reference itself. Set
        # as the "current stage" only so a failure here is attributed
        # correctly in logs/errors without touching the (already-COMPLETED)
        # competitor_normalization StageResult below.
        current_stage = "omantel_normalization"
        omantel_plans, _omantel_errors = ensure_omantel_reference(run_id, repo, settings)

        # --- Stage 2: plan_matching (Step 3) --------------------------------
        current_stage = "plan_matching"
        started_at = utcnow()
        _save_stage(repo, run_id, competitor_run_id, current_stage, "PROCESSING", started_at)

        step3_matches = match_competitor_plans(enriched_plans, omantel_plans)

        _save_stage(
            repo, run_id, competitor_run_id, current_stage, "COMPLETED", started_at, result=step3_matches
        )

        match_counts = Counter(m.get("match_status") for m in step3_matches)

        logger.info(
            "%s | Step 3 complete: %s",
            _ctx(run_id, competitor_run_id, current_stage),
            dict(match_counts),
        )

        # --- Stage 3: gap_analysis (Step 4) ---------------------------------
        current_stage = "gap_analysis"
        started_at = utcnow()
        _save_stage(repo, run_id, competitor_run_id, current_stage, "PROCESSING", started_at)

        step4_results = analyze_matches(step3_matches, enriched_plans, omantel_plans)

        _save_stage(
            repo, run_id, competitor_run_id, current_stage, "COMPLETED", started_at, result=step4_results
        )

        gap_counts = Counter(r.get("gap_analysis_status") for r in step4_results)

        logger.info(
            "%s | Step 4 complete: %s",
            _ctx(run_id, competitor_run_id, current_stage),
            dict(gap_counts),
        )

        # --- Stage 4: risk_analysis (Step 5) --------------------------------
        current_stage = "risk_analysis"
        started_at = utcnow()
        _save_stage(repo, run_id, competitor_run_id, current_stage, "PROCESSING", started_at)

        # Load the real Omantel product-performance CSV fresh for this
        # competitor (no caching -- see module docstring). The FULL loaded
        # dataset is passed to analyze_step5_records, unfiltered by
        # competitor, because relative exposure scoring is meant to be
        # relative to Omantel's whole portfolio. A failure to load must not
        # fail this competitor's pipeline -- fall back to an empty list,
        # reproducing the previous REVIEW_REQUIRED-for-everything behavior.
        try:
            performance_records = load_performance_records_from_csv(
                settings.omantel_performance_csv_path
            )
        except Exception as exc:  # noqa: BLE001 - defensive fallback, do not fail the competitor pipeline
            logger.warning(
                "%s | Could not load Omantel product-performance data from %s (%s); "
                "falling back to performance_records=[] for this run - "
                "REVIEW_REQUIRED is expected for matched plans until this is resolved.",
                _ctx(run_id, competitor_run_id, current_stage),
                settings.omantel_performance_csv_path,
                exc,
            )
            performance_records = []

        step5_results = analyze_step5_records(
            step4_results, performance_records=performance_records
        )

        _save_stage(
            repo, run_id, competitor_run_id, current_stage, "COMPLETED", started_at, result=step5_results
        )

        risk_counts = Counter(r.get("risk_status") for r in step5_results)

        logger.info(
            "%s | Step 5 complete: %s (%d performance records loaded from %s)",
            _ctx(run_id, competitor_run_id, current_stage),
            dict(risk_counts),
            len(performance_records),
            settings.omantel_performance_csv_path,
        )

        # --- Stage 5: narrative_generation (Step 6) -------------------------
        current_stage = "narrative_generation"
        started_at = utcnow()
        _save_stage(repo, run_id, competitor_run_id, current_stage, "PROCESSING", started_at)

        narrative_report = generate_narrative_report(
            competitor, enriched_plans, omantel_plans, step3_matches, step4_results, step5_results
        )

        _save_stage(
            repo,
            run_id,
            competitor_run_id,
            current_stage,
            "COMPLETED",
            started_at,
            result=narrative_report,
        )

        narrative_counts = Counter(
            r.get("narrative_source") for r in narrative_report.get("records", [])
        )

        logger.info(
            "%s | Step 6 complete: %s",
            _ctx(run_id, competitor_run_id, current_stage),
            dict(narrative_counts),
        )

        # --- Done ------------------------------------------------------------
        cr.status = "COMPLETED"
        cr.completed_at = utcnow()
        repo.save_competitor_run(cr)

    except Exception as exc:  # noqa: BLE001 - background-task isolation boundary
        logger.error(
            "%s | Stage failed: %s",
            _ctx(run_id, competitor_run_id, current_stage),
            exc,
            exc_info=True,
        )

        # "omantel_normalization" is not one of this competitor's own 5
        # stages -- its failure is already recorded at the run level by
        # ensure_omantel_reference; only persist a competitor-level
        # StageResult for the competitor's own stages.
        if current_stage in STAGE_NAMES:
            stage_result = repo.get_stage_result(run_id, competitor_run_id, current_stage)
            failed_started_at = stage_result.started_at if stage_result else utcnow()

            _save_stage(
                repo,
                run_id,
                competitor_run_id,
                current_stage,
                "FAILED",
                failed_started_at,
                error=str(exc),
            )

        cr.status = "FAILED"
        cr.completed_at = utcnow()
        cr.error = str(exc)
        repo.save_competitor_run(cr)

    finally:
        _refresh_run_aggregate(run_id, repo)
