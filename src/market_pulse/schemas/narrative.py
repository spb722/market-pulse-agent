"""Domain models for Step 6: LLM-generated business narratives.

These mirror the shapes produced/consumed by ``reference/step6.py``'s
*business-logic* subset only -- the notebook's matplotlib/Excel/CSV/
IPython-display scaffolding has no production equivalent and is not modeled
here (see ``market_pulse.services.narrative_service`` module docstring).

Step 6 augments Step 4 (gap analysis) + Step 5 (risk scoring) output with a
short LLM-written (or deterministic-fallback) explanation. The report record
below intentionally mirrors the reference's ``build_report_record`` column
naming (Title Case, matching the reference's ``report_df`` columns) via
pydantic aliases, since that is the field-naming the reference's business
logic actually produces -- while the narrative fields added by
``generate_narrative_report`` use the ``GapNarrative``-matching snake_case
names.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

NarrativeSource = Literal["LLM_GENERATED", "DETERMINISTIC_FALLBACK"]


# ---------------------------------------------------------------------------
# LLM structured output
# ---------------------------------------------------------------------------


class GapNarrative(BaseModel):
    """Structured LLM output explaining one competitor/Omantel gap.

    Mirrors ``reference/step6.py``'s ``GapNarrative`` model exactly (field
    names and ``extra="forbid"``). Do not add or remove fields.
    """

    model_config = ConfigDict(extra="forbid")

    gap_summary: str
    key_issue: str
    business_explanation: str


# ---------------------------------------------------------------------------
# Report records
# ---------------------------------------------------------------------------


class NarrativeReportRecord(BaseModel):
    """One row of the merged Step 4/5 gap-analysis + risk + narrative report.

    Mirrors ``narrative_service.build_report_record``'s returned dict (Title
    Case keys matching the reference's ``report_df`` columns) merged with the
    narrative fields added by ``generate_narrative_report`` (snake_case,
    matching ``GapNarrative``'s field names + ``narrative_source``).

    Kept loosely typed (``extra="allow"``) since the reference's per-metric
    columns (e.g. "PRICE Competitor", "PRICE Omantel", "PRICE Gap %",
    "PRICE Position") are dynamically named from ``METRICS`` rather than
    being a fixed set of fields -- the same convention used by
    ``GapAnalysisResult``/``RiskAnalysisResult`` for other dynamically-shaped
    Step 4/5 output.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    competitor_plan_id: Optional[str] = Field(default=None, alias="Competitor Plan ID")
    competitor_plan: Optional[str] = Field(default=None, alias="Competitor Plan")
    omantel_plan_id: Optional[str] = Field(default=None, alias="Omantel Plan ID")
    omantel_plan: Optional[str] = Field(default=None, alias="Omantel Plan")
    category: Optional[str] = Field(default=None, alias="Category")
    product_type: Optional[str] = Field(default=None, alias="Product Type")
    similarity: Optional[float] = Field(default=None, alias="Similarity")
    match_confidence: Optional[float] = Field(default=None, alias="Match Confidence")
    gap_analysis_status: Optional[str] = Field(default=None, alias="Gap Analysis Status")
    commercial_position_score: Optional[float] = Field(
        default=None, alias="Commercial Position Score"
    )
    commercial_position: Optional[str] = Field(default=None, alias="Commercial Position")
    primary_attention_area: Optional[str] = Field(
        default=None, alias="Primary Attention Area"
    )
    competitor_advantages: Optional[str] = Field(
        default=None, alias="Competitor Advantages"
    )
    omantel_advantages: Optional[str] = Field(default=None, alias="Omantel Advantages")
    capability_gaps: Optional[str] = Field(default=None, alias="Capability Gaps")
    competitive_threat: Optional[float] = Field(default=None, alias="Competitive Threat")
    avg_active_users_6m: Optional[float] = Field(
        default=None, alias="Avg Active Users 6M"
    )
    avg_product_arpu_6m: Optional[float] = Field(
        default=None, alias="Avg Product ARPU 6M"
    )
    avg_monthly_revenue_6m: Optional[float] = Field(
        default=None, alias="Avg Monthly Revenue 6M"
    )
    customer_exposure: Optional[float] = Field(default=None, alias="Customer Exposure")
    revenue_exposure: Optional[float] = Field(default=None, alias="Revenue Exposure")
    business_exposure: Optional[float] = Field(default=None, alias="Business Exposure")
    risk_score: Optional[float] = Field(default=None, alias="Risk Score")
    risk_level: Optional[str] = Field(default=None, alias="Risk Level")
    risk_status: Optional[str] = Field(default=None, alias="Risk Status")
    risk_reasons: Optional[str] = Field(default=None, alias="Risk Reasons")

    # Added by generate_narrative_report; None/absent for non-eligible rows.
    gap_summary: Optional[str] = None
    key_issue: Optional[str] = None
    business_explanation: Optional[str] = None
    narrative_source: Optional[NarrativeSource] = None


class NoMatchReportRecord(BaseModel):
    """A Step 3 record with no ("good") comparable Omantel match.

    Mirrors ``narrative_service.build_no_match_report``'s per-row shape --
    the "Potential Portfolio Gaps / No Good Comparable Match" section of the
    reference.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    competitor_plan: Optional[str] = Field(default=None, alias="Competitor Plan")
    category: Optional[str] = Field(default=None, alias="Category")
    role: Optional[str] = Field(default=None, alias="Role")
    product_type: Optional[str] = Field(default=None, alias="Product Type")
    omantel_match: Optional[str] = Field(default=None, alias="Omantel Match")
    similarity: Optional[float] = Field(default=None, alias="Similarity")
    match_confidence: Optional[float] = Field(default=None, alias="Match Confidence")
    match_status: Optional[str] = Field(default=None, alias="Match Status")
    reason: Optional[str] = Field(default=None, alias="Reason")


class ExecutiveSummary(BaseModel):
    """Run-level executive summary counts.

    Mirrors ``narrative_service.build_executive_summary``'s returned dict.
    Note: the reference's "Exposure data source: MOCK" field is deliberately
    NOT modeled here -- production Step 5 does not tag a mock data source
    (the mock-performance-data generator was dropped entirely in Step 5; see
    ``docs/architecture.md`` and ``risk_analysis_service`` module docstring).
    """

    model_config = ConfigDict(extra="allow")

    competitor: str
    competitor_plans_analyzed: int
    omantel_atl_products: int
    comparable_plans_matched: int
    no_direct_omantel_match: int
    gap_analyses_completed: int
    risk_scores_completed: int
    high_risk: int
    medium_risk: int
    low_risk: int


class NarrativeReport(BaseModel):
    """Full Step 6 (``narrative_generation``) stage result.

    Mirrors ``narrative_service.generate_narrative_report``'s returned dict.
    """

    model_config = ConfigDict(extra="allow")

    records: list[NarrativeReportRecord] = Field(default_factory=list)
    no_match_report: list[NoMatchReportRecord] = Field(default_factory=list)
    executive_summary: ExecutiveSummary
