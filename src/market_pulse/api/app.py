"""FastAPI application factory for the Market Pulse run-oriented API.

Run directly with ``uvicorn market_pulse.api.app:app``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from market_pulse.api.routes import router
from market_pulse.config.logging import setup_logging


@asynccontextmanager
async def _lifespan(app: FastAPI):
    setup_logging()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Market Pulse API", lifespan=_lifespan)
    app.include_router(router)

    return app


app = create_app()
