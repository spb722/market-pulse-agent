"""Domain models for Step 2: Omantel reference catalogue normalization and enrichment.

These mirror the shapes produced/consumed by ``reference/step2.py``.

Unlike Step 1 (competitor normalization), Step 2 builds a brand new,
fully-explicit output dict per row (see ``normalize_omantel_row`` in
``market_pulse.services.omantel_normalization_service``) rather than
copying-and-augmenting an arbitrary raw payload. The normalized-plan field
set is therefore fixed and known, so ``NormalizedOmantelPlan`` uses
``extra="forbid"``. Raw source fields that are merely passed through
untouched (``source_product_type``, ``message_english``, ``message_arabic``,
...) are still loosely typed because the reference reads them defensively
via ``row.get(...)`` from CSV-sourced data of unknown/uncontrolled shape.

This is a deliberately separate module from ``market_pulse.schemas.competitor``:
Step 2 has its own schema (``OmantelSemanticEnrichment``), which differs from
Step 1's ``PlanEnrichment`` (no ``plan_role``, no ``promo_status``). Do not
merge/reuse Step 1's schemas here.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Deterministic normalization output (rule-based features)
# ---------------------------------------------------------------------------

OmantelCategory = Literal["prepaid", "postpaid"]

PlanRole = Literal["MASTER", "BASE_PLAN", "ADDON", "UNKNOWN"]

ProductType = Literal["COMBO", "DATA", "VOICE", "IDD", "ROAMING", "SMS", "OTHER"]

ValidityBucket = Literal[
    "UNKNOWN",
    "POSTPAID_RECURRING",
    "UNSPECIFIED",
    "DAILY",
    "WEEKLY",
    "MONTHLY",
    "LONG_TERM",
    "OTHER",
]

PriceBand = Literal["UNKNOWN", "0_5", "5_10", "10_20", "20_30", "30_50", "50_PLUS"]


class NormalizedOmantelPlan(BaseModel):
    """Contract for a single Omantel plan after deterministic normalization.

    Mirrors the exact key set assembled by ``normalize_omantel_row`` in
    ``reference/step2.py``. This is a freshly-built dict (not a copy of an
    arbitrary raw row), so the field set is fixed and ``extra="forbid"``
    is used to catch accidental field drift.
    """

    model_config = ConfigDict(extra="forbid")

    operator: Literal["omantel"] = "omantel"
    category: OmantelCategory

    plan_name: Optional[str] = None
    plan_id: str

    plan_role: PlanRole
    product_type: ProductType
    source_product_type: Optional[str] = None

    price_omr: Optional[float] = None
    validity_days: Optional[float] = None

    validity_bucket: ValidityBucket
    price_band: PriceBand

    data_gb: Optional[float] = None
    social_pass_gb: Optional[float] = None
    unlimited_data: bool

    voice_minutes: Optional[float] = None
    flexi_minutes: Optional[float] = None
    intl_minutes: Optional[float] = None
    unlimited_calls: bool

    sms_count: Optional[float] = None
    unlimited_sms: bool

    source_offer_type: Optional[str] = None
    source_status: Optional[str] = None

    # Raw source content kept for LLM/audit. Source CSV data of unknown
    # cleanliness (may be missing/NaN) -- kept loosely typed on purpose.
    message_english: Any = None
    message_arabic: Any = None

    quality_flags: list[str] = Field(default_factory=list)

    data_gb_per_omr: Optional[float] = None


# ---------------------------------------------------------------------------
# LLM semantic enrichment output
# ---------------------------------------------------------------------------


class OmantelSemanticEnrichment(BaseModel):
    """Structured LLM semantic-enrichment output for a single Omantel plan.

    This mirrors ``reference/step2.py``'s ``OmantelSemanticEnrichment`` model
    exactly (field names, literals, and constraints). It is intentionally a
    different schema from Step 1's ``PlanEnrichment`` (no ``plan_role``, no
    ``promo_status``) -- do not merge/reuse Step 1's schema here.
    """

    model_config = ConfigDict(extra="forbid")

    semantic_product_type: Literal[
        "COMBO",
        "DATA",
        "VOICE",
        "IDD",
        "ROAMING",
        "SMS",
        "OTHER",
    ]

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

    benefit_tags: list[str] = Field(
        description=(
            "Short normalized benefit labels "
            "supported by the supplied product information."
        )
    )

    classification_confidence: float = Field(ge=0, le=1)

    rationale: str = Field(description="One short explanation.")


class EnrichedOmantelPlan(NormalizedOmantelPlan):
    """A normalized Omantel plan merged with its LLM semantic enrichment.

    Mirrors ``enrich_omantel_plan``'s output: a copy of the normalized plan
    dict with an added ``llm_enrichment`` key.
    """

    llm_enrichment: OmantelSemanticEnrichment


class OmantelClassificationError(BaseModel):
    """A single per-plan semantic-enrichment failure captured during batch enrichment."""

    plan_name: Optional[str] = None
    error: str
