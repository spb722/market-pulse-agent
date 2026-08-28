"""Run-oriented public API routes (``docs/architecture.md`` section 6).

Validates requests, calls the orchestration layer, and returns responses.
No business formulas live here -- see ``orchestration.pipeline`` and the
``services/`` modules for that.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from market_pulse.api.schemas import CompetitorSubmitRequest
from market_pulse.config.settings import Settings, get_settings
from market_pulse.orchestration.pipeline import (
    generate_competitor_run_id,
    generate_run_id,
    process_competitor,
)
from market_pulse.schemas.runs import STAGE_NAMES, CompetitorRun, Run, StageResult, utcnow
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

    if request.data:
        input_type = "inline"

        if "prepaid" in request.data:
            prepaid_raw = request.data["prepaid"]
            _validate_payload_shape(prepaid_raw, "prepaid")
        else:
            prepaid_raw = _EMPTY_ENVELOPE["prepaid"]

        if "postpaid" in request.data:
            postpaid_raw = request.data["postpaid"]
            _validate_payload_shape(postpaid_raw, "postpaid")
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

            _validate_payload_shape(prepaid_raw, "prepaid")
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

            _validate_payload_shape(postpaid_raw, "postpaid")
        else:
            postpaid_raw = _EMPTY_ENVELOPE["postpaid"]

    competitor_run_id = generate_competitor_run_id()

    cr = CompetitorRun(
        competitor_run_id=competitor_run_id,
        run_id=run_id,
        competitor=request.competitor,
        status="PROCESSING",
        input_type=input_type,
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
        "status": "PROCESSING",
    }


@router.get("/runs/{run_id}")
def get_run_status(run_id: str, repo: FileRunRepository = Depends(get_repository)) -> dict:
    run = repo.get_run(run_id)

    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")

    competitor_runs = repo.list_competitor_runs(run_id)

    return {
        "run_id": run.run_id,
        "status": run.status,
        "competitors": [
            {
                "competitor_run_id": cr.competitor_run_id,
                "competitor": cr.competitor,
                "status": cr.status,
            }
            for cr in competitor_runs
        ],
    }


@router.get("/runs/{run_id}/competitors")
def list_competitors(run_id: str, repo: FileRunRepository = Depends(get_repository)) -> list[CompetitorRun]:
    run = repo.get_run(run_id)

    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")

    return repo.list_competitor_runs(run_id)


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
