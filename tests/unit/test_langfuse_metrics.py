"""Tests for Langfuse metrics and optional input/output capture."""

from __future__ import annotations

from contextlib import contextmanager

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from market_pulse.config.settings import Settings
from market_pulse.llm import langfuse_metrics
from market_pulse.llm.langfuse_metrics import (
    TokenUsageCollector,
    llm_workflow_span,
    record_llm_metrics,
    update_workflow_span,
)


class FakeSpan:
    def __init__(self) -> None:
        self.updates: list[dict] = []

    def update(self, **kwargs):
        self.updates.append(kwargs)


class FakeLangfuse:
    def __init__(self) -> None:
        self.observations: list[dict] = []

    @contextmanager
    def start_as_current_observation(self, **kwargs):
        self.observations.append(kwargs)
        yield FakeSpan()

    def get_trace_url(self):
        return "https://langfuse.test/trace/1"


def test_token_collector_reads_langchain_usage_metadata():
    collector = TokenUsageCollector()
    result = LLMResult(
        generations=[
            [
                ChatGeneration(
                    message=AIMessage(
                        content="",
                        usage_metadata={
                            "input_tokens": 12,
                            "output_tokens": 5,
                            "total_tokens": 17,
                        },
                    )
                )
            ]
        ]
    )

    collector.on_llm_end(result)

    assert collector.input_tokens == 12
    assert collector.output_tokens == 5


def test_record_contains_only_requested_usage_and_total_cost(monkeypatch):
    client = FakeLangfuse()
    monkeypatch.setattr(langfuse_metrics, "_get_client", lambda settings: client)
    settings = Settings(
        _env_file=None,
        langfuse_enabled=True,
        langfuse_public_key="public",
        langfuse_secret_key="secret",
        langfuse_input_cost_per_million_tokens=2.0,
        langfuse_output_cost_per_million_tokens=4.0,
    )
    usage = TokenUsageCollector()
    usage.input_tokens = 100
    usage.output_tokens = 50

    trace_url = record_llm_metrics(
        stage="competitor_classification",
        usage=usage,
        cached=3,
        settings=settings,
    )

    assert trace_url == "https://langfuse.test/trace/1"
    assert client.observations == [
        {
            "as_type": "generation",
            "name": "competitor_classification",
            "usage_details": {"input": 100, "output": 50, "cached": 3},
            "cost_details": {"total": 0.0004},
        }
    ]


def test_record_includes_structured_io_when_enabled(monkeypatch):
    client = FakeLangfuse()
    monkeypatch.setattr(langfuse_metrics, "_get_client", lambda settings: client)
    settings = Settings(
        _env_file=None,
        langfuse_enabled=True,
        langfuse_capture_io=True,
        langfuse_public_key="public",
        langfuse_secret_key="secret",
    )

    record_llm_metrics(
        stage="plan_matching",
        usage=None,
        cached=1,
        settings=settings,
        input_payload={"plan_id": "c1"},
        output_payload={"selected_plan_id": "o1"},
    )

    assert client.observations == [
        {
            "as_type": "generation",
            "name": "plan_matching",
            "usage_details": {"input": 0, "output": 0, "cached": 1},
            "cost_details": {"total": 0.0},
            "input": {"plan_id": "c1"},
            "output": {"selected_plan_id": "o1"},
        }
    ]


def test_workflow_span_captures_summary_io_when_enabled(monkeypatch):
    client = FakeLangfuse()
    monkeypatch.setattr(langfuse_metrics, "_get_client", lambda settings: client)
    settings = Settings(
        _env_file=None,
        langfuse_enabled=True,
        langfuse_capture_io=True,
        langfuse_public_key="public",
        langfuse_secret_key="secret",
    )

    with llm_workflow_span(
        name="portfolio_analysis",
        settings=settings,
        input_payload={"segments": ["postpaid:DATA"]},
        metadata={"run_id": "RUN-1"},
    ) as span:
        update_workflow_span(span, {"recommendations": 2}, settings=settings)

    assert client.observations == [
        {
            "as_type": "span",
            "name": "portfolio_analysis",
            "metadata": {"run_id": "RUN-1"},
            "input": {"segments": ["postpaid:DATA"]},
        }
    ]
    assert span.updates == [{"output": {"recommendations": 2}}]


def test_workflow_span_is_disabled_without_langfuse(monkeypatch):
    monkeypatch.setattr(langfuse_metrics, "_get_client", lambda settings: None)
    settings = Settings(_env_file=None, langfuse_enabled=False)

    with llm_workflow_span(name="portfolio_analysis", settings=settings) as span:
        assert span is None
