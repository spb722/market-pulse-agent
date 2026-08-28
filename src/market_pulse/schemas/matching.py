"""Domain models for Step 3: competitor <-> Omantel comparable-plan matching.

These mirror the shapes produced/consumed by ``reference/step3.py``.

Both ``top_candidates`` entries and ``selected_match`` are dynamically
shaped dicts in the reference (a fixed set of descriptive keys plus
``**score`` spread from ``calculate_similarity``). Rather than fight that
dynamic shape with a rigid model, ``OmantelCandidate`` models the known
keys loosely (``extra="allow"``) -- mirroring how Step 1/2 handled
loosely-typed raw/derived fields.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# LLM structured output
# ---------------------------------------------------------------------------


class MatchDecision(BaseModel):
    """Structured LLM output for a single competitor-plan matching decision.

    Mirrors ``reference/step3.py``'s ``MatchDecision`` model exactly (field
    names, literals, and constraints). Do not add or remove fields.
    """

    model_config = ConfigDict(extra="forbid")

    selected_plan_id: Optional[str] = None

    match_status: Literal["MATCHED", "NO_GOOD_MATCH"]

    match_confidence: float = Field(ge=0, le=1)

    reason: str


# ---------------------------------------------------------------------------
# Matching output
# ---------------------------------------------------------------------------

MatchStatus = Literal[
    "MATCHED",
    "NO_GOOD_MATCH",
    "NO_DIRECT_MATCH",
    "REVIEW_REQUIRED",
    "PROCESSING_ERROR",
]


class OmantelCandidate(BaseModel):
    """A single scored Omantel candidate (top-N candidate or selected match).

    Combines ``find_top_candidates``'s descriptive fields with the
    similarity-score fields spread in from ``calculate_similarity``. Kept
    loosely typed (``extra="allow"``) since the reference builds this as a
    dynamically-composed dict.
    """

    model_config = ConfigDict(extra="allow")

    omantel_plan_id: Optional[str] = None
    omantel_plan_name: Optional[str] = None
    category: Optional[str] = None
    plan_role: Optional[str] = None
    product_type: Optional[str] = None
    price_omr: Optional[float] = None
    data_gb: Optional[float] = None
    voice_minutes: Optional[float] = None
    intl_minutes: Optional[float] = None
    validity_days: Optional[float] = None
    unlimited_data: Optional[bool] = None
    unlimited_calls: Optional[bool] = None

    similarity_score: Optional[float] = None
    price_similarity: Optional[float] = None
    data_similarity: Optional[float] = None
    voice_similarity: Optional[float] = None
    idd_similarity: Optional[float] = None
    sms_similarity: Optional[float] = None
    validity_similarity: Optional[float] = None


class SeparateOmantelOffer(BaseModel):
    """A standalone Omantel add-on offering a capability missing from a match."""

    model_config = ConfigDict(extra="allow")

    plan_id: Optional[str] = None
    plan_name: Optional[str] = None
    product_type: Optional[str] = None
    price_omr: Optional[float] = None


class CapabilityInsight(BaseModel):
    """A single capability gap between a competitor plan and its matched Omantel plan."""

    model_config = ConfigDict(extra="allow")

    capability: str
    status: Literal["MISSING_FROM_MATCHED_PLAN"]
    separate_omantel_offer_exists: bool
    separate_omantel_offers: list[SeparateOmantelOffer] = Field(default_factory=list)


class PlanMatchResult(BaseModel):
    """Full Step 3 output for a single competitor plan.

    Mirrors ``match_competitor_plan`` (+ ``attach_capability_insights``)'s
    returned dict. ``PROCESSING_ERROR`` results only populate a subset of
    fields (see ``match_competitor_plans``'s per-item error branch), so most
    fields are optional here.
    """

    model_config = ConfigDict(extra="allow")

    competitor: Optional[str] = None
    competitor_plan_id: Optional[str] = None
    competitor_plan_name: Optional[str] = None
    category: Optional[str] = None
    plan_role: Optional[str] = None
    product_type: Optional[str] = None

    top_candidates: list[OmantelCandidate] = Field(default_factory=list)

    selected_match: Optional[OmantelCandidate] = None
    match_status: MatchStatus
    match_confidence: Optional[float] = None
    selection_reason: Optional[str] = None

    capability_insights: list[CapabilityInsight] = Field(default_factory=list)

    error: Optional[str] = None
