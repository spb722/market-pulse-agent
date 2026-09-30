"""Tests for summing one run's LLM usage from its Langfuse session."""

from __future__ import annotations

from types import SimpleNamespace

from market_pulse.llm.langfuse_usage import summarize_run_usage


def _page(items, total_pages=1):
    return SimpleNamespace(data=items, meta=SimpleNamespace(total_pages=total_pages))


def _cursor_page(items, cursor=None):
    return SimpleNamespace(data=items, meta=SimpleNamespace(cursor=cursor))


def _trace(trace_id, name, **metadata):
    return SimpleNamespace(id=trace_id, name=name, metadata=metadata)


def _generation(input_tokens, output_tokens, llm_calls, cache_hits, requests, cost):
    return SimpleNamespace(
        usage_details={"input": input_tokens, "output": output_tokens, "cached": cache_hits},
        metadata={"llm_calls": llm_calls, "cache_hits": cache_hits, "requests": requests},
        total_cost=cost,
        cost_details={"total": cost},
    )


class FakeApi:
    def __init__(self, trace_pages, generations_by_trace):
        self.trace_pages = trace_pages
        self.generations_by_trace = generations_by_trace
        self.trace_queries = []
        self.observation_queries = []
        self.trace = SimpleNamespace(list=self._list_traces)
        self.observations = SimpleNamespace(get_many=self._get_observations)

    def _list_traces(self, *, page, limit, session_id):
        self.trace_queries.append(session_id)
        return _page(self.trace_pages[page - 1], total_pages=len(self.trace_pages))

    def _get_observations(self, *, limit, fields, trace_id, type, cursor=None):
        assert type == "GENERATION"
        assert fields == "core,basic,metadata,usage"
        self.observation_queries.append((trace_id, cursor))
        generations = self.generations_by_trace.get(trace_id, [])
        pages = generations if generations and isinstance(generations[0], list) else [generations]
        index = int(cursor) if cursor else 0
        next_cursor = str(index + 1) if index + 1 < len(pages) else None
        return _cursor_page(pages[index], next_cursor)


def test_summarize_run_usage_per_competitor_and_total():
    api = FakeApi(
        trace_pages=[
            [
                _trace("t1", "competitor_run", run_id="RUN-1", competitor_run_id="CR-001",
                       competitor="Ooredoo"),
                _trace("t2", "omantel_reference", run_id="RUN-1"),
            ],
            # Second page: CR-001 retried, plus another competitor.
            [
                _trace("t3", "competitor_run", run_id="RUN-1", competitor_run_id="CR-001",
                       competitor="Ooredoo"),
                _trace("t4", "competitor_run", run_id="RUN-1", competitor_run_id="CR-002",
                       competitor="Vodafone"),
            ],
        ],
        generations_by_trace={
            "t1": [_generation(100, 10, 2, 0, 2, 0.001), _generation(0, 0, 0, 1, 1, 0.0)],
            "t2": [_generation(500, 50, 5, 0, 5, 0.005)],
            "t3": [_generation(40, 4, 1, 0, 1, 0.0004)],
            "t4": [_generation(200, 20, 3, 1, 4, 0.002)],
        },
    )

    summary = summarize_run_usage("RUN-1", SimpleNamespace(api=api))

    assert api.trace_queries == ["RUN-1", "RUN-1"]
    rows = {(row["trace"], row["competitor_run_id"]): row for row in summary["rows"]}
    assert list(rows) == [
        ("omantel_reference", None),
        ("competitor_run", "CR-001"),
        ("competitor_run", "CR-002"),
    ]
    ooredoo = rows[("competitor_run", "CR-001")]
    assert ooredoo["competitor"] == "Ooredoo"
    assert ooredoo["traces"] == 2
    assert ooredoo["llm_calls"] == 3
    assert ooredoo["cache_hits"] == 1
    assert ooredoo["requests"] == 4
    assert ooredoo["total_tokens"] == 154

    total = summary["total"]
    assert total["traces"] == 4
    assert total["generations"] == 5
    assert total["llm_calls"] == 11
    assert total["cache_hits"] == 2
    assert total["input_tokens"] == 840
    assert total["output_tokens"] == 84
    assert total["total_tokens"] == 924
    assert round(total["total_cost"], 6) == 0.0084


def test_summarize_run_usage_empty_session():
    api = FakeApi(trace_pages=[[]], generations_by_trace={})

    summary = summarize_run_usage("RUN-EMPTY", SimpleNamespace(api=api))

    assert summary["rows"] == []
    assert summary["total"]["llm_calls"] == 0


def test_summarize_run_usage_paginates_generations_by_cursor():
    api = FakeApi(
        trace_pages=[[_trace("t1", "competitor_run", competitor_run_id="CR-001")]],
        generations_by_trace={
            "t1": [
                [_generation(100, 10, 1, 0, 1, 0.001)],
                [_generation(200, 20, 1, 0, 1, 0.002)],
            ]
        },
    )

    summary = summarize_run_usage("RUN-1", SimpleNamespace(api=api))

    assert api.observation_queries == [("t1", None), ("t1", "1")]
    assert summary["total"]["llm_calls"] == 2
    assert summary["total"]["input_tokens"] == 300
    assert summary["total"]["output_tokens"] == 30
