"""Domain models for Step 5: threat / business-exposure / risk scoring.

These mirror the shapes produced/consumed by ``reference/step5.py``, with one
deliberate architectural change (see ``docs/architecture.md`` discussion and
the implementation task for Step 5): the reference notebook *synthesizes* 12
months of mock Omantel product-performance data with an ``hashlib.md5``-seeded
``random.Random`` generator because no real performance-data source existed
when the notebook was written. Production drops that mock generator entirely
and instead requires the caller to supply real performance records via
``ProductPerformanceRecord``. Everything from the reference's
``perf_df = pd.read_csv(...)`` cell onward -- coercion, the 6-month window,
the groupby aggregation, exposure scoring, and the full risk formula -- is
real business logic and is preserved exactly.

Step 5 is purely deterministic (no LLM calls), so there is no LLM
structured-output model here.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

RiskStatus = Literal[
    "SCORED",
    "NOT_ANALYZED",
    "REVIEW_REQUIRED",
    "PROCESSING_ERROR",
]

RiskLevel = Literal["LOW", "MEDIUM", "HIGH", "NOT_SCORED"]


def _coerce_optional_float(value: Any) -> Optional[float]:
    """Schema-local numeric coercion mirroring ``pd.to_numeric(errors="coerce")``.

    ``None``/unparseable/NaN all normalize to ``None``. This is a small,
    schema-local duplicate of the service's ``clean_number`` (see
    ``risk_analysis_service.py`` module docstring for why per-step/per-module
    local copies of this helper are the project's established convention).
    """

    if value is None or value == "":
        return None

    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None

    if math.isnan(parsed):
        return None

    return parsed


class ProductPerformanceRecord(BaseModel):
    """A single month's performance record for one matched Omantel product.

    This is the production replacement for the reference's mock CSV row.
    Callers supply real performance data (e.g. from a data warehouse) instead
    of the notebook's md5-seeded random generator.

    ``month`` accepts either a ``"YYYY-MM"`` string (matching the reference's
    ``pd.to_datetime(..., format="%Y-%m")``) or a ``date``/``datetime``; it is
    normalized to the first day of that calendar month for period comparison.

    ``monthly_revenue_omr`` is optional -- see
    ``risk_analysis_service.aggregate_performance_records`` for the
    dataset-level (not per-record) fallback semantics mirroring the
    reference's ``"monthly_revenue_omr" not in perf_df.columns`` check.
    """

    model_config = ConfigDict(extra="allow")

    omantel_plan_id: str
    omantel_plan: str
    month: date
    active_users: Optional[float] = None
    product_arpu: Optional[float] = None
    monthly_revenue_omr: Optional[float] = None

    @field_validator("omantel_plan_id", "omantel_plan", mode="before")
    @classmethod
    def _stringify(cls, value: Any) -> str:
        return str(value)

    @field_validator("month", mode="before")
    @classmethod
    def _parse_month(cls, value: Any) -> date:
        if isinstance(value, datetime):
            return date(value.year, value.month, 1)

        if isinstance(value, date):
            return date(value.year, value.month, 1)

        if isinstance(value, str):
            try:
                parsed = datetime.strptime(value.strip(), "%Y-%m")
            except ValueError as exc:
                raise ValueError(
                    f"month must be parseable as 'YYYY-MM' or a date, got {value!r}"
                ) from exc

            return date(parsed.year, parsed.month, 1)

        raise ValueError(
            f"month must be a 'YYYY-MM' string or a date, got {value!r}"
        )

    @field_validator("active_users", "product_arpu", "monthly_revenue_omr", mode="before")
    @classmethod
    def _coerce_numeric(cls, value: Any) -> Optional[float]:
        return _coerce_optional_float(value)


class ProductPerformanceSummary(BaseModel):
    """A single matched Omantel product's aggregated 6-month performance +
    exposure scores -- mirrors a row of the reference's ``product_summary_df``
    (post exposure-scoring) and the value-shape stored in
    ``performance_lookup``."""

    model_config = ConfigDict(extra="allow")

    omantel_plan_id: str
    omantel_plan: str
    months_used: int
    avg_active_users_6m: Optional[float] = None
    avg_product_arpu_6m: Optional[float] = None
    avg_monthly_revenue_6m: Optional[float] = None
    customer_exposure_score: float
    revenue_exposure_score: float
    business_exposure_score: float


class RiskAnalysisResult(BaseModel):
    """Full Step 5 output for a single Step 4 matched-pair record.

    Mirrors ``analyze_step5_record``'s returned dict across all outcome
    branches (``SCORED``, ``NOT_ANALYZED``, ``REVIEW_REQUIRED``,
    ``PROCESSING_ERROR``). Fields only populated in the full ``SCORED`` path
    are optional; the early-exit branches only populate a small subset.
    """

    model_config = ConfigDict(extra="allow")

    competitor_plan_id: Optional[str] = None
    competitor_plan: Optional[str] = None
    omantel_plan_id: Optional[str] = None
    omantel_plan: Optional[str] = None
    product_type: Optional[str] = None
    similarity_score: Optional[float] = None
    match_confidence: Optional[float] = None
    step4_gap_analysis_status: Optional[str] = None

    risk_status: RiskStatus
    reason: Optional[str] = None
    error: Optional[str] = None

    latest_month_used: Optional[str] = None
    months_used: Optional[int] = None
    avg_active_users_6m: Optional[float] = None
    avg_product_arpu_6m: Optional[float] = None
    avg_monthly_revenue_6m: Optional[float] = None
    step4_commercial_position_score: Optional[float] = None
    competitive_threat_score: Optional[float] = None
    customer_exposure_score: Optional[float] = None
    revenue_exposure_score: Optional[float] = None
    business_exposure_score: Optional[float] = None
    risk_score: Optional[float] = None
    risk_level: Optional[RiskLevel] = None
    risk_reasons: list[str] = Field(default_factory=list)
