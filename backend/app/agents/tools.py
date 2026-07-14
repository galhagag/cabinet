"""Built-in agent tools: a code-based registry (not uploaded content, unlike
skills) plus the two tool executors. Both are plain httpx REST calls — no
new SDK dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..db.models import GDriveConnection, Room
from ..services.google_oauth import GoogleOAuthService
from ..services.secrets import SecretProvider
from .profiles import DATA_EXPERT_KEY, FCE_KEY


class ToolExecutionError(Exception):
    """A tool call failed (network, API error, etc.) — the caller feeds this
    back to the model as an error tool_result rather than failing the turn."""


@dataclass
class ToolContext:
    session: AsyncSession
    room: Room
    settings: Settings
    secret_provider: SecretProvider
    google_oauth: GoogleOAuthService
    transport: httpx.AsyncBaseTransport | None = None


ToolExecutorFn = Callable[[dict, ToolContext], Awaitable[str]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict  # JSON Schema
    default_agents: tuple[str, ...]
    executor: ToolExecutorFn = field(compare=False)


def _escape_drive_query(value: str) -> str:
    """Escape a value for Google Drive's `q` query language (single quotes
    and backslashes are the only characters that matter inside a quoted
    string there)."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


async def drive_search(arguments: dict, ctx: ToolContext) -> str:
    query = str(arguments.get("query", ""))
    result = await ctx.session.execute(
        select(GDriveConnection).where(GDriveConnection.room_id == ctx.room.id)
    )
    conn = result.scalar_one_or_none()
    if conn is None or conn.status not in ("connected", "linked") or not conn.google_folder_id:
        return "No Google Drive is connected for this room."

    token = await ctx.google_oauth.ensure_fresh_access_token(ctx.session, conn)
    params = {
        "q": (
            f"'{conn.google_folder_id}' in parents and trashed = false "
            f"and fullText contains '{_escape_drive_query(query)}'"
        ),
        "fields": "files(id,name,mimeType,webViewLink)",
        "pageSize": "5",
    }
    async with httpx.AsyncClient(transport=ctx.transport) as client:
        try:
            response = await client.get(
                "https://www.googleapis.com/drive/v3/files",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            response.raise_for_status()
            files = response.json().get("files", [])
            if not files:
                return f"No Drive files found matching {query!r}."
            lines = [f"- {f['name']} ({f['webViewLink']})" for f in files]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ToolExecutionError(f"drive_search failed: {exc}") from exc

    return f"Found {len(files)} file(s) in this room's Drive:\n" + "\n".join(lines)


async def web_search(arguments: dict, ctx: ToolContext) -> str:
    query = str(arguments.get("query", ""))
    api_key = await ctx.secret_provider.get_secret(ctx.settings.tavily_api_key_secret)
    async with httpx.AsyncClient(transport=ctx.transport) as client:
        try:
            response = await client.post(
                "https://api.tavily.com/search",
                json={"query": query, "max_results": 5},
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            results = response.json().get("results", [])
            if not results:
                return f"No web results found for {query!r}."
            lines = [
                f"- {r['title']}: {r['url']}\n  {r.get('content', '')[:280]}"
                for r in results
            ]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise ToolExecutionError(f"web_search failed: {exc}") from exc

    return f"Found {len(results)} web result(s):\n" + "\n".join(lines)


_QUERY_PARAM = {
    "type": "object",
    "properties": {"query": {"type": "string", "description": "The search terms."}},
    "required": ["query"],
}

TOOL_REGISTRY: dict[str, ToolDefinition] = {
    "drive_search": ToolDefinition(
        name="drive_search",
        description=(
            "Search for files by name or content in this room's connected "
            "Google Drive folder."
        ),
        parameters=_QUERY_PARAM,
        default_agents=(DATA_EXPERT_KEY, FCE_KEY),
        executor=drive_search,
    ),
    "web_search": ToolDefinition(
        name="web_search",
        description="Search the public web for current information.",
        parameters=_QUERY_PARAM,
        default_agents=(DATA_EXPERT_KEY, FCE_KEY),
        executor=web_search,
    ),
}


class ToolRunner:
    """Looks up and executes a registered tool by name."""

    async def run(self, name: str, arguments: dict, ctx: ToolContext) -> str:
        tool = TOOL_REGISTRY.get(name)
        if tool is None:
            raise ToolExecutionError(f"unknown tool: {name}")
        return await tool.executor(arguments, ctx)
