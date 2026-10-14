"""Shared ``total_data`` formula used by both Omantel and competitor normalization."""

from __future__ import annotations

from typing import Any, Optional


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return number


def compute_total_data(
    data_gb: Any, social_pass_gb: Any, entertainment_gb: Any
) -> Optional[float]:
    """``data_gb + social_pass_gb + entertainment_gb``.

    Returns None when ``data_gb`` is unknown. Missing social/entertainment
    values count as 0 in the sum. Unlimited data is handled at gap time via
    the unlimited flag, not here.
    """

    data = _num(data_gb)
    if data is None:
        return None
    return data + (_num(social_pass_gb) or 0.0) + (_num(entertainment_gb) or 0.0)
