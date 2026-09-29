"""Offline checks for raw plan-classification responses and JSON extraction."""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock

import httpx
import pytest
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from market_pulse.config.settings import Settings
from market_pulse.llm.plan_classifier import (
    enrich_one_plan,
    extract_json_object,
    get_classification_chain,
)
from market_pulse.schemas.competitor import PlanEnrichment


def valid_response() -> dict:
    return {
        "plan_role": "MASTER",
        "product_type": "DATA",
        "market_segment": "CONSUMER",
        "primary_value_driver": "DATA",
        "promo_status": "STANDARD",
        "benefit_tags": ["data"],
        "classification_confidence": 0.9,
        "rationale": "Contains {braces} in a quoted string.",
    }


def uncached_call() -> MagicMock:
    cache = MagicMock()
    cache.lookup.return_value = (None, None)
    cache.settings = Settings(langfuse_enabled=False, _env_file=None)
    return cache


@pytest.mark.parametrize(
    "wrap",
    [
        lambda text: text,
        lambda text: f"```json\n{text}\n```",
        lambda text: f"json\n{text}",
        lambda text: f"Here is the classification:\n{text}\nDone.",
    ],
)
def test_extracts_one_complete_object_with_surrounding_text(wrap):
    expected = valid_response()
    assert extract_json_object(wrap(json.dumps(expected))) == expected


@pytest.mark.parametrize(
    "content",
    ["No JSON", '{"incomplete":', '{"a": 1} {"b": 2}', '[{"a": 1}]'],
)
def test_rejects_missing_incomplete_or_ambiguous_json(content):
    with pytest.raises(ValueError, match="exactly one complete JSON object"):
        extract_json_object(content)


def test_logs_complete_raw_response_at_debug_before_validation(caplog):
    raw_content = f"```json\n{json.dumps(valid_response())}\n```"
    chain = MagicMock()
    chain.invoke.return_value = AIMessage(content=raw_content)

    with caplog.at_level(logging.DEBUG, logger="market_pulse.llm.plan_classifier"):
        enriched = enrich_one_plan(
            {"plan_name": "Hala 5"}, chain=chain, cache=uncached_call()
        )

    assert enriched["llm_enrichment"] == valid_response()
    assert raw_content in caplog.text
    assert "Hala 5" in caplog.text


def test_logs_raw_response_even_when_schema_validation_fails(caplog):
    wrong = valid_response()
    wrong["classification_confidence"] = 2
    raw_content = f"json\n{json.dumps(wrong)}\nDone."
    chain = MagicMock()
    chain.invoke.return_value = AIMessage(content=raw_content)

    with caplog.at_level(logging.DEBUG, logger="market_pulse.llm.plan_classifier"):
        with pytest.raises(ValidationError, match="classification_confidence"):
            enrich_one_plan({"plan_name": "Hala 5"}, chain=chain, cache=uncached_call())

    assert raw_content in caplog.text


def test_openai_compatible_client_returns_raw_message_before_parsing():
    raw_content = f"json\n{json.dumps(valid_response())}\nDone."

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        response_format = payload["response_format"]
        assert response_format["type"] == "json_schema"
        assert response_format["json_schema"]["strict"] is True
        schema = response_format["json_schema"]["schema"]
        assert set(schema["required"]) == set(PlanEnrichment.model_fields)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": raw_content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    llm = ChatOpenAI(
        model="test-model",
        api_key="test-key",
        base_url="https://example.invalid/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
        max_retries=0,
    )
    chain = get_classification_chain(llm=llm)
    response = chain.invoke({"plan_json": json.dumps({"plan_name": "Hala 5"})})

    assert isinstance(response, AIMessage)
    assert response.content == raw_content
