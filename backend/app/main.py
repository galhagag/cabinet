"""FastAPI application factory.

The lifespan wires every provider from configuration (mock in dev/CI, Azure
in production — see app/config.py) and parks the singletons on ``app.state``
where the routers' dependencies (app/api/deps.py) pick them up.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .agents.foundry_client import build_llm_backend
from .agents.orchestrator import Orchestrator, seed_global_config
from .api import admin, gdrive, messages, rooms, skills, ws
from .config import get_settings
from .db.base import get_sessionmaker, init_db
from .services.blob_storage import build_blob_provider
from .services.google_oauth import GoogleOAuthService
from .services.realtime import build_realtime
from .services.secrets import build_secret_provider
from .services.skills import SkillsService


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()

    await init_db()
    async with get_sessionmaker()() as session:
        await seed_global_config(session)

    secret_provider = build_secret_provider(settings)
    blob_provider = build_blob_provider(settings, secret_provider)
    manager, broker = build_realtime(settings, secret_provider)
    llm = await build_llm_backend(settings, secret_provider)

    app.state.settings = settings
    app.state.secret_provider = secret_provider
    app.state.blob_provider = blob_provider
    app.state.manager = manager
    app.state.broker = broker
    app.state.orchestrator = Orchestrator(settings, llm, broker)
    app.state.skills_service = SkillsService(blob_provider)
    app.state.google_oauth = GoogleOAuthService(settings, secret_provider)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # dev; production restricts to the frontend origin
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(admin.router)
    app.include_router(rooms.router)
    app.include_router(messages.router)
    app.include_router(gdrive.router)
    app.include_router(skills.router)
    app.include_router(ws.router)

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok", "app": get_settings().app_name}

    return app


app = create_app()
