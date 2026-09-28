"""Read one run's LLM usage back from Langfuse.

Every trace of a run is in the Langfuse session ``run_id`` (see
``langfuse_metrics.llm_trace``). This sums that session's generations per
unit of work -- each competitor run, the Omantel reference preparation and
the run report -- and for the whole run.
"""

from __future__ import annotations

from typing import Any

from langfuse import Langfuse

_PAGE_SIZE = 100
_COUNTERS = ("generations", "llm_calls", "cache_hits", "requests", "input_tokens", "output_tokens")


def _empty_row(name: str, competitor_run_id: str | None, competitor: str | None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "trace": name,
        "competitor_run_id": competitor_run_id,
        "competitor": competitor,
        "traces": 0,
    }
    row.update({counter: 0 for counter in _COUNTERS})
    row["total_tokens"] = 0
    row["total_cost"] = 0.0
    return row


def _paginate(fetch, **kwargs) -> list[Any]:
    items: list[Any] = []
    page = 1
    while True:
        response = fetch(page=page, limit=_PAGE_SIZE, **kwargs)
        items.extend(response.data)
        if page >= (response.meta.total_pages or 1):
            return items
        page += 1


def _generation_cost(generation: Any) -> float:
    if generation.calculated_total_cost is not None:
        return float(generation.calculated_total_cost)
    return float((generation.cost_details or {}).get("total", 0.0))


def _add(target: dict[str, Any], source: dict[str, Any]) -> None:
    for counter in (*_COUNTERS, "traces", "total_tokens"):
        target[counter] += source[counter]
    target["total_cost"] += source["total_cost"]


def summarize_run_usage(run_id: str, client: Langfuse) -> dict[str, Any]:
    """Return per-unit rows and run totals for the Langfuse session ``run_id``.

    Retried competitor runs produce several traces with the same
    ``competitor_run_id``; they are summed into one row.
    """

    rows: dict[tuple[str, str | None], dict[str, Any]] = {}
    for trace in _paginate(client.api.trace.list, session_id=run_id):
        metadata = trace.metadata or {}
        competitor_run_id = metadata.get("competitor_run_id")
        key = (trace.name or "unnamed", competitor_run_id)
        row = rows.setdefault(key, _empty_row(key[0], competitor_run_id, metadata.get("competitor")))
        row["traces"] += 1

        generations = _paginate(
            client.api.observations.get_many, trace_id=trace.id, type="GENERATION"
        )
        for generation in generations:
            usage = generation.usage_details or {}
            generation_metadata = generation.metadata or {}
            row["generations"] += 1
            row["llm_calls"] += int(generation_metadata.get("llm_calls", 0))
            row["cache_hits"] += int(generation_metadata.get("cache_hits", usage.get("cached", 0)))
            row["requests"] += int(generation_metadata.get("requests", 0))
            row["input_tokens"] += int(usage.get("input", 0))
            row["output_tokens"] += int(usage.get("output", 0))
            row["total_cost"] += _generation_cost(generation)
        row["total_tokens"] = row["input_tokens"] + row["output_tokens"]

    ordered = sorted(
        rows.values(), key=lambda row: (row["competitor_run_id"] is not None, row["trace"],
                                        row["competitor_run_id"] or "")
    )
    total = _empty_row("TOTAL", None, None)
    for row in ordered:
        _add(total, row)
    return {"run_id": run_id, "rows": ordered, "total": total}
