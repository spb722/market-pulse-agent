"""Step 2: Omantel reference catalogue normalization and enrichment.

Productionized equivalent of ``reference/step2.py``. This module contains
only pure, deterministic logic (ATL/active filtering, row normalization,
rule-based feature derivation) -- no LLM calls are made here.

This is intentionally a separate module from
``market_pulse.services.competitor_normalization_service`` even where the
logic looks superficially similar (e.g. ``validity_bucket``/``price_band``
style bucketing). The reference implementation has deliberately different
edge-case handling between Step 1 and Step 2 (see ``get_validity_bucket``
below vs Step 1's ``validity_bucket``) -- do not unify them.

The functions below intentionally mirror the reference implementation's
behavior (including boundary conditions for bucketing/banding) exactly.
Do not change formulas, thresholds, mappings, or field names.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Mapping, Optional

import pandas as pd

logger = logging.getLogger(__name__)

Row = Mapping[str, Any]

# ---------------------------------------------------------------------------
# Source-value mappings (verbatim from reference/step2.py)
# ---------------------------------------------------------------------------

PLAN_ROLE_MAP: dict[str, str] = {
    "Master": "MASTER",
    "Base_Plan": "BASE_PLAN",
    "Addon": "ADDON",
}


PRODUCT_TYPE_MAP: dict[str, str] = {
    "COMBO": "COMBO",
    "DATA": "DATA",
    "VOICE": "VOICE",
    "IDD": "IDD",
    "ROAMING": "ROAMING",
    "SMS": "SMS",
    # Do not guess what NV means.
    "NV": "OTHER",
}


# ---------------------------------------------------------------------------
# CSV loading + ATL/active filtering
# ---------------------------------------------------------------------------


def load_catalogue_csv(path: str | Path) -> pd.DataFrame:
    """Read a raw Omantel product catalogue CSV file into a DataFrame."""

    return pd.read_csv(path)


def filter_prepaid_atl(prepaid_df: pd.DataFrame) -> pd.DataFrame:
    """Filter the prepaid catalogue to active, above-the-line (ATL) offers."""

    return prepaid_df[
        (prepaid_df["offer_type"] == "ATL") & (prepaid_df["product_status"] == "active")
    ].copy()


def filter_postpaid_atl(postpaid_df: pd.DataFrame) -> pd.DataFrame:
    """Filter the postpaid catalogue to ATL / main-plan offers."""

    return postpaid_df[postpaid_df["product_flag"].isin(["ATL", "MAIN_PLAN"])].copy()


def load_omantel_catalogues_from_csv(
    prepaid_path: str | Path, postpaid_path: str | Path
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load the raw prepaid and postpaid catalogue CSVs from disk.

    Kept separate from the pure normalization logic below so tests can
    exercise normalization without requiring real CSV files on disk.
    """

    return load_catalogue_csv(prepaid_path), load_catalogue_csv(postpaid_path)


# ---------------------------------------------------------------------------
# Deterministic helpers
# ---------------------------------------------------------------------------


def clean_number(value: Any) -> Optional[float]:
    """Coerce a raw catalogue value to a float, or ``None`` if unusable."""

    if pd.isna(value):
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_validity_bucket(
    category: str, plan_role: str, days: Optional[float]
) -> str:
    """Bucket an Omantel plan's validity duration (in days) into a coarse category.

    Intentionally different from Step 1's ``validity_bucket``:
    - days == 0 is split into POSTPAID_RECURRING / UNSPECIFIED instead of
      being lumped into DAILY.
    - days == 1 is DAILY here too, but day 0 is never DAILY (unlike Step 1).
    Do not unify this with Step 1's bucketing function.
    """

    if days is None:
        return "UNKNOWN"

    # Do NOT convert postpaid base-plan validity=0 into 30 days.
    # Source doesn't explicitly tell us that.
    if category == "POSTPAID" and plan_role == "BASE_PLAN" and days == 0:
        return "POSTPAID_RECURRING"

    if days == 0:
        return "UNSPECIFIED"

    if days <= 1:
        return "DAILY"

    if days <= 7:
        return "WEEKLY"

    if 28 <= days <= 31:
        return "MONTHLY"

    if days > 31:
        return "LONG_TERM"

    return "OTHER"


