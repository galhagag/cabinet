"""Shared FastAPI dependencies: caller identity + app-state singletons."""
from __future__ import annotations

from fastapi import Header, Request

from ..agents.orchestrator import Orchestrator, RealtimeBroker
from ..services.google_oauth import GoogleOAuthService
from ..services.realtime import ConnectionManager
from ..services.skills import SkillsService


def get_current_user_email(
    x_user_email: str = Header(default="dev@thetaray.com"),
) -> str:
    """Caller identity from the ``X-User-Email`` header (dev/test only).

    Production swaps this single dependency for Microsoft Entra ID JWT
    validation (bearer token → verified email claim); everything downstream
    is unchanged.
    """
    return x_user_email


def get_orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator


def get_google_oauth(request: Request) -> GoogleOAuthService:
    return request.app.state.google_oauth


def get_skills_service(request: Request) -> SkillsService:
    return request.app.state.skills_service


def get_manager(request: Request) -> ConnectionManager:
    return request.app.state.manager


def get_broker(request: Request) -> RealtimeBroker:
    return request.app.state.broker
