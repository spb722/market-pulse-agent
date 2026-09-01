"""Unit tests for the exact Redis-backed structured LLM cache."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel, ValidationError
from redis.exceptions import ConnectionError as RedisConnectionError

from market_pulse.config.settings import Settings
from market_pulse.llm.cache import (
    COMPETITOR_CLASSIFICATION,
    LLMResponseCache,
    OMANTEL_ENRICHMENT,
    get_llm_cache_stats,
    invoke_structured_batch_cached,
    invoke_structured_cached,
    reset_llm_cache_stats,
)
from market_pulse.llm.omantel_classifier import classify_omantel_plans
from market_pulse.llm.narrative_generator import generate_narrative
from market_pulse.llm.plan_classifier import enrich_one_plan
from market_pulse.llm.plan_matcher import decide_match
from market_pulse.schemas.competitor import PlanEnrichment
from market_pulse.schemas.matching import MatchDecision
from market_pulse.schemas.narrative import GapNarrative
from market_pulse.schemas.omantel import OmantelSemanticEnrichment


class CachedAnswer(BaseModel):
    value: str


class InMemoryRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str, ex: int | None = None):
        self.values[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    def delete(self, key: str) -> int:
        existed = key in self.values
        self.values.pop(key, None)
        self.ttls.pop(key, None)
        return int(existed)


class BrokenRedis:
    def get(self, key: str):
        raise RedisConnectionError("redis unavailable")

    def set(self, key: str, value: str, ex: int | None = None):
        raise RedisConnectionError("redis unavailable")


@pytest.fixture(autouse=True)
def _reset_stats():
    reset_llm_cache_stats()
    yield
    reset_llm_cache_stats()


def cache_settings(**overrides) -> Settings:
    values = {
        "llm_cache_enabled": True,
        "llm_cache_fail_open": True,
        "llm_cache_ttl_jitter_percent": 0,
        "llm_cache_competitor_ttl_seconds": 1234,
        "llm_cache_omantel_ttl_seconds": 2345,
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def test_exact_miss_is_stored_and_second_call_is_a_hit():
    redis = InMemoryRedis()
    cache = LLMResponseCache(cache_settings(), client=redis)
    invoke = MagicMock(return_value=CachedAnswer(value="first response"))
    request = {"plan_json": '{"name":"A"}'}

    first = invoke_structured_cached(
        stage=COMPETITOR_CLASSIFICATION,
        request=request,
        output_model=CachedAnswer,
        prompt_version="prompt-v1",
        invoke=invoke,
        cache=cache,
    )
    second = invoke_structured_cached(
        stage=COMPETITOR_CLASSIFICATION,
        request=request,
        output_model=CachedAnswer,
        prompt_version="prompt-v1",
        invoke=invoke,
        cache=cache,
    )

    assert first == second == CachedAnswer(value="first response")
    invoke.assert_called_once_with()
    assert list(redis.ttls.values()) == [1234]
    assert get_llm_cache_stats()[COMPETITOR_CLASSIFICATION] == {
        "miss": 1,
        "store": 1,
        "hit": 1,
    }


def test_cache_key_changes_with_input_prompt_model_schema_and_key_version():
    cache = LLMResponseCache(cache_settings(), client=InMemoryRedis())
    request = {"value": "A"}

    base = cache.make_key("stage", request, CachedAnswer, "prompt-v1")
    assert cache.make_key("stage", {"value": "B"}, CachedAnswer, "prompt-v1") != base
    assert cache.make_key("stage", request, CachedAnswer, "prompt-v2") != base

    class OtherAnswer(BaseModel):
        other: int

    assert cache.make_key("stage", request, OtherAnswer, "prompt-v1") != base

    other_model = LLMResponseCache(
        cache_settings(openai_model="another-model"), client=InMemoryRedis()
    )
    assert other_model.make_key("stage", request, CachedAnswer, "prompt-v1") != base

    other_version = LLMResponseCache(
        cache_settings(llm_cache_key_version="v2"), client=InMemoryRedis()
    )
    assert other_version.make_key("stage", request, CachedAnswer, "prompt-v1") != base


def test_invalid_cached_response_is_deleted_and_regenerated():
    redis = InMemoryRedis()
    cache = LLMResponseCache(cache_settings(), client=redis)
    request = {"value": "A"}
    key = cache.make_key("stage", request, CachedAnswer, "prompt-v1")
    redis.values[key] = json.dumps({"response": {"wrong": "shape"}})
    invoke = MagicMock(return_value=CachedAnswer(value="repaired"))

    result = invoke_structured_cached(
        stage="stage",
        request=request,
        output_model=CachedAnswer,
        prompt_version="prompt-v1",
        invoke=invoke,
        cache=cache,
    )

    assert result.value == "repaired"
    invoke.assert_called_once_with()
    assert json.loads(redis.values[key])["response"] == {"value": "repaired"}
    assert get_llm_cache_stats()["stage"]["invalid"] == 1


def test_batch_invokes_provider_for_only_misses_and_preserves_order():
    redis = InMemoryRedis()
    cache = LLMResponseCache(cache_settings(), client=redis)
    first_request = {"value": "cached"}
    second_request = {"value": "fresh"}

    first_key = cache.make_key(
        OMANTEL_ENRICHMENT, first_request, CachedAnswer, "prompt-v1"
    )
    cache.store(OMANTEL_ENRICHMENT, first_key, CachedAnswer(value="cached answer"))
    invoke_batch = MagicMock(return_value=[CachedAnswer(value="fresh answer")])

    results = invoke_structured_batch_cached(
        stage=OMANTEL_ENRICHMENT,
        requests=[first_request, second_request],
        output_model=CachedAnswer,
        prompt_version="prompt-v1",
        invoke_batch=invoke_batch,
        cache=cache,
    )

    invoke_batch.assert_called_once_with([second_request])
    assert [result.value for result in results] == ["cached answer", "fresh answer"]


def test_redis_failure_fails_open_to_llm():
    cache = LLMResponseCache(cache_settings(), client=BrokenRedis())
    invoke = MagicMock(return_value=CachedAnswer(value="from llm"))

    result = invoke_structured_cached(
        stage="stage",
        request={"value": "A"},
        output_model=CachedAnswer,
        prompt_version="prompt-v1",
        invoke=invoke,
        cache=cache,
    )

    assert result.value == "from llm"
    invoke.assert_called_once_with()
    assert get_llm_cache_stats()["stage"]["error"] == 2  # read + attempted write


def test_redis_failure_can_be_configured_to_fail_closed():
    cache = LLMResponseCache(
        cache_settings(llm_cache_fail_open=False), client=BrokenRedis()
    )

    with pytest.raises(RedisConnectionError):
        invoke_structured_cached(
            stage="stage",
            request={"value": "A"},
            output_model=CachedAnswer,
            prompt_version="prompt-v1",
            invoke=lambda: CachedAnswer(value="not reached"),
            cache=cache,
        )


def test_settings_reject_non_positive_ttl():
    with pytest.raises(ValidationError):
        cache_settings(llm_cache_omantel_ttl_seconds=0)


def _plan_enrichment() -> PlanEnrichment:
    return PlanEnrichment(
        plan_role="MASTER",
        product_type="DATA",
        market_segment="CONSUMER",
        primary_value_driver="DATA",
        promo_status="STANDARD",
        benefit_tags=["DATA"],
        classification_confidence=0.9,
        rationale="Data plan.",
    )


def test_competitor_classifier_uses_the_shared_cache():
    cache = LLMResponseCache(cache_settings(), client=InMemoryRedis())
    chain = MagicMock()
    chain.invoke.return_value = _plan_enrichment()
    plan = {"plan_name": "Same Plan", "data_gb": 5}

    first = enrich_one_plan(plan, chain=chain, cache=cache)
    second = enrich_one_plan(plan, chain=chain, cache=cache)

    assert first == second
    chain.invoke.assert_called_once()


def test_omantel_batch_uses_the_shared_cache():
    cache = LLMResponseCache(cache_settings(), client=InMemoryRedis())
    enrichment = OmantelSemanticEnrichment(
        semantic_product_type="DATA",
        market_segment="CONSUMER",
        primary_value_driver="DATA",
        benefit_tags=["DATA"],
        classification_confidence=0.9,
        rationale="Data plan.",
    )
    chain = MagicMock()
    chain.batch.return_value = [enrichment]
    plans = [{"plan_name": "Same Omantel Plan", "data_gb": 5}]

    first, first_errors = classify_omantel_plans(plans, chain=chain, cache=cache)
    second, second_errors = classify_omantel_plans(plans, chain=chain, cache=cache)

    assert first == second
    assert first_errors == second_errors == []
    chain.batch.assert_called_once()


def test_plan_matcher_uses_the_shared_cache():
    cache = LLMResponseCache(cache_settings(), client=InMemoryRedis())
    decision = MatchDecision(
        selected_plan_id="o1",
        match_status="MATCHED",
        match_confidence=0.9,
        reason="Closest candidate.",
    )
    chain = MagicMock()
    chain.invoke.return_value = decision
    competitor = {"plan_id": "c1"}
    candidates = [{"omantel_plan_id": "o1", "similarity_score": 0.9}]

    first = decide_match(competitor, candidates, chain=chain, cache=cache)
    second = decide_match(competitor, candidates, chain=chain, cache=cache)

    assert first == second
    chain.invoke.assert_called_once()


def test_narrative_generator_uses_the_shared_cache():
    cache = LLMResponseCache(cache_settings(), client=InMemoryRedis())
    narrative = GapNarrative(
        gap_summary="Summary",
        key_issue="DATA",
        business_explanation="Explanation",
    )
    chain = MagicMock()
    chain.invoke.return_value = narrative
    step4 = {
        "competitor_plan": "Competitor A",
        "omantel_plan": "Omantel A",
        "metric_gaps": {},
        "weighted_position": {},
    }
    step5 = {"risk_score": 10, "risk_level": "LOW"}

    first = generate_narrative(step4, step5, chain=chain, cache=cache)
    second = generate_narrative(step4, step5, chain=chain, cache=cache)

    assert first == second
    assert first[1] == "LLM_GENERATED"
    chain.invoke.assert_called_once()
