"""Structured output models for run-level executive portfolio advice."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


PortfolioDecision = Literal[
    "KEEP",
    "MONITOR",
    "ENHANCE",
    "REPRICE",
    "REPACKAGE",
    "INVESTIGATE",
]


class PortfolioRecommendation(BaseModel):
    """One evidence-grounded recommendation for an affected Omantel plan."""

    model_config = ConfigDict(extra="forbid")

    omantel_plan_id: str
    decision: PortfolioDecision
    suggested_action: str = Field(min_length=1, max_length=500)


class PortfolioSegmentAdvice(BaseModel):
    """One LLM response covering every affected plan in a risky segment."""

    model_config = ConfigDict(extra="forbid")

    segment_summary: str = Field(min_length=1, max_length=500)
    recommendations: list[PortfolioRecommendation]
