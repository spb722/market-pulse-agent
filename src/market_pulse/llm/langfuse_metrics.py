"""Langfuse metrics and optional I/O for structured LLM calls.

Every observation contains input tokens, output tokens, the number of exact
Redis response-cache hits, and total cost. Logical request/response payloads
are included only when ``langfuse_capture_io`` is enabled.
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from functools import lru_cache
from typing import Any, Iterator

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langfuse import Langfuse
from pydantic import BaseModel

from market_pulse.config.settings import Settings

logger = logging.getLogger(__name__)


class TokenUsageCollector(BaseCallbackHandler):
    """Thread-safe token collector compatible with invoke and batch calls."""

    def __init__(self) -> None:
        super().__init__()
        self.input_tokens = 0
        self.output_tokens = 0
        self._lock = threading.Lock()

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
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens


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


def record_llm_metrics(
    *,
    stage: str,
    usage: TokenUsageCollector | None,
    cached: int,
    settings: Settings,
    input_payload: Any = None,
    output_payload: Any = None,
) -> str | None:
    """Record one generation and return its trace URL, if any."""

    client = _get_client(settings)
    if client is None:
        return None

    input_tokens = usage.input_tokens if usage is not None else 0
    output_tokens = usage.output_tokens if usage is not None else 0
    total_cost = (
        input_tokens * settings.langfuse_input_cost_per_million_tokens
        + output_tokens * settings.langfuse_output_cost_per_million_tokens
    ) / 1_000_000

    observation = {
        "as_type": "generation",
        "name": stage,
        "usage_details": {
            "input": input_tokens,
            "output": output_tokens,
            "cached": cached,
        },
        "cost_details": {"total": total_cost},
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
