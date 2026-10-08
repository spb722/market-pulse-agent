"""Tests for Langfuse metrics and optional input/output capture."""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from market_pulse.config.settings import Settings
from market_pulse.llm import langfuse_metrics
from market_pulse.llm.langfuse_metrics import (
    TokenUsageCollector,
    llm_trace,
    llm_workflow_span,
    record_llm_metrics,
    update_workflow_span,
)


class FakeSpan:
    def __init__(self, name: str | None = None) -> None:
        self.name = name
        self.updates: list[dict] = []

    def update(self, **kwargs):
        self.updates.append(kwargs)


class FakeLangfuse:
    def __init__(self) -> None:
        self.observations: list[dict] = []

        self.spans: list[FakeSpan] = []

    @contextmanager
    def start_as_current_observation(self, **kwargs):
        self.observations.append(kwargs)
        span = FakeSpan(kwargs.get("name"))
        self.spans.append(span)
        yield span

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
        langfuse_capture_io=False,
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
            "usage_details": {"input": 100, "output": 50, "total": 150, "cached": 3},
            "cost_details": {"total": 0.0004},
            "metadata": {"llm_calls": 0, "cache_hits": 3, "requests": 1},
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
            "usage_details": {"input": 0, "output": 0, "total": 0, "cached": 1},
            "cost_details": {"total": 0.0},
            "metadata": {"llm_calls": 0, "cache_hits": 1, "requests": 1},
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


def _usage_result(input_tokens: int, output_tokens: int) -> LLMResult:
    return LLMResult(
        generations=[
            [
                ChatGeneration(
                    message=AIMessage(
                        content="",
                        usage_metadata={
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "total_tokens": input_tokens + output_tokens,
                        },
                    )
                )
            ]
        ]
    )


def _enabled_settings(**overrides) -> Settings:
    return Settings(
        _env_file=None,
        langfuse_enabled=True,
        langfuse_capture_io=False,
        langfuse_public_key="public",
        langfuse_secret_key="secret",
        **overrides,
    )


def _usage(input_tokens: int, output_tokens: int, llm_calls: int) -> TokenUsageCollector:
    usage = TokenUsageCollector()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    usage.llm_calls = llm_calls
    return usage


@pytest.fixture
def propagated(monkeypatch):
    calls: list[dict] = []

    @contextmanager
    def fake_propagate_attributes(**kwargs):
        calls.append(kwargs)
        yield

    monkeypatch.setattr(langfuse_metrics, "propagate_attributes", fake_propagate_attributes)
    return calls


def test_token_collector_counts_one_llm_call_per_provider_response_and_error():
    collector = TokenUsageCollector()

    collector.on_llm_end(_usage_result(10, 2))
    collector.on_llm_end(_usage_result(7, 3))
    collector.on_llm_error(RuntimeError("provider unavailable"))

    assert collector.llm_calls == 3
    assert collector.input_tokens == 17
    assert collector.output_tokens == 5


def test_record_reports_llm_calls_and_batch_request_count(monkeypatch):
    client = FakeLangfuse()
    monkeypatch.setattr(langfuse_metrics, "_get_client", lambda settings: client)

    record_llm_metrics(
        stage="omantel_enrichment",
        usage=_usage(300, 90, llm_calls=3),
        cached=2,
        requests=5,
        settings=_enabled_settings(),
    )

    assert client.observations[0]["metadata"] == {"llm_calls": 3, "cache_hits": 2, "requests": 5}
    assert client.observations[0]["usage_details"]["total"] == 390


def test_llm_trace_groups_generations_under_run_session_and_competitor(monkeypatch, propagated):
    client = FakeLangfuse()
    monkeypatch.setattr(langfuse_metrics, "_get_client", lambda settings: client)
    settings = _enabled_settings(
        langfuse_input_cost_per_million_tokens=1.0,
        langfuse_output_cost_per_million_tokens=5.0,
    )

    with llm_trace(
        name="competitor_run",
        settings=settings,
        run_id="RUN-1001",
        competitor_run_id="CR-002",
        competitor="Vodafone",
    ) as totals:
        record_llm_metrics(
            stage="competitor_classification", usage=_usage(1000, 200, 2), cached=1,
            requests=3, settings=settings,
        )
        record_llm_metrics(
            stage="plan_matching", usage=None, cached=1, settings=settings,
        )

    attributes = {"run_id": "RUN-1001", "competitor_run_id": "CR-002", "competitor": "Vodafone"}
    assert client.observations[0] == {
        "as_type": "span", "name": "competitor_run", "metadata": attributes,
    }
    assert propagated == [
        {
            "session_id": "RUN-1001",
            "trace_name": "competitor_run",
            "metadata": attributes,
            "tags": ["run:RUN-1001", "competitor_run:CR-002", "competitor:Vodafone"],
        }
    ]
    assert totals.as_dict() == {
        "llm_calls": 2,
        "cache_hits": 2,
        "requests": 4,
        "input_tokens": 1000,
        "output_tokens": 200,
        "total_tokens": 1200,
        "total_cost": 0.002,
    }
    root_span = client.spans[0]
    assert root_span.updates == [{"metadata": {**attributes, **totals.as_dict()}}]


