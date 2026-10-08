"""Generate the business report using the same service as the report API.

For a single run, output and status are saved per run. Legacy explicit
--run selections and no-argument invocation still produce the shared HTML.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from market_pulse.config.settings import get_settings
from market_pulse.services.business_report_service import (
    DEFAULT_RUNS,
    generate_run_report,
    write_business_report,
)
from market_pulse.storage.file_repository import FileRunRepository


def _parse_spec(spec: str) -> tuple:
    """RUN_ID:COMPETITOR_RUN_ID:NAME with an optional trailing :ATL / :BTL (default ATL)."""
    scope = "ATL"
    head, _, tail = spec.rpartition(":")
    if tail.upper() in ("ATL", "BTL") and head.count(":") >= 2:
        spec, scope = head, tail.upper()
    parts = spec.split(":", 2)
    return (*parts, scope) if len(parts) == 3 else tuple(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--run-id", help="Generate a report for every completed competitor in this run.")
    source.add_argument(
        "--run", action="append", dest="runs", metavar="RUN_ID:COMPETITOR_RUN_ID:NAME",
        help="Explicit completed competitor selection; repeat to include multiple competitors.",
    )
    args = parser.parse_args()
    settings = get_settings()
    repo = FileRunRepository(settings.runs_dir)

    if args.run_id:
        job = generate_run_report(args.run_id, repo, settings)
        if job.report_status != "COMPLETED":
            parser.exit(1, (job.report_error or "Report generation is already in progress.") + "\n")
        print(f"Wrote {job.report_path}")
        return

    run_specs = [_parse_spec(spec) for spec in args.runs] if args.runs else DEFAULT_RUNS
    if any(len(spec) < 4 and spec not in DEFAULT_RUNS for spec in run_specs):
        parser.error("--run must have the format RUN_ID:COMPETITOR_RUN_ID:NAME[:ATL|BTL]")
    run_ids = sorted({spec[0] for spec in run_specs})
    analysis_run_id = run_ids[0] if len(run_ids) == 1 else "MULTI-RUN:" + ",".join(run_ids)
    output_path = (Path(settings.reports_dir) / "market_pulse_business_report.html").resolve()
    write_business_report(
        run_specs, analysis_run_id=analysis_run_id, repo=repo, settings=settings,
        output_path=output_path,
    )
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
