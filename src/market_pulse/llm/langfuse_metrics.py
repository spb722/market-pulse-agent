"""Langfuse metrics and optional I/O for structured LLM calls.

Every observation contains input tokens, output tokens, the number of exact
Redis response-cache hits, and total cost, plus ``llm_calls`` (provider calls
actually made), ``cache_hits`` and ``requests`` in its metadata. Logical
request/response payloads are included only when ``langfuse_capture_io`` is
enabled.

``llm_trace`` groups the generations of one unit of work (a competitor run,
the run's Omantel reference preparation, or a run report) into one Langfuse
trace, puts every trace of a run in the Langfuse session ``run_id``, and
records the trace's usage totals on its root span.
"""

from __future__ import annotations

import logging
import threading
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Iterator

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langfuse import Langfuse, propagate_attributes
from opentelemetry import context as otel_context
from pydantic import BaseModel

from market_pulse.config.settings import Settings

logger = logging.getLogger(__name__)


class TokenUsageCollector(BaseCallbackHandler):
    """Thread-safe token collector compatible with invoke and batch calls."""

    def __init__(self) -> None:
        super().__init__()
        self.input_tokens = 0
        self.output_tokens = 0
        self.llm_calls = 0
        self._lock = threading.Lock()

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        # A failed provider call still counts as a call made.
        with self._lock:
            self.llm_calls += 1

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        input_tokens = 0
        output_tokens = 0

        for generations in response.generations:
            for generation in generations:
                if not isinstance(generation, ChatGeneration):
                    continue
                message = generation.message
                if not isinstance(message, AIMessage) or not message.usage_metadata:
                    continue
                input_tokens += int(message.usage_metadata.get("input_tokens", 0))
                output_tokens += int(message.usage_metadata.get("output_tokens", 0))

        with self._lock:
            self.llm_calls += 1
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens


@dataclass
class LLMUsageTotals:
    """Usage rolled up over every generation recorded inside one ``llm_trace``."""

    llm_calls: int = 0
    cache_hits: int = 0
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_cost: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def add(
        self,
        *,
        llm_calls: int,
        cache_hits: int,
        requests: int,
        input_tokens: int,
        output_tokens: int,
        total_cost: float,
    ) -> None:
        with self._lock:
            self.llm_calls += llm_calls
            self.cache_hits += cache_hits
            self.requests += requests
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens
            self.total_cost += total_cost

    def as_dict(self) -> dict[str, Any]:
        return {
            "llm_calls": self.llm_calls,
            "cache_hits": self.cache_hits,
            "requests": self.requests,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "total_cost": round(self.total_cost, 8),
        }


_active_totals: ContextVar[LLMUsageTotals | None] = ContextVar(
    "market_pulse_llm_usage_totals", default=None
)


@lru_cache(maxsize=4)
def _build_client(
    public_key: str,
    secret_key: str,
    base_url: str,
    environment: str,
) -> Langfuse:
    return Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        base_url=base_url,
        environment=environment,
    )


def _get_client(settings: Settings) -> Langfuse | None:
    if not settings.langfuse_enabled:
        return None
    secret_key = settings.langfuse_secret_key.get_secret_value()
    if not settings.langfuse_public_key or not secret_key:
        logger.warning("Langfuse metrics disabled because credentials are missing")
        return None
    try:
        return _build_client(
            settings.langfuse_public_key,
            secret_key,
            settings.langfuse_base_url,
            settings.langfuse_environment,
        )
    except Exception as exc:  # noqa: BLE001 - observability must fail open
        logger.warning("Could not initialize Langfuse metrics; continuing: %s", exc)
        return None


def get_langfuse_client(settings: Settings) -> Langfuse | None:
    """The configured Langfuse client, or ``None`` when disabled/unconfigured."""

    return _get_client(settings)


def record_llm_metrics(
    *,
    stage: str,
    usage: TokenUsageCollector | None,
    cached: int,
    settings: Settings,
    input_payload: Any = None,
    output_payload: Any = None,
    requests: int = 1,
) -> str | None:
    """Record one generation and return its trace URL, if any.

    ``requests`` is the number of logical requests this generation covers
    (a batch covers several); ``cached`` of them were served from Redis and
    ``usage.llm_calls`` is how many provider calls were actually made.
    """

    input_tokens = usage.input_tokens if usage is not None else 0
    output_tokens = usage.output_tokens if usage is not None else 0
    llm_calls = usage.llm_calls if usage is not None else 0
    total_cost = (
        input_tokens * settings.langfuse_input_cost_per_million_tokens
        + output_tokens * settings.langfuse_output_cost_per_million_tokens
    ) / 1_000_000

    totals = _active_totals.get()
    if totals is not None:
        totals.add(
            llm_calls=llm_calls,
            cache_hits=cached,
            requests=requests,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_cost=total_cost,
        )

    client = _get_client(settings)
    if client is None:
        return None

    observation = {
        "as_type": "generation",
        "name": stage,
        "usage_details": {
            "input": input_tokens,
            "output": output_tokens,
            # Explicit, so Langfuse does not sum the ``cached`` hit count
            # into the trace's total tokens.
            "total": input_tokens + output_tokens,
            "cached": cached,
        },
        "cost_details": {"total": total_cost},
        "metadata": {"llm_calls": llm_calls, "cache_hits": cached, "requests": requests},
    }
    if settings.langfuse_capture_io:
        observation["input"] = _serialize_payload(input_payload)
        observation["output"] = _serialize_payload(output_payload)

    try:
        with client.start_as_current_observation(
            **observation,
        ):
            trace_url = client.get_trace_url()
        return trace_url
    except Exception as exc:  # noqa: BLE001 - observability must fail open
        logger.warning("Could not record Langfuse LLM metrics; continuing: %s", exc)
        return None


