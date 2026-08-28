"""Plain logging setup for the Market Pulse API process.

Deliberately simple: a console handler + a rotating file handler sharing a
straightforward timestamp/level/logger-name/message formatter. No
contextvars or custom ``logging.Filter`` machinery -- ``orchestration.pipeline``
includes run/competitor/stage identifiers directly in its own log messages
instead (see ``pipeline.py``).
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"

_configured = False


def setup_logging(level: str | None = None) -> None:
    """Configure the root logger once (idempotent).

    Reads ``log_dir``/``log_file``/``log_level`` from ``Settings`` unless
    ``level`` is explicitly passed.
    """

    global _configured

    if _configured or logging.getLogger().handlers:
        _configured = True
        return

    from market_pulse.config.settings import get_settings

    settings = get_settings()

    resolved_level = (level or settings.log_level or "INFO").upper()

    log_dir = Path(settings.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(_LOG_FORMAT)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        log_dir / settings.log_file, maxBytes=5_000_000, backupCount=3
    )
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(resolved_level)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    _configured = True
