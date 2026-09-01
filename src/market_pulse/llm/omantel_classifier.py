"""LLM-based semantic enrichment for Step 2 (Omantel reference normalization).

This is the only module that constructs an LLM client for Omantel plan
enrichment. Business/deterministic logic must not live here; it belongs in
``market_pulse.services.omantel_normalization_service``.

This is intentionally a separate module from
``market_pulse.llm.plan_classifier`` (Step 1), even though the structure is
similar: Step 2 uses its own prompt, its own output schema
(``OmantelSemanticEnrichment``), and its own structured-output invocation
(no ``strict=True`` -- see ``get_semantic_chain`` below).

Preserves, verbatim, the reference implementation's:
- semantic enrichment prompt (system + human text)
- structured output schema (``OmantelSemanticEnrichment``) and invocation
  method (``method="json_schema"``, without ``strict=True``)
- ``max_concurrency=5`` batch semantics

The reference implementation experimented with ``ChatGroq`` and a local
Ollama-backed ``ChatOpenAI`` client. Production instead always uses
``langchain_openai.ChatOpenAI`` against a configurable OpenAI-compatible
endpoint (see ``market_pulse.config.settings``), reusing the same
convention established for Step 1's classifier.

Unlike the reference notebook's single ``semantic_chain.batch(...)`` call
(which has no per-item failure isolation and would abort the whole batch on
one failure), the production batch function isolates per-plan failures so a
single bad plan cannot blow up the entire shared Omantel reference build.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from market_pulse.config.settings import Settings, get_settings
from market_pulse.llm.cache import (
    LLMResponseCache,
    OMANTEL_ENRICHMENT,
    invoke_structured_batch_cached,
    invoke_structured_cached,
)
from market_pulse.schemas.omantel import OmantelSemanticEnrichment

logger = logging.getLogger(__name__)

Plan = dict[str, Any]

# SemanticChain.invoke({"plan_json": str}) -> OmantelSemanticEnrichment
SemanticChain = Callable[[dict[str, str]], OmantelSemanticEnrichment]
_CACHE_PROMPT_VERSION = "omantel-semantic-enrichment-v1"


semantic_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
You are a telecom product-normalization component
inside Omantel's Market Pulse Agent.

You are receiving an Omantel source-of-truth product.

Your role is ONLY semantic enrichment.

RULES:

1. Never modify numeric facts.
2. Never invent price, data, validity, minutes or SMS.
3. Never change plan_role.
4. Trust the supplied structured source fields.
5. Use product name and campaign messages only to understand semantics.
6. If evidence is insufficient, return UNKNOWN.
7. benefit_tags must only represent benefits clearly supported by the source.
8. Do not perform competitor comparison.
9. Do not calculate gap analysis.
10. Do not calculate risk.
11. Do not recommend actions.

Examples of normalized benefit tags:

DATA_ROLLOVER
SOCIAL_DATA
UNLIMITED_DATA
UNLIMITED_VOICE
IDD
ROAMING
FLEXI_MINUTES
ENTERTAINMENT
BONUS_DATA
SMS
VOICE

Do not invent a tag merely because it is common in telecom.
""",
        ),
        (
            "human",
            """
Enrich this Omantel product:

{plan_json}
""",
        ),
    ]
)


def get_llm_client(settings: Optional[Settings] = None) -> ChatOpenAI:
    """Construct the OpenAI-compatible chat client used for Omantel semantic enrichment.

    Configuration is sourced from ``market_pulse.config.settings`` -- never
    hardcoded or prompted for interactively. This reuses the same
    Settings-driven convention as Step 1's classifier rather than
    introducing a second, incompatible configuration mechanism.
    """

    settings = settings or get_settings()

    return ChatOpenAI(
        model=settings.openai_model,
        temperature=settings.openai_temperature,
        max_retries=settings.openai_max_retries,
        api_key=settings.openai_api_key or None,
        base_url=settings.openai_base_url,
    )


def get_semantic_chain(llm: Optional[ChatOpenAI] = None) -> SemanticChain:
    """Build the semantic enrichment chain: prompt | structured-output LLM.

    Note: unlike Step 1's classification chain, the reference does NOT pass
    ``strict=True`` here. Preserve that difference.
    """

    llm = llm or get_llm_client()

    structured_llm = llm.with_structured_output(
        OmantelSemanticEnrichment, method="json_schema"
    )

    return semantic_prompt | structured_llm


def enrich_omantel_plan(
    plan: Plan,
    chain: Optional[SemanticChain] = None,
    cache: LLMResponseCache | None = None,
) -> Plan:
    """Semantically enrich a single normalized Omantel plan.

    Merges the result as ``llm_enrichment`` into a copy of ``plan``. Raises
    whatever the underlying chain raises; callers that want batch failure
    isolation should use ``classify_omantel_plans``.
    """

    request = {"plan_json": json.dumps(plan, ensure_ascii=False)}
    llm_result = invoke_structured_cached(
        stage=OMANTEL_ENRICHMENT,
        request=request,
        output_model=OmantelSemanticEnrichment,
        prompt_version=_CACHE_PROMPT_VERSION,
        invoke=lambda config=None: (
            (chain or get_semantic_chain()).invoke(request, config=config)
            if config is not None
            else (chain or get_semantic_chain()).invoke(request)
        ),
        cache=cache,
    )

    enriched = plan.copy()

    # Keep LLM-derived fields clearly separated
    enriched["llm_enrichment"] = llm_result.model_dump()

    return enriched


def classify_omantel_plans(
    plans: list[Plan],
    chain: Optional[SemanticChain] = None,
    cache: LLMResponseCache | None = None,
) -> tuple[list[Plan], list[dict[str, Any]]]:
    """Semantically enrich a batch of normalized Omantel plans.

    Mirrors the reference's ``semantic_chain.batch(inputs, config={"max_concurrency": 5})``
    call, but adds per-plan failure isolation (via ``return_exceptions=True``)
    so a single plan's enrichment failure is recorded in the returned
    ``errors`` list instead of aborting the whole shared Omantel reference
    build.

    Returns:
        (enriched_plans, errors)
    """

    if not plans:
        return [], []

    chain = chain or get_semantic_chain()

    inputs = [{"plan_json": json.dumps(plan, ensure_ascii=False)} for plan in plans]

    results = invoke_structured_batch_cached(
        stage=OMANTEL_ENRICHMENT,
        requests=inputs,
        output_model=OmantelSemanticEnrichment,
        prompt_version=_CACHE_PROMPT_VERSION,
        invoke_batch=lambda requests, config=None: chain.batch(
            requests,
            config={"max_concurrency": 5, **(config or {})},
            return_exceptions=True,
        ),
        cache=cache,
    )

    enriched_plans: list[Plan] = []
    errors: list[dict[str, Any]] = []

    for plan, result in zip(plans, results):
        if isinstance(result, Exception):
            plan_name = plan.get("plan_name")
            logger.warning("Omantel plan enrichment failed for %r: %s", plan_name, result)
            errors.append({"plan_name": plan_name, "error": str(result)})
        else:
            enriched = plan.copy()
            enriched["llm_enrichment"] = result.model_dump()
            enriched_plans.append(enriched)

    logger.info(
        "Omantel semantic enrichment complete: %d succeeded, %d failed",
        len(enriched_plans),
        len(errors),
    )

    return enriched_plans, errors
