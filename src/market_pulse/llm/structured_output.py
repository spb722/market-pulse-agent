"""Shared deterministic structured-output handling for LLM calls.

OpenAI-compatible proxies sometimes wrap JSON in a Markdown code fence, which
makes ``with_structured_output`` fail inside the SDK/parser before application
code can see the text. Instead, chains send the same ``response_format``
request but return the raw ``AIMessage``; the response is then logged at DEBUG,
one JSON object is extracted deterministically, and it is validated with the
target Pydantic model.
"""

from __future__ import annotations

import json
import logging
from typing import Any, TypeVar

from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_core.utils.function_calling import convert_to_openai_function
from openai.lib._parsing import type_to_response_format_param
from pydantic import BaseModel

logger = logging.getLogger(__name__)

ModelT = TypeVar("ModelT", bound=BaseModel)


def build_response_format(model: type[BaseModel], strict: bool | None = None) -> dict[str, Any]:
    """Build the OpenAI ``response_format`` dict for a Pydantic model.

    ``strict=True`` / ``False`` build the request from langchain's
    ``convert_to_openai_function`` (Step 1 uses ``strict=True``).

    ``strict=None`` reproduces what ``ChatOpenAI.with_structured_output(model,
    method="json_schema")`` sends when ``strict`` is not given. langchain then
    keeps the Pydantic class as ``response_format`` and the OpenAI SDK's
    ``chat.completions.parse`` converts it with ``type_to_response_format_param``
    (an OpenAI-strict schema with ``"strict": true``). That same SDK function is
    used here so the wire payload is unchanged.
    """

    if strict is None:
        return dict(type_to_response_format_param(model))  # type: ignore[arg-type]

    function = convert_to_openai_function(model, strict=strict)
    function["schema"] = function.pop("parameters")
    return {"type": "json_schema", "json_schema": function}


def extract_json_object(content: str) -> dict[str, Any]:
    """Extract one complete JSON object from text or a Markdown code fence.

    Text before/after the object is allowed. Extra JSON objects, incomplete
    objects, and braces outside the selected object are rejected as ambiguous.
    """

    decoder = json.JSONDecoder()
    text = content.strip()
    matches: list[dict[str, Any]] = []

    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, end = decoder.raw_decode(text, index)
        except json.JSONDecodeError:
            continue
        outside = text[:index] + text[end:]
        if isinstance(value, dict) and not any(char in outside for char in "{}[]"):
            matches.append(value)

    if len(matches) != 1:
        raise ValueError("Expected exactly one complete JSON object in LLM response")
    return matches[0]


def validate_structured_response(
    response: AIMessage | ModelT,
    model: type[ModelT],
    label: str = "LLM",
    context: str | None = None,
    log: logging.Logger | None = None,
) -> ModelT:
    """Validate a raw chat response into ``model``.

    Instances of ``model`` pass through unchanged (injected/mocked chains).
    """

    if isinstance(response, model):
        return response
    if not isinstance(response, AIMessage):
        raise TypeError(f"Expected AIMessage from {label}, got {type(response).__name__}")

    suffix = f" for {context}" if context else ""
    (log or logger).debug("Raw %s response%s:\n%s", label, suffix, response.content)
    if not isinstance(response.content, str):
        raise TypeError(f"Expected text content in {label} response")
    return model.model_validate(extract_json_object(response.content))


def structured_output_chain(
    prompt: Runnable,
    llm: Any,
    model: type[ModelT],
    label: str,
    strict: bool | None = None,
    log: logging.Logger | None = None,
) -> Runnable:
    """Build ``prompt | llm(response_format) | validate`` returning ``model``.

    The validation step is a RunnableLambda so the run config (e.g. Langfuse
    callbacks) propagates exactly as with the previous structured-output chain.
    """

    bound = llm.bind(response_format=build_response_format(model, strict))
    return (
        prompt
        | bound
        | RunnableLambda(lambda response: validate_structured_response(response, model, label, log=log))
    )
