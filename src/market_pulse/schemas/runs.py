"""Domain models for the run-oriented orchestration layer.

These are the production wrapping-layer models described in
``docs/architecture.md`` sections 3, 6, 9 and 10 -- they sit on top of the
already-verified Steps 1-6 service modules and give the API a
run/competitor-run/stage-result shape to persist and report on.

Not modeled on any ``reference/stepN.py`` file (there is none for this
layer); the source of truth here is ``docs/architecture.md``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

RunStatus = Literal["CREATED", "PROCESSING", "COMPLETED", "FAILED", "PARTIAL"]
CompetitorRunStatus = Literal["CREATED", "PROCESSING", "COMPLETED", "FAILED"]
StageStatus = Literal["PENDING", "PROCESSING", "COMPLETED", "FAILED"]

# The per-competitor pipeline stages (docs/architecture.md section 7).
# ``omantel_normalization`` (Step 2) is shared/run-level and deliberately
# NOT one of these five -- it is tracked separately (see
# ``storage.file_repository.FileRunRepository.get_omantel_stage_result``).
STAGE_NAMES: list[str] = [
    "competitor_normalization",
    "plan_matching",
    "gap_analysis",
    "risk_analysis",
    "narrative_generation",
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Run(BaseModel):
    """A single Market Pulse analysis cycle (``docs/architecture.md`` 3.1)."""

    run_id: str
    status: RunStatus = "CREATED"
    created_at: datetime = Field(default_factory=utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    completed_competitor_count: int = 0
    omantel_reference_status: StageStatus = "PENDING"


class CompetitorRun(BaseModel):
    """One competitor submitted under a run (``docs/architecture.md`` 3.2)."""

    competitor_run_id: str
    run_id: str
    competitor: str
    status: CompetitorRunStatus = "CREATED"
    input_type: Literal["inline", "path"]
    created_at: datetime = Field(default_factory=utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None


class ReportJob(BaseModel):
    """Report generation status, separate from competitor processing status."""

    run_id: str
    report_status: Literal["NOT_GENERATED", "PROCESSING", "COMPLETED", "FAILED"] = "NOT_GENERATED"
    report_path: Optional[str] = None
    report_error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class StageResult(BaseModel):
    """The result of a single pipeline stage for a run (or competitor run).

    ``competitor_run_id`` is ``None`` for the shared ``omantel_normalization``
    stage (docs/architecture.md section 7).
    """

    run_id: str
    competitor_run_id: Optional[str] = None
    stage: str
    status: StageStatus = "PENDING"
    result: Optional[Any] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
