"""Domain models for Step 1: competitor normalization and enrichment.

These mirror the shapes produced/consumed by ``reference/step1.py``.

Raw competitor crawler payloads are intentionally loosely typed
(``extra="allow"``) because the crawler output schema is not fully known
up front and the reference implementation accesses fields defensively via
``dict.get(...)``. Do not invent new required fields beyond what the
reference implementation reads.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, RootModel

# ---------------------------------------------------------------------------
# Raw crawler payload input
# ---------------------------------------------------------------------------

CompetitorCategory = Literal["prepaid", "postpaid"]


class CompetitorRootPayload(BaseModel):
    """The single root object found at ``payload[0]`` in a raw crawler file.

    Only the keys read by the reference implementation
    (``master_plans``, ``basic_plans``, ``addon_plans``) are modeled
    explicitly. Any other keys present in the crawler output are preserved
    via ``extra="allow"`` but are not otherwise used by Step 1.
    """

    model_config = ConfigDict(extra="allow")

    master_plans: list[dict[str, Any]] = Field(default_factory=list)
    basic_plans: list[dict[str, Any]] = Field(default_factory=list)
    addon_plans: list[dict[str, Any]] = Field(default_factory=list)


class CompetitorRawPayload(RootModel[list[CompetitorRootPayload]]):
    """A raw crawler payload: a list containing exactly one root object.

    This mirrors the reference implementation's ``root = payload[0]``
    access pattern for both prepaid and postpaid crawler files.
    """

    root: list[CompetitorRootPayload] = Field(min_length=1)


# ---------------------------------------------------------------------------
# Deterministic normalization output (rule-based features)
# ---------------------------------------------------------------------------

ValidityBucket = Literal["UNKNOWN", "DAILY", "WEEKLY", "MONTHLY", "LONG_TERM", "OTHER"]
PriceBand = Literal["UNKNOWN", "0_5", "5_10", "10_20", "20_30", "30_50", "50_PLUS"]
MarketSegmentRule = Literal["CONSUMER", "BUSINESS"]


class NormalizedPlan(BaseModel):
    """Contract for a plan after deterministic rule-based normalization.

    Extra fields from the original raw plan (e.g. ``plan_name``,
    ``price_omr``, ``data_gb``, ``operator``, ...) are preserved as-is
    via ``extra="allow"`` since Step 1 only *adds* derived fields on top
    of a copy of the raw plan dict; it never removes existing fields.
    """

    model_config = ConfigDict(extra="allow")

    validity_bucket: ValidityBucket
    price_band: PriceBand
    market_segment_rule: MarketSegmentRule
    has_social_data: bool
    has_bonus_data: bool
    has_roaming: bool
    has_idd: bool
    has_entertainment: bool
    data_gb_per_omr: Optional[float] = None


# ---------------------------------------------------------------------------
# LLM classification output
# ---------------------------------------------------------------------------


class PlanEnrichment(BaseModel):
    """Structured LLM classification output for a single plan.

    This mirrors ``reference/step1.py``'s ``PlanEnrichment`` model exactly
    (field names, literals, and constraints). Do not add or remove fields.
    """

    model_config = ConfigDict(extra="forbid")

    plan_role: Literal["MASTER", "BASE_PLAN", "ADDON", "UNKNOWN"] = Field(
        description=(
            "MASTER for a primary prepaid tariff/bundle, "
            "BASE_PLAN for a primary postpaid subscription, "
            "ADDON for an optional bundle requiring another plan."
        )
    )

    product_type: Literal["COMBO", "DATA", "VOICE", "IDD", "ROAMING", "SMS", "OTHER"]

    market_segment: Literal["CONSUMER", "BUSINESS", "UNKNOWN"]

    primary_value_driver: Literal[
        "DATA",
        "VOICE",
        "IDD",
        "ROAMING",
        "SOCIAL",
        "ENTERTAINMENT",
        "BALANCED",
        "OTHER",
    ]

    promo_status: Literal["STANDARD", "PROMO", "UNKNOWN"]

    benefit_tags: list[str] = Field(
        description=(
            "Short normalized benefit tags derived only "
            "from information present in the plan."
        )
    )

    classification_confidence: float = Field(ge=0, le=1)

    rationale: str = Field(description="Very short reason for the classifications.")


class EnrichedPlan(NormalizedPlan):
    """A normalized plan merged with its LLM classification output.

    Mirrors ``enrich_one_plan``'s output: a copy of the normalized plan
    dict with an added ``llm_enrichment`` key.
    """

    llm_enrichment: PlanEnrichment


class ClassificationError(BaseModel):
    """A single per-plan classification failure captured during batch enrichment."""

    plan_name: Optional[str] = None
    error: str
