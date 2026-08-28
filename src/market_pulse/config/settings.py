"""Environment-based configuration for Market Pulse.

No secrets are hardcoded here and nothing is prompted for interactively.
Values are sourced from environment variables (optionally via a local
`.env` file) with sensible defaults for the non-secret settings.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings.

    ``openai_api_key`` is read from the ``OPENAI_API_KEY`` environment
    variable. It intentionally has no default value baked into source
    control; if it is required (i.e. an LLM call is actually made) and
    missing, the LLM client construction will fail loudly rather than
    silently proceeding.

    The LLM client is built against an OpenAI-compatible chat completions
    API. By default (``openai_base_url`` unset/``None``) this targets
    OpenAI's own endpoint. Setting ``OPENAI_BASE_URL`` (or the
    OpenAI-SDK-conventional ``OPENAI_API_BASE``) repoints the client at any
    other OpenAI-compatible provider (e.g. Groq's OpenAI-compatible
    endpoint), without any code changes.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openai_api_key: str = ""
    openai_base_url: Optional[str] = Field(
        default=None, validation_alias=AliasChoices("OPENAI_BASE_URL", "OPENAI_API_BASE")
    )
    # Matches the model configured in reference/step1.py. This is a
    # Groq-hosted open-weight model id; point ``openai_base_url`` at an
    # OpenAI-compatible endpoint that serves it (e.g. Groq's) if not using
    # OpenAI's own endpoint.
    openai_model: str = "openai/gpt-oss-20b"
    openai_temperature: float = 0
    openai_max_retries: int = 2

    # --- Run-oriented orchestration/storage (see docs/architecture.md) -----
    # Root directory for the file-based run/competitor-run/stage-result
    # repository. Overridable via the ``RUNS_DIR`` environment variable.
    runs_dir: str = "runs"

    # Shared Omantel reference catalogue CSV locations (see
    # docs/architecture.md section 5). These real files already exist in the
    # repo; do not move them.
    omantel_prepaid_csv_path: str = "data/omantel/PREPAID_PRODUCT_CATALOG.csv"
    omantel_postpaid_csv_path: str = "data/omantel/POSTPAID_PRODUCT_CATALOG.csv"

    # Real Omantel product-performance CSV (see
    # risk_analysis_service.load_performance_records_from_csv). Loaded fresh
    # per-competitor at the risk_analysis stage; see
    # orchestration/pipeline.py's fallback-to-empty-list behavior if this
    # path is missing/unreadable at run time.
    omantel_performance_csv_path: str = "data/omantel/PRODUCT_PERFORMANCE.csv"

    # Step 4/5 business-logic formula config (see
    # market_pulse.config.formula_config and config/risk_scoring.yaml). Holds
    # the gap-analysis parity threshold/weights and the risk-scoring
    # thresholds/exposure weights that used to be hardcoded Python module
    # constants. Overridable via the ``FORMULA_CONFIG_PATH`` environment
    # variable.
    formula_config_path: str = "config/risk_scoring.yaml"

    # --- Logging (see src/market_pulse/config/logging.py) -----------------
    log_dir: str = "logs"
    log_file: str = "market_pulse.log"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance built from the environment."""

    return Settings()
