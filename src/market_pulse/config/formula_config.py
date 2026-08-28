"""Loading/validation for the Step 4/5 business-logic formula config.

The numeric constants that drive Step 4 (gap analysis -- ``parity_threshold``,
``overall_position_threshold``, per-product-type metric ``weights``) and
Step 5 (risk scoring -- ``competitive_threat_threshold``,
``business_exposure_weights``, ``risk_level_thresholds``) used to be hardcoded
Python module constants (see ``reference/step4.py``'s ``PARITY_THRESHOLD``/
``WEIGHTS`` and ``reference/step5.py``'s inline ``0.60``/``0.40``, ``-10``,
and ``30``/``60`` literals). They now live in a single YAML file
(``config/risk_scoring.yaml`` by default) so they can be recalibrated without
a code change -- see that file's extensive comments for what each value
means and why you might change it.

This module owns:
- the Pydantic models mirroring the YAML structure exactly, with validation
  that catches hand-edited-YAML mistakes (typo'd weights, missing product
  types, unknown metric names, inverted thresholds) at load time rather than
  silently producing wrong scores.
- ``load_formula_config``: read + validate a YAML file into a
  ``FormulaConfig``.
- ``get_formula_config``/``get_gap_analysis_config``/
  ``get_risk_analysis_config``: settings-driven, cached accessors, mirroring
  ``market_pulse.config.settings.get_settings``'s caching pattern.

Every default value in the checked-in YAML file matches the original
reference implementation's hardcoded constants exactly, so resolving this
config with no overrides reproduces today's exact behavior. Only an
explicitly *overridden* config (e.g. an edited YAML file, or a config object
passed directly to a service function in tests) changes computed results.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional, Union

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

from market_pulse.config.settings import Settings, get_settings

# Valid metric names for gap-analysis weights -- must match the metric keys
# actually produced by build_metric_gaps in gap_analysis_service.py.
_VALID_METRIC_NAMES = {"price", "data", "voice", "idd", "sms", "validity"}

# Hand-edited YAML weights are not expected to be floating-point-exact; this
# tolerance is loose enough to accept e.g. 0.30 + 0.30 + 0.20 + 0.10 + 0.10
# floating-point noise, but tight enough to catch a real typo (e.g. 0.03
# instead of 0.3, which would be off by ~0.27).
_WEIGHT_SUM_TOLERANCE = 1e-3


class GapAnalysisConfig(BaseModel):
    """Step 4 (gap analysis) tunables -- mirrors ``gap_analysis:`` in YAML."""

    parity_threshold: float = Field(ge=0.0, le=1.0)
    overall_position_threshold: float = Field(gt=0.0)
    # product_type -> {metric_name: weight}
    weights: dict[str, dict[str, float]]

    @model_validator(mode="after")
    def _validate_weights(self) -> "GapAnalysisConfig":
        if "OTHER" not in self.weights:
            raise ValueError(
                "gap_analysis.weights must include an 'OTHER' entry -- it is "
                "the universal fallback used for any product type not "
                "explicitly listed."
            )

        for product_type, metric_weights in self.weights.items():
            unknown_metrics = set(metric_weights) - _VALID_METRIC_NAMES
            if unknown_metrics:
                raise ValueError(
                    f"gap_analysis.weights['{product_type}'] uses unknown "
                    f"metric name(s) {sorted(unknown_metrics)}; valid metric "
                    f"names are {sorted(_VALID_METRIC_NAMES)}."
                )

            for metric, weight in metric_weights.items():
                if weight < 0:
                    raise ValueError(
                        f"gap_analysis.weights['{product_type}']['{metric}'] "
                        f"must be >= 0, got {weight}."
                    )

            total = sum(metric_weights.values())
            if abs(total - 1.0) > _WEIGHT_SUM_TOLERANCE:
                raise ValueError(
                    f"gap_analysis.weights['{product_type}'] weights must sum "
                    f"to 1.0, got {total} ({metric_weights})."
                )

        return self


class BusinessExposureWeights(BaseModel):
    """Step 5 customer/revenue exposure blend -- must sum to 1.0."""

    customer_weight: float = Field(ge=0.0, le=1.0)
    revenue_weight: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_sum(self) -> "BusinessExposureWeights":
        total = self.customer_weight + self.revenue_weight

        if abs(total - 1.0) > _WEIGHT_SUM_TOLERANCE:
            raise ValueError(
                "risk_analysis.business_exposure_weights.customer_weight + "
                f"revenue_weight must sum to 1.0, got {total}."
            )

        return self


class RiskLevelThresholds(BaseModel):
    """Step 5 final 0-100 risk_score cutoffs for LOW/MEDIUM/HIGH."""

    medium: float = Field(ge=0.0)
    high: float = Field(ge=0.0)

    @model_validator(mode="after")
    def _validate_ordering(self) -> "RiskLevelThresholds":
        if not self.high > self.medium:
            raise ValueError(
                "risk_analysis.risk_level_thresholds.high must be strictly "
                f"greater than medium (got high={self.high}, medium={self.medium})."
            )

        return self


class RiskAnalysisConfig(BaseModel):
    """Step 5 (risk scoring) tunables -- mirrors ``risk_analysis:`` in YAML."""

    competitive_threat_threshold: float = Field(gt=0.0)
    business_exposure_weights: BusinessExposureWeights
    risk_level_thresholds: RiskLevelThresholds


class FormulaConfig(BaseModel):
    """Top-level container mirroring the whole YAML file."""

    gap_analysis: GapAnalysisConfig
    risk_analysis: RiskAnalysisConfig


def load_formula_config(path: Union[str, Path]) -> FormulaConfig:
    """Read and validate the formula config YAML file at ``path``.

    Raises:
        FileNotFoundError: if ``path`` does not exist.
        ValueError: if the file is empty, not valid YAML, or fails the
            ``FormulaConfig`` validation rules (wraps the underlying
            ``pydantic.ValidationError`` with a clearer message).
    """

    resolved = Path(path)

    if not resolved.exists():
        raise FileNotFoundError(
            f"Formula config file not found: {resolved}. Set the "
            "FORMULA_CONFIG_PATH environment variable (or "
            "Settings.formula_config_path) to a valid path, e.g. "
            "config/risk_scoring.yaml."
        )

    with resolved.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not raw:
        raise ValueError(f"Formula config file is empty or contains no data: {resolved}")

    try:
        return FormulaConfig(**raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid formula config at {resolved}: {exc}") from exc


@lru_cache
def _load_formula_config_cached(path: str) -> FormulaConfig:
    return load_formula_config(path)


def get_formula_config(settings: Optional[Settings] = None) -> FormulaConfig:
    """Return the (cached) ``FormulaConfig`` resolved from ``settings``.

    Mirrors ``market_pulse.config.settings.get_settings``'s caching pattern:
    cached per resolved path string so repeated calls (e.g. once per plan in
    a batch) don't re-read/re-validate the YAML file from disk.
    """

    settings = settings or get_settings()

    return _load_formula_config_cached(settings.formula_config_path)


def get_gap_analysis_config(settings: Optional[Settings] = None) -> GapAnalysisConfig:
    """Convenience wrapper: just the Step 4 (gap analysis) config section."""

    return get_formula_config(settings).gap_analysis


def get_risk_analysis_config(settings: Optional[Settings] = None) -> RiskAnalysisConfig:
    """Convenience wrapper: just the Step 5 (risk analysis) config section."""

    return get_formula_config(settings).risk_analysis