def get_price_band(price: Optional[float]) -> str:
    """Bucket a price (in OMR) into a coarse commercial band."""

    if price is None:
        return "UNKNOWN"

    if price <= 5:
        return "0_5"

    if price <= 10:
        return "5_10"

    if price <= 20:
        return "10_20"

    if price <= 30:
        return "20_30"

    if price <= 50:
        return "30_50"

    return "50_PLUS"


def normalize_data(
    unit_in_mb: Any, product_name: Any = ""
) -> tuple[Optional[float], bool]:
    """Normalize a raw MB value (+ product name signal) to (data_gb, unlimited_data)."""

    mb = clean_number(unit_in_mb)

    name = str(product_name).lower()

    unlimited = "unlimited data" in name or (mb is not None and mb >= 1_000_000)

    if unlimited:
        return None, True

    if mb is None:
        return None, False

    return round(mb / 1024, 3), False


def normalize_voice(
    units_minutes: Any,
) -> tuple[Optional[float], bool, Optional[str]]:
    """Normalize a raw voice-minutes value to (voice_minutes, unlimited, warning)."""

    value = clean_number(units_minutes)

    if value is None:
        return None, False, None

    # Known unlimited-style sentinel
    if value >= 999999:
        return None, True, None

    # Suspicious encoded values.
    # Keep raw value but DON'T use it in comparison maths.
    if value >= 100000:
        return None, False, "SUSPECT_ENCODED_VOICE_MINUTES"

    return value, False, None


def normalize_sms(units_sms: Any) -> tuple[Optional[float], bool]:
    """Normalize a raw SMS-count value to (sms_count, unlimited_sms)."""

    value = clean_number(units_sms)

    if value is None:
        return None, False

    # Source uses 99999 for unlimited SMS products
    if value >= 99999:
        return None, True

    return value, False


def extract_gb_from_name(product_name: Any) -> Optional[float]:
    """Extract a GB quantity mentioned in a product name, if any."""

    if not product_name:
        return None

    match = re.search(r"(\d+(?:\.\d+)?)\s*GB", str(product_name), re.IGNORECASE)

    if match:
        return float(match.group(1))

    return None


def build_quality_flags(
    product_name: Any,
    data_gb: Optional[float],
    voice_warning: Optional[str],
    category: str,
    plan_role: str,
    validity_days: Optional[float],
) -> list[str]:
    """Derive audit quality flags for a normalized Omantel plan."""

    flags: list[str] = []

    # Check data value mentioned in product name
    name_data_gb = extract_gb_from_name(product_name)

    if (
        name_data_gb is not None
        and data_gb is not None
        and abs(name_data_gb - data_gb) > 0.01
    ):
        flags.append("NAME_DATA_MISMATCH")

    if voice_warning:
        flags.append(voice_warning)

    if category == "POSTPAID" and plan_role == "BASE_PLAN" and validity_days == 0:
        flags.append("ZERO_VALIDITY_POSTPAID_BASE_PLAN")

    return flags


