"""Batched LLM recommendations for the report's executive decision view.

One invocation covers one risky ``category + product_type`` segment and may
return recommendations for several Omantel plans. All numeric comparison and
risk facts and the executive rationale are calculated upstream; this module
only asks the model to select and explain an action for consideration.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from market_pulse.config.settings import Settings, get_settings
from market_pulse.llm.cache import (
    LLMResponseCache,
    PORTFOLIO_ANALYSIS,
    invoke_structured_cached,
)
from market_pulse.schemas.portfolio import PortfolioSegmentAdvice

_CACHE_PROMPT_VERSION = "portfolio-advice-v3"


portfolio_advice_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
You are the executive decision-support layer for Omantel's Market Pulse report.

You receive already-calculated comparisons between multiple competitor packs
and the Omantel plans they matched. One request covers one category and product
type and can contain several Omantel plans.

Return one recommendation for every omantel_plan_id in the input, exactly once.

STRICT RULES:
1. Use only the supplied facts. Never invent plans, competitors, features,
   customer behavior, internal constraints, financial outcomes, or numbers.
2. Do not recalculate or change similarity, gap, exposure, risk scores, or risk
   levels.
3. Treat low similarity as weak evidence. Prefer INVESTIGATE over a confident
   product change when comparability is weak or evidence is incomplete.
4. Explain material customer/revenue exposure when supplied. Do not imply that
   a positive but very small risk is urgent.
5. Preserve Omantel advantages as well as competitor advantages.
6. Suggested actions are options for business review, not guaranteed outcomes.
7. Keep suggested_action concise and business-friendly.
8. Use only these decisions: KEEP, MONITOR, ENHANCE, REPRICE, REPACKAGE,
   INVESTIGATE.
9. Do not mention a competitor or plan name unless it appears in the facts for
   that omantel_plan_id.
10. Do not predict or claim market-share loss, churn, retention, uptake,
    migration, revenue change, or customer behavior. Those outcomes are not in
    the supplied facts.
11. If a plan name appears to contain a quantity that conflicts with a
    structured metric_gaps value, trust metric_gaps and do not extract the
    quantity from the plan name.
12. Use a number only with its exact supplied field meaning. In particular,
    never describe one exposure score as another type of exposure.
13. Mention a percentage only when it is the exact supplied gap_pct, or an
    exact similarity_score converted to a percentage.
""",
        ),
        (
            "human",
            """
RISKY SEGMENT FACTS:

{facts}
""",
        ),
    ]
)


def get_llm_client(settings: Optional[Settings] = None) -> ChatOpenAI:
    """Construct the configured OpenAI-compatible client for report advice."""

    settings = settings or get_settings()
    return ChatOpenAI(
        model=settings.openai_model,
        temperature=settings.openai_temperature,
        max_retries=settings.openai_max_retries,
        api_key=settings.openai_api_key or None,
        base_url=settings.openai_base_url,
    )


def get_portfolio_advice_chain(llm: Optional[ChatOpenAI] = None):
    """Build the prompt and structured-output chain."""

    llm = llm or get_llm_client()
    structured_llm = llm.with_structured_output(
        PortfolioSegmentAdvice,
        method="json_schema",
    )
    return portfolio_advice_prompt | structured_llm


def generate_segment_advice(
    segment_facts: dict[str, Any],
    *,
    chain=None,
    cache: LLMResponseCache | None = None,
) -> PortfolioSegmentAdvice:
    """Generate one cached response for an entire risky segment."""

    request = {
        "facts": json.dumps(
            segment_facts,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    }
    return invoke_structured_cached(
        stage=PORTFOLIO_ANALYSIS,
        request=request,
        output_model=PortfolioSegmentAdvice,
        prompt_version=_CACHE_PROMPT_VERSION,
        invoke=lambda config=None: (
            (chain or get_portfolio_advice_chain()).invoke(request, config=config)
            if config is not None
            else (chain or get_portfolio_advice_chain()).invoke(request)
        ),
        cache=cache,
    )
