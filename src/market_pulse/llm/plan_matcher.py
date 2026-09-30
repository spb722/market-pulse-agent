"""LLM-based comparable-plan matching for Step 3 (plan matching).

This is the only module that constructs an LLM client for competitor <->
Omantel plan matching. Business/deterministic logic (structured similarity
scoring, candidate filtering, capability-gap insights) must not live here;
it belongs in ``market_pulse.services.plan_matching_service``.

Preserves, verbatim, the reference implementation's:
- matching prompt (system + human text, including all 11 numbered rules)
- structured output schema (``MatchDecision``) and invocation method
  (``method="json_schema"``, without ``strict=True`` -- matches Step 2's
  pattern, differs from Step 1)

The reference implementation experimented with a local Ollama-backed
``ChatOpenAI`` client (``model="gpt-oss:20b"``, ``base_url="http://localhost:11434/v1"``).
Production instead always uses ``langchain_openai.ChatOpenAI`` against a
configurable OpenAI-compatible endpoint (see ``market_pulse.config.settings``),
reusing the same convention established for Step 1's classifier and Step 2's
semantic enrichment.
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
    PLAN_MATCHING,
    invoke_structured_cached,
)
from market_pulse.llm.structured_output import structured_output_chain
from market_pulse.schemas.matching import MatchDecision

logger = logging.getLogger(__name__)

Plan = dict[str, Any]

# MatchChain.invoke({"competitor": str, "candidates": str}) -> MatchDecision
MatchChain = Callable[[dict[str, str]], MatchDecision]
_CACHE_PROMPT_VERSION = "plan-matching-v2"


match_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
You are the comparable-plan matching component
inside Omantel's Market Pulse Agent.

You receive:

1. One competitor telecom plan.
2. Up to three Omantel candidate plans.

The candidates have already passed strict checks for:

- prepaid/postpaid
- plan role
- product type

They have also been ranked using structured
commercial similarity.

Your task is ONLY to select the most commercially
comparable Omantel candidate.

IMPORTANT RULES:

1. Select ONLY from the candidate IDs supplied.
2. Never create a new product.
3. Never combine a base plan with an add-on.
4. Ignore market segment.
5. Ignore campaign strategy.
6. Do not calculate competitive gaps.
7. Do not calculate risk.
8. Similarity does NOT mean better or worse.
9. Focus on price, data, voice/IDD and validity.
10. If all supplied candidates are clearly poor
    comparisons, return NO_GOOD_MATCH and
    selected_plan_id = null.
11. Keep the reason short.

OUTPUT FORMAT:

Return exactly one JSON object with these four fields at the top level:

- selected_plan_id: one omantel_plan_id from the candidates, or null
- match_status: MATCHED or NO_GOOD_MATCH
- match_confidence: a number from 0 to 1
- reason: one short sentence

Example:

{{"selected_plan_id": "USG_0000000", "match_status": "MATCHED", "match_confidence": 0.8, "reason": "Closest on price, data and validity."}}

Use these exact field names and no other fields.
Return the JSON object only, with no Markdown, headings, bold text,
code fence or other text.
""",
        ),
        (
            "human",
            """
COMPETITOR PLAN:

{competitor}

OMANTEL CANDIDATES:

{candidates}
""",
        ),
    ]
)


def get_llm_client(settings: Optional[Settings] = None) -> ChatOpenAI:
    """Construct the OpenAI-compatible chat client used for plan matching.

    Configuration (model name, temperature, retries, API key, base URL) is
    sourced from ``market_pulse.config.settings`` -- never hardcoded or
    prompted for interactively. This reuses the same Settings-driven
    convention as Step 1's classifier and Step 2's semantic enrichment,
    rather than the reference notebook's local Ollama experiment.
    """

    settings = settings or get_settings()

    return ChatOpenAI(
        model=settings.openai_model,
        temperature=settings.openai_temperature,
        max_retries=settings.openai_max_retries,
        api_key=settings.openai_api_key or None,
        base_url=settings.openai_base_url,
    )


def get_match_chain(llm: Optional[ChatOpenAI] = None) -> MatchChain:
    """Build the matching chain: prompt | structured-output LLM.

    Note: like Step 2's semantic enrichment chain (and unlike Step 1's
    classification chain), the reference does NOT pass ``strict=True``
    here. Preserve that difference.
    """

    llm = llm or get_llm_client()

    return structured_output_chain(match_prompt, llm, MatchDecision, "plan match decision", log=logger)


def decide_match(
    competitor_plan_compact: Plan,
    top_candidates: list[dict[str, Any]],
    chain: Optional[MatchChain] = None,
    cache: LLMResponseCache | None = None,
) -> MatchDecision:
    """Ask the LLM to select the most comparable Omantel candidate.

    ``competitor_plan_compact`` should already be the compacted competitor
    plan payload (see ``plan_matching_service.compact_plan``), and
    ``top_candidates`` the pre-filtered/scored candidate list (see
    ``plan_matching_service.find_top_candidates``). This function performs
    no filtering/scoring itself -- it is a thin invocation wrapper.
    """

    request = {
        "competitor": json.dumps(competitor_plan_compact, ensure_ascii=False),
        "candidates": json.dumps(top_candidates, ensure_ascii=False),
    }
    return invoke_structured_cached(
        stage=PLAN_MATCHING,
        request=request,
        output_model=MatchDecision,
        prompt_version=_CACHE_PROMPT_VERSION,
        invoke=lambda config=None: (
            (chain or get_match_chain()).invoke(request, config=config)
            if config is not None
            else (chain or get_match_chain()).invoke(request)
        ),
        cache=cache,
    )
