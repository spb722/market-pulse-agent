"""Domain models for Step 4: competitive gap analysis.

These mirror the shapes produced/consumed by ``reference/step4.py``.

Step 4 is purely deterministic (no LLM calls), so there is no LLM
structured-output model here -- only the shapes of ``analyze_match``'s
output.
"""

from __future__ import annotations

from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

MetricPosition = Literal[
    "PARITY",
    "OMANTEL_ADVANTAGE",
    "COMPETITOR_ADVANTAGE",
    "NOT_SCORED",
]

GapAnalysisStatus = Literal[
    "ANALYZED",
    "NOT_ANALYZED",
    "REVIEW_REQUIRED",
    "PROCESSING_ERROR",
]

OverallPosition = Literal[
    "OMANTEL_ADVANTAGE",
    "COMPETITOR_ADVANTAGE",
    "BALANCED",
    "NOT_SCORED",
]

CapabilityGapStatus = Literal[
    "NATIVE_GAP_ADDON_EXISTS",
    "NATIVE_GAP_NO_ADDON_FOUND",
]

# A metric's competitor/omantel value is either a finite number, the literal
# string "UNLIMITED" (unlimited_metric_gap), or None (unscoreable metric).
MetricValue = Union[float, str, None]


class MetricGap(BaseModel):
    """A single per-metric competitor-vs-Omantel gap.

    Mirrors ``finite_metric_gap``/``unlimited_metric_gap``/``validity_gap``'s
    returned dict. ``note`` is only populated by ``validity_gap``'s
    recurring-postpaid-base-plan special case.
    """

    model_config = ConfigDict(extra="allow")

    competitor: MetricValue = None
    omantel: MetricValue = None
    difference: Optional[float] = None
    gap_pct: Optional[float] = None
    normalized_advantage: Optional[float] = None
    position: MetricPosition
    note: Optional[str] = None


class MetricGaps(BaseModel):
    """The full set of per-metric gaps built by ``build_metric_gaps``."""

    model_config = ConfigDict(extra="allow")

    price: MetricGap
    data: MetricGap
    voice: MetricGap
    idd: MetricGap
    sms: MetricGap
    validity: MetricGap


class WeightedPosition(BaseModel):
    """Weighted commercial position, mirroring ``compute_weighted_position``."""

    model_config = ConfigDict(extra="allow")

    product_type: str
    commercial_position_score: Optional[float] = None
    overall_position: OverallPosition
    effective_weights: dict[str, float] = Field(default_factory=dict)
    weighted_contributions: dict[str, float] = Field(default_factory=dict)


class SeparateOmantelOffer(BaseModel):
    """A standalone Omantel add-on offering a capability -- as carried through
    from Step 3's ``capability_insights`` entries."""

    model_config = ConfigDict(extra="allow")

    plan_id: Optional[str] = None
    plan_name: Optional[str] = None
    product_type: Optional[str] = None
    price_omr: Optional[float] = None


class CapabilityGap(BaseModel):
    """A single capability the competitor has that the matched Omantel plan
    lacks, mirroring ``capability_gaps_from_match``'s returned dicts."""

    model_config = ConfigDict(extra="allow")

    capability: Optional[str] = None
    position: Literal["COMPETITOR_ADVANTAGE"] = "COMPETITOR_ADVANTAGE"
    status: CapabilityGapStatus
    separate_omantel_offer_exists: bool
    separate_omantel_offers: list[Any] = Field(default_factory=list)


class GapAnalysisResult(BaseModel):
    """Full Step 4 output for a single competitor plan.

    Mirrors ``analyze_match``'s returned dict across all four outcome
    branches (``ANALYZED``, ``NOT_ANALYZED``, ``REVIEW_REQUIRED``,
    ``PROCESSING_ERROR``). Fields only populated in the full ``ANALYZED``
    path are optional; ``PROCESSING_ERROR`` results only populate a small
    subset (see ``analyze_matches``'s per-item error branch).
    """

    model_config = ConfigDict(extra="allow")

    competitor_plan_id: Optional[str] = None
    competitor_plan: Optional[str] = None
    category: Optional[str] = None
    plan_role: Optional[str] = None
    product_type: Optional[str] = None
    step3_match_status: Optional[str] = None
    match_confidence: Optional[float] = None

    gap_analysis_status: GapAnalysisStatus
    reason: Optional[str] = None
    error: Optional[str] = None

    omantel_plan_id: Optional[str] = None
    omantel_plan: Optional[str] = None
    similarity_score: Optional[float] = None

    metric_gaps: Optional[MetricGaps] = None
    weighted_position: Optional[WeightedPosition] = None
    capability_gaps: list[CapabilityGap] = Field(default_factory=list)
