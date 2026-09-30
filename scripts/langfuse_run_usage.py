"""Print one run's LLM usage from Langfuse: per competitor run and in total.

Reads the Langfuse session named after the run ID. Langfuse ingests
asynchronously, so a run that just finished may take a few seconds to show.
"""

from __future__ import annotations

import argparse
import json
import sys

from market_pulse.config.settings import get_settings
from market_pulse.llm.langfuse_metrics import get_langfuse_client
from market_pulse.llm.langfuse_usage import summarize_run_usage

_COLUMNS = (
    ("trace", "Trace", 18),
    ("competitor_run_id", "Competitor run", 16),
    ("competitor", "Competitor", 14),
    ("llm_calls", "LLM calls", 10),
    ("cache_hits", "Cache hits", 11),
    ("requests", "Requests", 9),
    ("input_tokens", "Input tok", 11),
    ("output_tokens", "Output tok", 11),
    ("total_tokens", "Total tok", 11),
    ("total_cost", "Cost", 10),
)


def _format(row: dict, key: str) -> str:
    value = row.get(key)
    if value is None:
        return "-"
    if key == "total_cost":
        return f"{value:.4f}"
    return str(value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id", help="Run ID, e.g. RUN-1001 (the Langfuse session ID).")
    parser.add_argument("--json", action="store_true", help="Print the summary as JSON.")
    args = parser.parse_args()

    client = get_langfuse_client(get_settings())
    if client is None:
        parser.exit(1, "Langfuse is not enabled/configured (LANGFUSE_ENABLED and keys).\n")

    summary = summarize_run_usage(args.run_id, client)
    if summary["total"]["llm_calls"] and not summary["total"]["total_tokens"]:
        print(
            "Warning: LLM calls were recorded, but no token usage was captured. "
            "The displayed zero token totals do not establish actual usage.",
            file=sys.stderr,
        )
    if args.json:
        print(json.dumps(summary, indent=2))
        return
    if not summary["rows"]:
        parser.exit(1, f"No Langfuse traces found in session {args.run_id!r}.\n")

    print("  ".join(label.ljust(width) for _, label, width in _COLUMNS))
    for row in [*summary["rows"], summary["total"]]:
        print("  ".join(_format(row, key).ljust(width) for key, _, width in _COLUMNS))


if __name__ == "__main__":
    main()
