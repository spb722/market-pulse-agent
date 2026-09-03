"""Exact Redis request/response cache for structured LLM calls.

The cache is deliberately content-addressed rather than semantic: identical
stage inputs, model settings, prompt versions and output schemas share one
entry across pipeline run IDs. Cached values contain only schema-validated
structured responses; exceptions and deterministic fallbacks are never
stored.

Redis is an optimization, not a pipeline dependency. With the default
``llm_cache_fail_open=True``, Redis connection/authentication failures are
logged and the original LLM invocation proceeds normally.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import threading
from collections import Counter, defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Callable, Mapping, Sequence, TypeVar

from pydantic import BaseModel, ValidationError
from redis import Redis
from redis.exceptions import RedisError

from market_pulse.config.settings import Settings, get_settings
from market_pulse.llm.langfuse_metrics import TokenUsageCollector, record_llm_metrics

logger = logging.getLogger(__name__)

StructuredModel = TypeVar("StructuredModel", bound=BaseModel)

COMPETITOR_CLASSIFICATION = "competitor_classification"
OMANTEL_ENRICHMENT = "omantel_enrichment"
PLAN_MATCHING = "plan_matching"
NARRATIVE_GENERATION = "narrative_generation"
PORTFOLIO_ANALYSIS = "portfolio_analysis"

_TTL_SETTING_BY_STAGE = {
    COMPETITOR_CLASSIFICATION: "llm_cache_competitor_ttl_seconds",
    OMANTEL_ENRICHMENT: "llm_cache_omantel_ttl_seconds",
    PLAN_MATCHING: "llm_cache_matching_ttl_seconds",
    NARRATIVE_GENERATION: "llm_cache_narrative_ttl_seconds",
    PORTFOLIO_ANALYSIS: "llm_cache_portfolio_ttl_seconds",
}


class CacheStats:
    """Small process-local counter set used for logs and verification."""

    def __init__(self) -> None:
        self._counts: dict[str, Counter[str]] = defaultdict(Counter)
        self._lock = threading.Lock()

    def increment(self, stage: str, event: str, amount: int = 1) -> None:
        with self._lock:
            self._counts[stage][event] += amount

    def snapshot(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return {stage: dict(counts) for stage, counts in self._counts.items()}

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()


_stats = CacheStats()


def get_llm_cache_stats() -> dict[str, dict[str, int]]:
    return _stats.snapshot()


def reset_llm_cache_stats() -> None:
    _stats.reset()


class LLMResponseCache:
    """Redis-backed exact cache for Pydantic structured LLM responses."""

    def __init__(self, settings: Settings, client: Redis | None = None) -> None:
        self.settings = settings
        self.enabled = settings.llm_cache_enabled
        self._client = client
        self._warned_unavailable = False

    @property
    def client(self) -> Redis:
        if self._client is None:
            self._client = Redis.from_url(
                self.settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=self.settings.llm_cache_socket_timeout_seconds,
                socket_timeout=self.settings.llm_cache_socket_timeout_seconds,
            )
        return self._client

    def ttl_for_stage(self, stage: str) -> int:
        setting_name = _TTL_SETTING_BY_STAGE.get(stage)
        if setting_name is None:
            return self.settings.llm_cache_default_ttl_seconds
        return int(getattr(self.settings, setting_name))

    def effective_ttl(self, stage: str) -> int:
        base_ttl = self.ttl_for_stage(stage)
        jitter_percent = self.settings.llm_cache_ttl_jitter_percent
        if jitter_percent == 0:
            return base_ttl

        variation = base_ttl * jitter_percent / 100
        return max(1, round(random.uniform(base_ttl - variation, base_ttl + variation)))

    def make_key(
        self,
        stage: str,
        request: Mapping[str, Any],
        output_model: type[BaseModel],
        prompt_version: str,
    ) -> str:
        envelope = {
            "stage": stage,
            "request": request,
            "model": self.settings.openai_model,
            "temperature": self.settings.openai_temperature,
            "base_url": self.settings.openai_base_url,
            "prompt_version": prompt_version,
            "output_schema": output_model.model_json_schema(),
        }
        canonical = json.dumps(
            envelope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return (
            f"{self.settings.llm_cache_namespace}:"
            f"{self.settings.llm_cache_key_version}:{stage}:{digest}"
        )

    def lookup(
        self,
        stage: str,
        request: Mapping[str, Any],
        output_model: type[StructuredModel],
        prompt_version: str,
    ) -> tuple[str | None, StructuredModel | None]:
        if not self.enabled:
            _stats.increment(stage, "bypass")
            return None, None

        key = self.make_key(stage, request, output_model, prompt_version)

        try:
            raw = self.client.get(key)
        except RedisError as exc:
            self._redis_failure(stage, "read", exc)
            return key, None

        if raw is None:
            _stats.increment(stage, "miss")
            logger.debug("LLM cache miss: stage=%s key=%s", stage, key)
            return key, None

        try:
            cached = json.loads(raw)
            result = output_model.model_validate(cached["response"])
        except (json.JSONDecodeError, KeyError, TypeError, ValidationError) as exc:
            _stats.increment(stage, "invalid")
            _stats.increment(stage, "miss")
            logger.warning("Discarding invalid LLM cache entry: stage=%s key=%s: %s", stage, key, exc)
            try:
                self.client.delete(key)
            except RedisError as delete_exc:
                self._redis_failure(stage, "delete", delete_exc)
            return key, None

        _stats.increment(stage, "hit")
        logger.debug("LLM cache hit: stage=%s key=%s", stage, key)
        return key, result

    def store(self, stage: str, key: str | None, response: BaseModel) -> None:
        if not self.enabled or key is None:
            return

        value = json.dumps(
            {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "response": response.model_dump(mode="json"),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        ttl = self.effective_ttl(stage)

        try:
            # redis-py's SET with EX writes the value and expiration atomically.
            self.client.set(key, value, ex=ttl)
        except RedisError as exc:
            self._redis_failure(stage, "write", exc)
            return

        _stats.increment(stage, "store")
        logger.debug("Stored LLM cache entry: stage=%s key=%s ttl=%d", stage, key, ttl)

    def _redis_failure(self, stage: str, operation: str, exc: RedisError) -> None:
        _stats.increment(stage, "error")
        if not self.settings.llm_cache_fail_open:
            raise exc

        if not self._warned_unavailable:
            logger.warning(
                "Redis LLM cache %s failed; continuing without cache because "
                "LLM_CACHE_FAIL_OPEN=true: %s",
                operation,
                exc,
            )
            self._warned_unavailable = True
        else:
            logger.debug("Redis LLM cache %s failed: %s", operation, exc)


@lru_cache
def get_llm_response_cache() -> LLMResponseCache:
    return LLMResponseCache(get_settings())


def reset_llm_response_cache() -> None:
    """Clear the cached singleton, primarily for tests/config reloads."""

    get_llm_response_cache.cache_clear()


def invoke_structured_cached(
    *,
    stage: str,
    request: Mapping[str, Any],
    output_model: type[StructuredModel],
    prompt_version: str,
    invoke: Callable[[], StructuredModel],
    cache: LLMResponseCache | None = None,
) -> StructuredModel:
    cache = cache or get_llm_response_cache()
    key, cached = cache.lookup(stage, request, output_model, prompt_version)
    if cached is not None:
        record_llm_metrics(
            stage=stage,
            usage=None,
            cached=1,
            settings=cache.settings,
            input_payload=request,
            output_payload=cached,
        )
        return cached

    usage = TokenUsageCollector() if cache.settings.langfuse_enabled else None
    response = invoke_with_callbacks(invoke, usage) if usage is not None else invoke()
    if isinstance(response, output_model):
        cache.store(stage, key, response)
    record_llm_metrics(
        stage=stage,
        usage=usage,
        cached=0,
        settings=cache.settings,
        input_payload=request,
        output_payload=response,
    )
    return response


def invoke_structured_batch_cached(
    *,
    stage: str,
    requests: Sequence[Mapping[str, Any]],
    output_model: type[StructuredModel],
    prompt_version: str,
    invoke_batch: Callable[[list[Mapping[str, Any]]], list[StructuredModel | Exception]],
    cache: LLMResponseCache | None = None,
) -> list[StructuredModel | Exception]:
    """Resolve cache hits and invoke one provider batch for only the misses."""

    cache = cache or get_llm_response_cache()

    if not cache.enabled:
        _stats.increment(stage, "bypass", len(requests))
        usage = TokenUsageCollector() if cache.settings.langfuse_enabled else None
        results = (
            invoke_batch_with_callbacks(invoke_batch, list(requests), usage)
            if usage is not None
            else invoke_batch(list(requests))
        )
        record_llm_metrics(
            stage=stage,
            usage=usage,
            cached=0,
            settings=cache.settings,
            input_payload=list(requests),
            output_payload=results,
        )
        return results

    results: list[StructuredModel | Exception | None] = [None] * len(requests)
    miss_indices: list[int] = []
    miss_keys: list[str | None] = []
    miss_requests: list[Mapping[str, Any]] = []

    for index, request in enumerate(requests):
        key, cached = cache.lookup(stage, request, output_model, prompt_version)
        if cached is not None:
            results[index] = cached
        else:
            miss_indices.append(index)
            miss_keys.append(key)
            miss_requests.append(request)

    usage = TokenUsageCollector() if cache.settings.langfuse_enabled else None
    if miss_requests:
        fresh_results = (
            invoke_batch_with_callbacks(invoke_batch, miss_requests, usage)
            if usage is not None
            else invoke_batch(miss_requests)
        )
        if len(fresh_results) != len(miss_requests):
            raise RuntimeError(
                "Structured LLM batch returned a different number of results "
                f"than requests ({len(fresh_results)} != {len(miss_requests)})."
            )
        for index, key, response in zip(miss_indices, miss_keys, fresh_results):
            results[index] = response
            if isinstance(response, output_model):
                cache.store(stage, key, response)

    # Every slot is filled either by a cache hit or the provider batch.
    if any(result is None for result in results):
        raise RuntimeError("Structured LLM cache batch left an unresolved result slot.")
    record_llm_metrics(
        stage=stage,
        usage=usage,
        cached=len(requests) - len(miss_requests),
        settings=cache.settings,
        input_payload=list(requests),
        output_payload=[result for result in results if result is not None],
    )
    return [result for result in results if result is not None]


def invoke_with_callbacks(
    invoke: Callable[..., StructuredModel],
    usage: TokenUsageCollector,
) -> StructuredModel:
    """Invoke a zero-argument closure while attaching the usage callback."""

    try:
        return invoke(config={"callbacks": [usage]})
    except TypeError as exc:
        # Existing call sites use zero-argument closures. Until all callers
        # accept RunnableConfig, retain compatibility with arbitrary/test
        # closures without hiding TypeErrors raised inside the LLM call.
        if "unexpected keyword argument 'config'" not in str(exc):
            raise
        return invoke()


def invoke_batch_with_callbacks(
    invoke_batch: Callable[..., list[StructuredModel | Exception]],
    requests: list[Mapping[str, Any]],
    usage: TokenUsageCollector,
) -> list[StructuredModel | Exception]:
    """Invoke a batch closure while attaching the usage callback."""

    try:
        return invoke_batch(requests, config={"callbacks": [usage]})
    except TypeError as exc:
        if "unexpected keyword argument 'config'" not in str(exc):
            raise
        return invoke_batch(requests)