def test_llm_trace_adds_offer_scope_metadata_and_tag(monkeypatch, propagated):
    client = FakeLangfuse()
    monkeypatch.setattr(langfuse_metrics, "_get_client", lambda settings: client)

    with llm_trace(
        name="competitor_run",
        settings=_enabled_settings(),
        run_id="RUN-1",
        competitor_run_id="CR-1",
        offer_scope="BTL",
    ):
        pass

    assert propagated[0]["metadata"]["offer_scope"] == "BTL"
    assert propagated[0]["tags"] == ["run:RUN-1", "competitor_run:CR-1", "offer_scope:BTL"]
    assert client.observations[0]["metadata"]["offer_scope"] == "BTL"


def test_nested_llm_trace_keeps_its_usage_out_of_the_outer_trace(monkeypatch, propagated):
    client = FakeLangfuse()
    monkeypatch.setattr(langfuse_metrics, "_get_client", lambda settings: client)
    settings = _enabled_settings()

    with llm_trace(
        name="competitor_run", settings=settings, run_id="RUN-1", competitor_run_id="CR-1"
    ) as competitor_totals:
        with llm_trace(name="omantel_reference", settings=settings, run_id="RUN-1") as omantel:
            record_llm_metrics(
                stage="omantel_enrichment", usage=_usage(50, 5, 4), cached=0, requests=4,
                settings=settings,
            )
        record_llm_metrics(
            stage="plan_matching", usage=_usage(10, 1, 1), cached=0, settings=settings,
        )

    assert omantel.llm_calls == 4
    assert competitor_totals.llm_calls == 1
    assert propagated[1]["metadata"] == {"run_id": "RUN-1"}
    assert propagated[1]["tags"] == ["run:RUN-1"]


def test_llm_trace_starts_a_new_root_trace_when_nested(monkeypatch, propagated):
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace import TracerProvider

    seen: list[object] = []

    class RecordingLangfuse(FakeLangfuse):
        @contextmanager
        def start_as_current_observation(self, **kwargs):
            seen.append(otel_trace.get_current_span())
            yield FakeSpan()

    monkeypatch.setattr(langfuse_metrics, "_get_client", lambda settings: RecordingLangfuse())
    tracer = TracerProvider().get_tracer("test")

    with tracer.start_as_current_span("competitor_run") as outer:
        with llm_trace(name="omantel_reference", settings=_enabled_settings(), run_id="RUN-1"):
            pass
        restored = otel_trace.get_current_span()

    assert seen == [otel_trace.INVALID_SPAN]
    assert restored is outer


def test_llm_trace_propagates_body_errors_and_resets_totals(monkeypatch, propagated):
    client = FakeLangfuse()
    monkeypatch.setattr(langfuse_metrics, "_get_client", lambda settings: client)
    settings = _enabled_settings()

    with pytest.raises(ValueError, match="stage failed"):
        with llm_trace(name="competitor_run", settings=settings, run_id="RUN-1"):
            raise ValueError("stage failed")

    assert langfuse_metrics._active_totals.get() is None
    assert client.spans[0].updates == []


def test_llm_trace_collects_totals_when_langfuse_disabled(monkeypatch):
    monkeypatch.setattr(langfuse_metrics, "_get_client", lambda settings: None)
    settings = Settings(_env_file=None, langfuse_enabled=False)

    with llm_trace(name="competitor_run", settings=settings, run_id="RUN-1") as totals:
        record_llm_metrics(stage="plan_matching", usage=None, cached=1, settings=settings)

    assert totals.cache_hits == 1
    assert totals.requests == 1


def test_llm_trace_fails_open_when_langfuse_span_cannot_start(monkeypatch):
    class BrokenLangfuse(FakeLangfuse):
        def start_as_current_observation(self, **kwargs):
            raise RuntimeError("langfuse down")

    monkeypatch.setattr(langfuse_metrics, "_get_client", lambda settings: BrokenLangfuse())
    settings = _enabled_settings()

    with llm_trace(name="competitor_run", settings=settings, run_id="RUN-1") as totals:
        record_llm_metrics(stage="plan_matching", usage=None, cached=1, settings=settings)

    assert totals.cache_hits == 1


def test_llm_trace_makes_propagated_attributes_ascii(monkeypatch, propagated):
    monkeypatch.setattr(langfuse_metrics, "_get_client", lambda settings: FakeLangfuse())

    with llm_trace(
        name="competitor_run", settings=_enabled_settings(), run_id="RUN-1",
        competitor="أوريدو",
    ):
        pass

    assert propagated[0]["metadata"]["competitor"] == "??????"
    assert all(tag.isascii() for tag in propagated[0]["tags"])
