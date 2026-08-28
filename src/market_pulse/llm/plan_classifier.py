"""LLM-based plan classification for Step 1 (competitor normalization).

This is the only module in the codebase that constructs an LLM client.
Business/deterministic logic must not live here; it belongs in
``market_pulse.services.competitor_normalization_service``.

Preserves, verbatim, the reference implementation's:
- classification prompt (system + human text)
- structured output schema (``PlanEnrichment``) and invocation method
- per-plan failure isolation semantics (one failing plan must not abort
  the whole batch)

The reference implementation used Groq's ``ChatGroq`` client directly.
Production instead uses ``langchain_openai.ChatOpenAI`` against an
OpenAI-compatible chat completions API (configurable base URL), so the
same code can target OpenAI, Groq's OpenAI-compatible endpoint, or any
other compatible provider without changes to business logic, the prompt,
or the output schema.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from market_pulse.config.settings import Settings, get_settings
from market_pulse.schemas.competitor import PlanEnrichment

logger = logging.getLogger(__name__)

Plan = dict[str, Any]

# ClassificationChain.invoke({"plan_json": str}) -> PlanEnrichment
ClassificationChain = Callable[[dict[str, str]], PlanEnrichment]


classification_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
You are a telecom product classification component
inside Omantel's Market Pulse Agent.

Your task is ONLY to classify and enrich competitor plans.

IMPORTANT RULES:

1. Never modify factual numeric values.
2. Never invent benefits.
3. Use only the information in the supplied plan JSON.
4. If information is insufficient, classify it as UNKNOWN.
5. A primary PREPAID tariff/bundle is normally MASTER.
6. A primary POSTPAID subscription is normally BASE_PLAN.
7. A product purchased on top of another plan is ADDON.
8. COMBO means it meaningfully combines multiple services,
   for example data + voice.
9. DATA means the principal commercial product is data-only.
10. BUSINESS should only be used when there is evidence that
    the product targets business customers.
11. PROMO should only be used where promotional evidence exists.
12. classification_confidence must represent how certain you are.

Do not perform competitor gap analysis.
Do not calculate risk.
Do not recommend Omantel actions.
""",
        ),
        (
            "human",
            """
Classify this telecom competitor plan:

{plan_json}
""",
        ),
    ]
)


def get_llm_client(settings: Optional[Settings] = None) -> ChatOpenAI:
    """Construct the OpenAI-compatible chat client used for plan classification.

    Configuration (model name, temperature, retries, API key, base URL) is
    sourced from ``market_pulse.config.settings`` -- never hardcoded or
    prompted for interactively. Leaving ``openai_base_url`` unset targets
    OpenAI's own endpoint; setting it repoints the client at any other
    OpenAI-compatible provider (e.g. Groq's OpenAI-compatible endpoint).
    """

    settings = settings or get_settings()

    return ChatOpenAI(
        model=settings.openai_model,
        temperature=settings.openai_temperature,
        max_retries=settings.openai_max_retries,
        api_key=settings.openai_api_key or None,
        base_url=settings.openai_base_url,
    )


def get_classification_chain(llm: Optional[ChatOpenAI] = None) -> ClassificationChain:
    """Build the classification chain: prompt | structured-output LLM."""

    llm = llm or get_llm_client()

    structured_llm = llm.with_structured_output(
        PlanEnrichment, method="json_schema", strict=True
    )

    return classification_prompt | structured_llm


def enrich_one_plan(plan: Plan, chain: Optional[ClassificationChain] = None) -> Plan:
    """Classify a single normalized plan and merge the result as ``llm_enrichment``.

    Raises whatever the underlying chain raises; callers that want batch
    failure isolation should catch exceptions (see ``classify_plans``).
    """

    chain = chain or get_classification_chain()

    llm_result = chain.invoke(
        {"plan_json": json.dumps(plan, ensure_ascii=False)}
    )

    enriched = plan.copy()

    # Keep LLM-derived fields clearly separated
    enriched["llm_enrichment"] = llm_result.model_dump()

    return enriched


def classify_plans(
    plans: list[Plan], chain: Optional[ClassificationChain] = None
) -> tuple[list[Plan], list[dict[str, Any]]]:
    """Classify a batch of normalized plans.

    A single plan's classification failure is isolated: it is recorded in
    the returned ``errors`` list rather than aborting the whole batch.

    Returns:
        (enriched_plans, errors)
    """

    chain = chain or get_classification_chain()

    enriched_plans: list[Plan] = []
    errors: list[dict[str, Any]] = []

    for plan in plans:
        try:
            enriched_plans.append(enrich_one_plan(plan, chain=chain))
        except Exception as exc:  # noqa: BLE001 - intentional isolation boundary
            plan_name = plan.get("plan_name")
            logger.warning("Plan classification failed for %r: %s", plan_name, exc)
            errors.append({"plan_name": plan_name, "error": str(exc)})

    logger.info(
        "Classification complete: %d succeeded, %d failed",
        len(enriched_plans),
        len(errors),
    )

    return enriched_plans, errors