def _serialize_payload(value: Any) -> Any:
    """Convert structured results and batch exceptions to JSON-safe values."""

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Exception):
        return {"error": str(value)}
    if isinstance(value, dict):
        return {key: _serialize_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize_payload(item) for item in value]
    return value


@contextmanager
def llm_workflow_span(
    *,
    name: str,
    settings: Settings,
    input_payload: Any = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[Any]:
    """Create an optional parent span for a multi-generation LLM workflow."""

    client = _get_client(settings)
    if client is None:
        yield None
        return

    observation: dict[str, Any] = {"as_type": "span", "name": name}
    if metadata:
        observation["metadata"] = _serialize_payload(metadata)
    if settings.langfuse_capture_io:
        observation["input"] = _serialize_payload(input_payload)
    try:
        context = client.start_as_current_observation(**observation)
        span = context.__enter__()
    except Exception as exc:  # noqa: BLE001 - observability must fail open
        logger.warning("Could not start Langfuse workflow span; continuing: %s", exc)
        yield None
        return

    try:
        yield span
    except BaseException as body_exc:
        try:
            context.__exit__(type(body_exc), body_exc, body_exc.__traceback__)
        except Exception as exc:  # noqa: BLE001 - observability must fail open
            logger.warning("Could not close Langfuse workflow span; continuing: %s", exc)
        raise
    else:
        try:
            context.__exit__(None, None, None)
        except Exception as exc:  # noqa: BLE001 - observability must fail open
            logger.warning("Could not close Langfuse workflow span; continuing: %s", exc)


def _ascii_attribute(value: str) -> str:
    """Propagated Langfuse attributes must be US-ASCII and at most 200 chars."""

    return value.encode("ascii", "replace").decode("ascii")[:200]


@contextmanager
def llm_trace(
    *,
    name: str,
    settings: Settings,
    run_id: str,
    competitor_run_id: str | None = None,
    competitor: str | None = None,
    offer_scope: str | None = None,
) -> Iterator[LLMUsageTotals]:
    """Group the LLM generations of one unit of work into one Langfuse trace.

    Always starts a new root trace (even when nested, e.g. the run's Omantel
    preparation triggered from inside a competitor run) so each trace's
    totals describe exactly one unit of work. The trace joins the Langfuse
    session ``run_id`` and is tagged/filterable by run and competitor run.
    Yields the usage totals, which are collected even when Langfuse is off.
    """

    totals = LLMUsageTotals()
    totals_token = _active_totals.set(totals)
    try:
        client = _get_client(settings)
        if client is None:
            yield totals
            return

        attributes = {"run_id": _ascii_attribute(run_id)}
        tags = [f"run:{run_id}"]
        if competitor_run_id:
            attributes["competitor_run_id"] = _ascii_attribute(competitor_run_id)
            tags.append(f"competitor_run:{competitor_run_id}")
        if competitor:
            attributes["competitor"] = _ascii_attribute(competitor)
            tags.append(f"competitor:{competitor}")
        if offer_scope:
            attributes["offer_scope"] = _ascii_attribute(offer_scope)
            tags.append(f"offer_scope:{offer_scope}")

        context_token = otel_context.attach(otel_context.Context())
        stack = ExitStack()
        try:
            span = None
            try:
                span = stack.enter_context(
                    client.start_as_current_observation(
                        as_type="span", name=name, metadata=attributes
                    )
                )
                stack.enter_context(
                    propagate_attributes(
                        session_id=_ascii_attribute(run_id),
                        trace_name=_ascii_attribute(name),
                        metadata=attributes,
                        tags=[_ascii_attribute(tag) for tag in tags],
                    )
                )
            except Exception as exc:  # noqa: BLE001 - observability must fail open
                logger.warning("Could not start Langfuse trace %s; continuing: %s", name, exc)
                _close_stack(stack, None)
                span = None
            if span is None:
                yield totals
                return

            try:
                yield totals
            except BaseException as body_exc:
                _close_stack(stack, body_exc)
                raise
            try:
                span.update(metadata={**attributes, **totals.as_dict()})
            except Exception as exc:  # noqa: BLE001 - observability must fail open
                logger.warning("Could not record Langfuse trace totals; continuing: %s", exc)
            _close_stack(stack, None)
        finally:
            otel_context.detach(context_token)
    finally:
        _active_totals.reset(totals_token)


def _close_stack(stack: ExitStack, body_exc: BaseException | None) -> None:
    try:
        if body_exc is None:
            stack.close()
        else:
            stack.__exit__(type(body_exc), body_exc, body_exc.__traceback__)
    except Exception as exc:  # noqa: BLE001 - observability must fail open
        logger.warning("Could not close Langfuse trace; continuing: %s", exc)


def update_workflow_span(span: Any, output_payload: Any, *, settings: Settings) -> None:
    """Best-effort output update for a workflow span."""

    if span is None or not settings.langfuse_capture_io:
        return
    try:
        span.update(output=_serialize_payload(output_payload))
    except Exception as exc:  # noqa: BLE001 - observability must fail open
        logger.warning("Could not update Langfuse workflow span; continuing: %s", exc)


def flush_langfuse(settings: Settings) -> None:
    """Flush queued observations at a pipeline/script boundary."""

    client = _get_client(settings)
    if client is None:
        return
    try:
        client.flush()
    except Exception as exc:  # noqa: BLE001 - observability must fail open
        logger.warning("Could not flush Langfuse LLM metrics; continuing: %s", exc)
