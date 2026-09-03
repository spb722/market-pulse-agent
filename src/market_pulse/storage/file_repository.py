"""A simple, local-filesystem-backed repository for runs/competitor runs/stage results.

Per ``docs/architecture.md`` section 8, intermediate results must be stored
by run/competitor/stage so the UI can read progress while processing is
still ongoing. This module is deliberately the *only* place in the codebase
that knows about the on-disk directory layout -- callers (orchestration,
API) go through ``FileRunRepository`` exclusively.

Directory layout (root configurable via ``Settings.runs_dir``, default
``./runs``)::

    runs/
      <run_id>/
        run.json
        omantel/
          stage_result.json          # the shared omantel_normalization StageResult
        portfolio_analysis.json      # run-level executive report analysis
        report.json                  # separate report-job status and final path
        report.lock                  # local-filesystem build lock
        competitors/
          <competitor_run_id>/
            competitor_run.json
            stages/
              competitor_normalization.json
              plan_matching.json
              gap_analysis.json
              risk_analysis.json
              narrative_generation.json

Writes are atomic: content is written to a temp file in the same directory
and then moved into place with ``os.replace``, so a crash mid-write never
corrupts a previously-good file and concurrent background threads never
observe a half-written file. Reads return ``None`` (not an exception) when
a file doesn't exist yet -- callers use that to represent
PENDING/not-started.

This is intentionally a single concrete class with no abstract base/
interface layer -- nothing else in this codebase implements an alternate
backend, so an interface would just be unused ceremony.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import Any, BinaryIO, Optional

from market_pulse.schemas.runs import CompetitorRun, ReportJob, Run, StageResult


class FileRunRepository:
    """Local-JSON-file persistence for the run-oriented orchestration layer."""

    def __init__(self, root: str | Path = "runs") -> None:
        self.root = Path(root)

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    def _run_dir(self, run_id: str) -> Path:
        return self.root / run_id

    def _run_file(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "run.json"

    def _omantel_stage_file(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "omantel" / "stage_result.json"

    def _portfolio_analysis_file(self, run_id: str) -> Path:
        return self._run_dir(run_id) / "portfolio_analysis.json"

    def _competitor_dir(self, run_id: str, competitor_run_id: str) -> Path:
        return self._run_dir(run_id) / "competitors" / competitor_run_id

    def _competitor_run_file(self, run_id: str, competitor_run_id: str) -> Path:
        return self._competitor_dir(run_id, competitor_run_id) / "competitor_run.json"

    def _stage_file(self, run_id: str, competitor_run_id: str, stage: str) -> Path:
        return self._competitor_dir(run_id, competitor_run_id) / "stages" / f"{stage}.json"

    # ------------------------------------------------------------------
    # Low-level atomic JSON read/write
    # ------------------------------------------------------------------

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)

        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, default=str, indent=2)

            os.replace(tmp_path, path)
        except BaseException:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

    @staticmethod
    def _read_json(path: Path) -> Optional[dict]:
        if not path.exists():
            return None

        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def create_run(self, run: Run) -> None:
        self._write_json(self._run_file(run.run_id), run.model_dump(mode="json"))

    def get_run(self, run_id: str) -> Optional[Run]:
        data = self._read_json(self._run_file(run_id))

        return Run.model_validate(data) if data is not None else None

    def save_run(self, run: Run) -> None:
        self._write_json(self._run_file(run.run_id), run.model_dump(mode="json"))

    # ------------------------------------------------------------------
    # Competitor run
    # ------------------------------------------------------------------

    def create_competitor_run(self, cr: CompetitorRun) -> None:
        self._write_json(
            self._competitor_run_file(cr.run_id, cr.competitor_run_id),
            cr.model_dump(mode="json"),
        )

    def get_competitor_run(self, run_id: str, competitor_run_id: str) -> Optional[CompetitorRun]:
        data = self._read_json(self._competitor_run_file(run_id, competitor_run_id))

        return CompetitorRun.model_validate(data) if data is not None else None

    def save_competitor_run(self, cr: CompetitorRun) -> None:
        self._write_json(
            self._competitor_run_file(cr.run_id, cr.competitor_run_id),
            cr.model_dump(mode="json"),
        )

    def list_competitor_runs(self, run_id: str) -> list[CompetitorRun]:
        competitors_dir = self._run_dir(run_id) / "competitors"

        if not competitors_dir.exists():
            return []

        runs: list[CompetitorRun] = []

        for competitor_run_id in sorted(os.listdir(competitors_dir)):
            cr = self.get_competitor_run(run_id, competitor_run_id)

            if cr is not None:
                runs.append(cr)

        return runs

    # ------------------------------------------------------------------
    # Stage results
    # ------------------------------------------------------------------

    def save_stage_result(self, sr: StageResult) -> None:
        if sr.competitor_run_id is None:
            raise ValueError(
                "save_stage_result requires a competitor_run_id; "
                "use save_omantel_stage_result for the shared Step 2 stage."
            )

        self._write_json(
            self._stage_file(sr.run_id, sr.competitor_run_id, sr.stage),
            sr.model_dump(mode="json"),
        )

    def get_stage_result(
        self, run_id: str, competitor_run_id: str, stage: str
    ) -> Optional[StageResult]:
        data = self._read_json(self._stage_file(run_id, competitor_run_id, stage))

        return StageResult.model_validate(data) if data is not None else None

    def get_omantel_stage_result(self, run_id: str) -> Optional[StageResult]:
        data = self._read_json(self._omantel_stage_file(run_id))

        return StageResult.model_validate(data) if data is not None else None

    def save_omantel_stage_result(self, sr: StageResult) -> None:
        self._write_json(self._omantel_stage_file(sr.run_id), sr.model_dump(mode="json"))

    # ------------------------------------------------------------------
    # Run-level report analysis
    # ------------------------------------------------------------------

    def get_portfolio_analysis(self, run_id: str) -> Optional[dict[str, Any]]:
        return self._read_json(self._portfolio_analysis_file(run_id))

    def save_portfolio_analysis(self, run_id: str, payload: dict[str, Any]) -> None:
        self._write_json(self._portfolio_analysis_file(run_id), payload)

    def get_report_job(self, run_id: str) -> ReportJob:
        data = self._read_json(self._run_dir(run_id) / "report.json")
        return ReportJob.model_validate(data) if data else ReportJob(run_id=run_id)

    def save_report_job(self, job: ReportJob) -> None:
        self._write_json(self._run_dir(job.run_id) / "report.json", job.model_dump(mode="json"))

    def acquire_report_lock(self, run_id: str) -> BinaryIO | None:
        """Nonblocking local-filesystem lock; also works across API workers.

        The caller must verify the run exists, and close the returned handle
        after the job finishes. POSIX releases the lock if its process exits,
        so an interrupted job can be retried without deleting any lock files.
        """
        handle = (self._run_dir(run_id) / "report.lock").open("ab")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return None
        except BaseException:
            handle.close()
            raise
        return handle
