"""Run-oriented public API routes (``docs/architecture.md`` section 6).

Validates requests, calls the orchestration layer, and returns responses.
No business formulas live here -- see ``orchestration.pipeline`` and the
``services/`` modules for that.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from market_pulse.api.schemas import CompetitorSubmitRequest
from market_pulse.config.settings import Settings, get_settings
from market_pulse.orchestration.pipeline import (
    generate_competitor_run_id,
    generate_run_id,
    process_competitor,
)
from market_pulse.schemas.runs import STAGE_NAMES, CompetitorRun, ReportJob, Run, StageResult, utcnow
from market_pulse.services.business_report_service import (
    generate_run_report,
    reserve_report_generation,
)
from market_pulse.storage.file_repository import FileRunRepository

logger = logging.getLogger(__name__)

router = APIRouter()

_repository: FileRunRepository | None = None


def get_repository() -> FileRunRepository:
    """FastAPI dependency returning the shared repository instance.

    A single module-level instance is fine for V1 -- file storage has no
    connection-pooling concerns. Built lazily so ``Settings().runs_dir`` is
    read at first use (picking up test overrides) rather than at import time.
    """

    global _repository

    if _repository is None:
        _repository = FileRunRepository(get_settings().runs_dir)

    return _repository


def _load_raw_payload(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


_EMPTY_ENVELOPE = {
    "prepaid": [{"master_plans": [], "addon_plans": []}],
    "postpaid": [{"basic_plans": [], "addon_plans": []}],
}

_CATEGORY_KEYS = {
    "prepaid": ("master_plans", "addon_plans"),
    "postpaid": ("basic_plans", "addon_plans"),
}


def _validate_payload_shape(raw: Any, category: str) -> None:
    """Validate that ``raw`` looks like a wrapped competitor crawler payload.

    Guards against the real, observed mistake of submitting a flat list of
    individual plan dicts instead of the expected
    ``[{"master_plans": [...], "addon_plans": [...]}]`` envelope.
    """

    expected_keys = _CATEGORY_KEYS[category]

    if not isinstance(raw, list) or not raw:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{category} data does not look like a competitor crawler payload -- "
                f"expected a non-empty list whose first item contains "
                f"{' and/or '.join(repr(k) for k in expected_keys)}, but found "
                f"type={type(raw).__name__} "
                f"length={len(raw) if isinstance(raw, list) else 'n/a'}."
            ),
        )

    first = raw[0]

    if not isinstance(first, dict) or not any(k in first for k in expected_keys):
        found_keys = list(first.keys()) if isinstance(first, dict) else []

        raise HTTPException(
            status_code=422,
            detail=(
                f"{category} data does not look like a competitor crawler payload -- "
                f"expected the first item to contain "
                f"{' and/or '.join(repr(k) for k in expected_keys)}, but found keys: "
                f"{found_keys}. If this is a flat list of individual plan objects, wrap "
                f"it as: [{{{expected_keys[0]!r}: [...], {expected_keys[1]!r}: []}}]"
            ),
        )


_BTL_PRODUCT_TYPES = ("COMBO", "DATA", "VOICE", "IDD", "ROAMING", "SMS", "OTHER")


def _check_root_scope(raw: Any, category: str, request_scope: str) -> None:
    """Reject a payload whose root ``offer_scope`` contradicts the request.

    Used for BTL requests (root must be "BTL" if present) and, as a reverse
    safety check, for ATL requests (root must not say "BTL"). A root without
    an ``offer_scope`` field is always accepted.
    """

    root = raw[0] if isinstance(raw, list) and raw else None

    if not isinstance(root, dict) or "offer_scope" not in root:
        return

    file_scope = root["offer_scope"]

    if request_scope == "BTL" and file_scope != "BTL":
        raise HTTPException(
            status_code=422,
            detail=(
                f"{category} payload root has offer_scope={file_scope!r} but the request "
                f"offer_scope is 'BTL'."
            ),
        )

    if request_scope == "ATL" and file_scope == "BTL":
        raise HTTPException(
            status_code=422,
            detail=(
                f"{category} payload root is marked offer_scope='BTL' but the request "
                f"offer_scope is 'ATL' (default). Submit it with offer_scope='BTL'."
            ),
        )


def _validate_btl_payload(raw: Any, category: str, seen_plan_ids: dict[str, str]) -> None:
    """BTL-only strictness: plan_id present/unique, product_type in the allowed set.

    ``seen_plan_ids`` maps plan_id -> category and is shared across the
    competitor's prepaid and postpaid payloads so duplicates are caught
    across both. Missing categories (synthesized empty envelopes) pass.
    Null ``validity_days`` / ``data_quality_flags`` are warnings only.
    """

    root = raw[0]

    for key in _CATEGORY_KEYS[category]:
        plans = root.get(key) or []

        if not isinstance(plans, list):
            raise HTTPException(
                status_code=422,
                detail=f"{category} '{key}' must be a list of plan objects, found {type(plans).__name__}.",
            )

        for index, plan in enumerate(plans):
            if not isinstance(plan, dict):
                raise HTTPException(
                    status_code=422,
                    detail=f"{category} {key}[{index}] must be a plan object, found {type(plan).__name__}.",
                )

            plan_id = plan.get("plan_id")
            label = plan.get("plan_name") or plan.get("name") or f"{key}[{index}]"

            if not isinstance(plan_id, str) or not plan_id.strip():
                raise HTTPException(
                    status_code=422,
                    detail=f"{category} {key}[{index}] ({label!r}) is missing a plan_id (BTL plans require a non-empty plan_id).",
                )

            if plan_id in seen_plan_ids:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Duplicate plan_id {plan_id!r} in {category} {key}[{index}] "
                        f"(already used in {seen_plan_ids[plan_id]}); plan_id must be unique "
                        f"across the competitor's prepaid and postpaid plans."
                    ),
                )

            seen_plan_ids[plan_id] = f"{category} {key}"

            product_type = plan.get("product_type")

            if product_type not in _BTL_PRODUCT_TYPES:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"{category} {key}[{index}] plan_id={plan_id!r} has invalid product_type "
                        f"{product_type!r}; expected one of {list(_BTL_PRODUCT_TYPES)}."
                    ),
                )

            if plan.get("validity_days") is None or plan.get("data_quality_flags"):
                logger.warning(
                    "BTL %s plan_id=%s | warning: null validity_days or data_quality_flags present",
                    category,
                    plan_id,
                )


def _validate_scope_payload(raw: Any, category: str, offer_scope: str, seen_plan_ids: dict[str, str]) -> None:
    """Shape check (all scopes) plus scope-specific checks, before any run is created."""

    _validate_payload_shape(raw, category)
    _check_root_scope(raw, category, offer_scope)

    if offer_scope == "BTL":
        _validate_btl_payload(raw, category, seen_plan_ids)


@router.post("/runs", status_code=201)
def create_run(repo: FileRunRepository = Depends(get_repository)) -> dict:
    run_id = generate_run_id()

    run = Run(run_id=run_id, status="CREATED", created_at=utcnow())
    repo.create_run(run)

    logger.info("run=%s | Run created", run_id)

    return {"run_id": run.run_id, "status": run.status}


@router.post("/runs/{run_id}/competitors", status_code=201)
def submit_competitor(
    run_id: str,
    request: CompetitorSubmitRequest,
    background_tasks: BackgroundTasks,
    repo: FileRunRepository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> dict:
    run = repo.get_run(run_id)

    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")

    seen_plan_ids: dict[str, str] = {}

    if request.data:
        input_type = "inline"

        if "prepaid" in request.data:
            prepaid_raw = request.data["prepaid"]
            _validate_scope_payload(prepaid_raw, "prepaid", request.offer_scope, seen_plan_ids)
        else:
            prepaid_raw = _EMPTY_ENVELOPE["prepaid"]

        if "postpaid" in request.data:
            postpaid_raw = request.data["postpaid"]
            _validate_scope_payload(postpaid_raw, "postpaid", request.offer_scope, seen_plan_ids)
        else:
            postpaid_raw = _EMPTY_ENVELOPE["postpaid"]
    else:
        input_type = "path"

        prepaid_path = request.data_path.get("prepaid")
        postpaid_path = request.data_path.get("postpaid")

        if prepaid_path:
            if not Path(prepaid_path).exists():
                raise HTTPException(
                    status_code=422, detail=f"data_path.prepaid file not found: {prepaid_path!r}"
                )

            try:
                prepaid_raw = _load_raw_payload(prepaid_path)
            except (json.JSONDecodeError, OSError) as exc:
                raise HTTPException(
                    status_code=422, detail=f"Failed to load data_path.prepaid JSON: {exc}"
                ) from exc

            _validate_scope_payload(prepaid_raw, "prepaid", request.offer_scope, seen_plan_ids)
        else:
            prepaid_raw = _EMPTY_ENVELOPE["prepaid"]

        if postpaid_path:
            if not Path(postpaid_path).exists():
                raise HTTPException(
                    status_code=422, detail=f"data_path.postpaid file not found: {postpaid_path!r}"
                )

            try:
                postpaid_raw = _load_raw_payload(postpaid_path)
            except (json.JSONDecodeError, OSError) as exc:
                raise HTTPException(
                    status_code=422, detail=f"Failed to load data_path.postpaid JSON: {exc}"
                ) from exc

            _validate_scope_payload(postpaid_raw, "postpaid", request.offer_scope, seen_plan_ids)
        else:
            postpaid_raw = _EMPTY_ENVELOPE["postpaid"]

    competitor_run_id = generate_competitor_run_id()

    cr = CompetitorRun(
        competitor_run_id=competitor_run_id,
        run_id=run_id,
        competitor=request.competitor,
        status="PROCESSING",
        input_type=input_type,
        offer_scope=request.offer_scope,
        created_at=utcnow(),
    )
    repo.create_competitor_run(cr)

    # A run with a competitor actively processing must itself show
    # PROCESSING (docs/architecture.md section 6.3's example), not sit at
    # CREATED until that competitor happens to finish. compute_run_status
    # only runs when a competitor reaches a terminal state, so bump it here
    # immediately on submission.
    if run.status == "CREATED":
        run.status = "PROCESSING"
        run.started_at = run.started_at or utcnow()
        repo.save_run(run)

    for stage in STAGE_NAMES:
        repo.save_stage_result(
            StageResult(run_id=run_id, competitor_run_id=competitor_run_id, stage=stage, status="PENDING")
        )

    logger.info(
        "run=%s cr=%s | Competitor %r submitted (input_type=%s)",
        run_id,
        competitor_run_id,
        request.competitor,
        input_type,
    )

    background_tasks.add_task(
        process_competitor,
        run_id,
        competitor_run_id,
        request.competitor,
        prepaid_raw,
        postpaid_raw,
        repo,
        settings,
    )

    return {
        "run_id": run_id,
        "competitor_run_id": competitor_run_id,
        "competitor": request.competitor,
        "offer_scope": request.offer_scope,
        "status": "PROCESSING",
    }


@router.post(
    "/runs/{run_id}/report",
    status_code=202,
    response_model=ReportJob,
    responses={
        404: {"description": "Run not found"},
        409: {"description": "Omantel or competitor stage results are not ready"},
    },
)
def generate_report(
    run_id: str,
    background_tasks: BackgroundTasks,
    repo: FileRunRepository = Depends(get_repository),
    settings: Settings = Depends(get_settings),
) -> ReportJob:
    """Start a report build; poll GET /runs/{run_id} for its file path."""
    try:
        job, lock = reserve_report_generation(run_id, repo)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if lock is not None:
        background_tasks.add_task(generate_run_report, run_id, repo, settings, job=job, lock=lock)
    return job.model_copy(deep=True)


@router.get("/runs/{run_id}")
def get_run_status(run_id: str, repo: FileRunRepository = Depends(get_repository)) -> dict:
    run = repo.get_run(run_id)

    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")

    competitor_runs = repo.list_competitor_runs(run_id)
    report = repo.get_report_job(run_id)

    return {
        "run_id": run.run_id,
        "status": run.status,
        "report_status": report.report_status,
        "report_path": report.report_path,
        "report_error": report.report_error,
        "competitors": [
            {
                "competitor_run_id": cr.competitor_run_id,
                "competitor": cr.competitor,
                "offer_scope": cr.offer_scope,
                "status": cr.status,
            }
            for cr in competitor_runs
        ],
    }


@router.get("/runs/{run_id}/competitors")
def list_competitors(
    run_id: str,
    offer_scope: Optional[Literal["ATL", "BTL"]] = Query(default=None),
    repo: FileRunRepository = Depends(get_repository),
) -> list[CompetitorRun]:
    run = repo.get_run(run_id)

    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")

    competitor_runs = repo.list_competitor_runs(run_id)

    if offer_scope is not None:
        competitor_runs = [cr for cr in competitor_runs if cr.offer_scope == offer_scope]

    return competitor_runs


@router.get("/runs/{run_id}/competitors/{competitor_run_id}")
def get_competitor_status(
    run_id: str, competitor_run_id: str, repo: FileRunRepository = Depends(get_repository)
) -> dict:
    run = repo.get_run(run_id)

    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")

    cr = repo.get_competitor_run(run_id, competitor_run_id)

    if cr is None:
        raise HTTPException(
            status_code=404, detail=f"Competitor run {competitor_run_id!r} not found."
        )

    stages = {}

    for stage in STAGE_NAMES:
        sr = repo.get_stage_result(run_id, competitor_run_id, stage)
        stages[stage] = sr.status if sr is not None else "PENDING"

    return {
        "run_id": run_id,
        "competitor_run_id": competitor_run_id,
        "competitor": cr.competitor,
        "offer_scope": cr.offer_scope,
        "status": cr.status,
        "stages": stages,
    }


@router.get("/runs/{run_id}/competitors/{competitor_run_id}/results/{stage}")
def get_stage_result(
    run_id: str,
    competitor_run_id: str,
    stage: str,
    repo: FileRunRepository = Depends(get_repository),
) -> StageResult:
    if stage not in STAGE_NAMES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown stage {stage!r}. Valid stages: {STAGE_NAMES}.",
        )

    run = repo.get_run(run_id)

    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")

    cr = repo.get_competitor_run(run_id, competitor_run_id)


    if cr is None:
        raise HTTPException(
            status_code=404, detail=f"Competitor run {competitor_run_id!r} not found."
        )

    sr = repo.get_stage_result(run_id, competitor_run_id, stage)

    if sr is None or sr.status != "COMPLETED":
        current_status = sr.status if sr is not None else "PENDING"

        raise HTTPException(
            status_code=409,
            detail={
                "message": f"Stage {stage!r} is not completed yet.",
                "status": current_status,
            },
        )

    return sr
