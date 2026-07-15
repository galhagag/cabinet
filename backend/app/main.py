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
from .services.entra_auth import EntraTokenValidator
from .services.google_oauth import GoogleOAuthService
from .services.ratelimit import build_rate_limiter
from .services.realtime import build_realtime
from .services.secrets import build_secret_provider
from .services.skills import SkillsService


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.validate_for_environment()

    await init_db()
    async with get_sessionmaker()() as session:
        await seed_global_config(session)

    secret_provider = build_secret_provider(settings)
    blob_provider = build_blob_provider(settings, secret_provider)
    manager, broker = build_realtime(settings, secret_provider)
    rate_limiter = build_rate_limiter(settings)
    llm = await build_llm_backend(settings, secret_provider)

    app.state.settings = settings
    app.state.secret_provider = secret_provider
    app.state.blob_provider = blob_provider
    app.state.manager = manager
    app.state.broker = broker
    app.state.rate_limiter = rate_limiter
    app.state.orchestrator = Orchestrator(settings, llm, broker)
    app.state.skills_service = SkillsService(
        blob_provider,
        md_max_bytes=settings.skill_md_max_bytes,
        zip_max_bytes=settings.skill_zip_max_bytes,
        zip_total_uncompressed_max_bytes=settings.skill_zip_total_uncompressed_max_bytes,
    )
    app.state.google_oauth = GoogleOAuthService(settings, secret_provider)
    app.state.entra_validator = (
        EntraTokenValidator(settings) if settings.auth_mode == "entra" else None
    )
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)

    origins = settings.cors_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        # Wildcard origins + credentials is a combination browsers reject —
        # and, worse, Starlette's CORSMiddleware treats *any* "*" entry as
        # allow-all and will reflect back an arbitrary request Origin with
        # Access-Control-Allow-Credentials: true. Guard on membership, not
        # exact-list equality, so "*" mixed with real origins still disables
        # credentials.
        allow_credentials=settings.auth_mode == "entra" and "*" not in origins,
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