def normalize_omantel_row(row: Row, category: str) -> dict[str, Any]:
    """Normalize a single raw Omantel catalogue row into the flat plan contract.

    ``row`` may be a plain ``dict`` or a ``pandas.Series`` (both support
    ``.get(...)``), matching how ``reference/step2.py`` iterates DataFrame
    rows via ``df.iterrows()``.

    ``category`` must be ``"PREPAID"`` or ``"POSTPAID"`` (uppercase, matching
    the reference call sites).
    """

    plan_role = PLAN_ROLE_MAP.get(row.get("type"), "UNKNOWN")

    source_product_type = row.get("product_type")

    product_type = PRODUCT_TYPE_MAP.get(source_product_type, "OTHER")

    price = clean_number(row.get("price"))

    validity_days = clean_number(row.get("validity_in_days"))

    # DATA
    data_gb, unlimited_data = normalize_data(
        row.get("unit_in_mb"), row.get("product_name")
    )

    # VOICE
    voice_minutes, unlimited_voice, voice_warning = normalize_voice(
        row.get("units_minutes")
    )

    # SMS
    sms_count, unlimited_sms = normalize_sms(row.get("units_sms"))

    # Prepaid-specific detailed fields
    social_data_gb = None
    flexi_minutes = None
    intl_minutes = None

    if category == "PREPAID":
        social = clean_number(row.get("data_social"))

        if social and social > 0:
            social_data_gb = social

        flexi = clean_number(row.get("min_flex"))

        if flexi and flexi > 0:
            flexi_minutes = flexi

        idd = clean_number(row.get("idd"))

        if idd and idd > 0:
            intl_minutes = idd

    quality_flags = build_quality_flags(
        product_name=row.get("product_name"),
        data_gb=data_gb,
        voice_warning=voice_warning,
        category=category,
        plan_role=plan_role,
        validity_days=validity_days,
    )

    result: dict[str, Any] = {
        # Identity
        "operator": "omantel",
        "category": category.lower(),
        "plan_name": row.get("product_name"),
        "plan_id": str(row.get("product_id")),
        # Classification
        "plan_role": plan_role,
        "product_type": product_type,
        "source_product_type": source_product_type,
        # Commercial
        "price_omr": price,
        "validity_days": validity_days,
        "validity_bucket": get_validity_bucket(category, plan_role, validity_days),
        "price_band": get_price_band(price),
        # Data
        "data_gb": data_gb,
        "social_pass_gb": social_data_gb,
        "unlimited_data": unlimited_data,
        # Voice
        "voice_minutes": voice_minutes,
        "flexi_minutes": flexi_minutes,
        "intl_minutes": intl_minutes,
        "unlimited_calls": unlimited_voice,
        # SMS
        "sms_count": sms_count,
        "unlimited_sms": unlimited_sms,
        # Source classifications
        "source_offer_type": (
            row.get("offer_type") if category == "PREPAID" else row.get("product_flag")
        ),
        "source_status": (
            row.get("product_status") if category == "PREPAID" else None
        ),
        # Keep source content for LLM/audit
        "message_english": row.get("message_english"),
        "message_arabic": row.get("message_arabic"),
        # Audit
        "quality_flags": quality_flags,
    }

    # Value metric - deterministic only
    if price is not None and price > 0 and data_gb is not None:
        result["data_gb_per_omr"] = round(data_gb / price, 3)
    else:
        result["data_gb_per_omr"] = None

    return result


def normalize_omantel_catalogues(
    prepaid_df: pd.DataFrame, postpaid_df: pd.DataFrame
) -> list[dict[str, Any]]:
    """Filter (ATL/active) and normalize both Omantel catalogues.

    Pure, deterministic. Returns a single flat list combining prepaid and
    postpaid normalized plans (each still carrying its own ``category``
    field), matching ``reference/step2.py``'s ``omantel_plans`` list.
    """

    prepaid_atl = filter_prepaid_atl(prepaid_df)
    postpaid_atl = filter_postpaid_atl(postpaid_df)

    prepaid_normalized = [
        normalize_omantel_row(row, "PREPAID") for _, row in prepaid_atl.iterrows()
    ]

    postpaid_normalized = [
        normalize_omantel_row(row, "POSTPAID") for _, row in postpaid_atl.iterrows()
    ]

    omantel_plans = prepaid_normalized + postpaid_normalized

    logger.info(
        "Normalized %d Omantel plans (prepaid=%d, postpaid=%d)",
        len(omantel_plans),
        len(prepaid_normalized),
        len(postpaid_normalized),
    )

    return omantel_plans


def run_omantel_normalization(
    prepaid_df: pd.DataFrame, postpaid_df: pd.DataFrame
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Thin Step 2 orchestration: deterministic normalization + LLM semantic enrichment.

    Composes ``normalize_omantel_catalogues`` (pure/deterministic, this
    module) with the batch LLM enrichment
    (``market_pulse.llm.omantel_classifier``). This is the only function in
    this module that transitively triggers LLM calls; it delegates entirely
    to the isolated ``llm`` module rather than constructing any LLM client
    itself.

    Returns:
        (enriched_plans, errors) -- the shared Omantel reference output,
        combining prepaid + postpaid plans, matching the reference
        notebook's ``omantel_enriched`` list plus isolated per-plan errors.
    """

    from market_pulse.llm.omantel_classifier import classify_omantel_plans

    normalized_plans = normalize_omantel_catalogues(prepaid_df, postpaid_df)

    return classify_omantel_plans(normalized_plans)
