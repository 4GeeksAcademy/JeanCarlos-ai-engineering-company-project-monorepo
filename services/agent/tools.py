"""Tools of the agent: live operational data read through the HealthCore MCP server.

The agent no longer calls the Incidents Manager (or the inventory) directly. Both lookups load
their tools from the MCP server (mcps/healthcore_mcp) with langchain-mcp-adapters and call them
as an MCP client:

- lookup_tickets → MCP tools get_incident (by id) or list_incidents (filters);
- lookup_stock → MCP tool query_inventory(operation="get").

Authentication: the agent is the OAuth client `healthcore-agent` (client credentials grant
against the authorization server of the MCP server). Its tokens only carry incidents:read and
inventory:read, so even a bug in the agent cannot create or change tickets.

Each lookup has an explicit timeout (TOOL_TIMEOUT_SECONDS = 4) covering token, discovery and
call. Failures never raise into the graph: they come back as unavailable / not_found and the
graph routes to its fallback node. The typed contracts below are unchanged from Part 2.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.shared.incidents import BRANCHES, CATEGORIES, ORIGINS, STATUSES

TOOL_TIMEOUT_SECONDS = 4.0  # whole MCP lookup: OAuth token + tool discovery + call
MAX_TICKETS = 5
REPO_ROOT = Path(__file__).resolve().parents[2]

IncidentStatus = Literal[STATUSES]  # type: ignore[valid-type]
IncidentCategory = Literal[CATEGORIES]  # type: ignore[valid-type]
IncidentOrigin = Literal[ORIGINS]  # type: ignore[valid-type]
IncidentBranch = Literal[BRANCHES]  # type: ignore[valid-type]
LookupStatus = Literal["ok", "not_found", "unavailable"]


def tool_timeout() -> float:
    return float(os.environ.get("AGENT_TOOL_TIMEOUT_SECONDS", TOOL_TIMEOUT_SECONDS))


# ---------------------------------------------------------------- contracts


class TicketQuery(BaseModel):
    """Input of lookup_tickets: one ticket by id, or a filtered list (catalog values)."""

    model_config = ConfigDict(extra="forbid")

    ticket_id: int | None = Field(default=None, gt=0)
    status: IncidentStatus | None = None
    category: IncidentCategory | None = None
    origin: IncidentOrigin | None = None
    branch: IncidentBranch | None = None

    @model_validator(mode="after")
    def one_ticket_or_filters(self) -> "TicketQuery":
        if self.ticket_id is None and not any((self.status, self.category, self.origin, self.branch)):
            raise ValueError("ticket_id or at least one filter is required")
        return self


class Ticket(BaseModel):
    """Fields of the ticket the agent may repeat (the description is left out: it can carry
    free text from patients)."""

    id: int
    title: str
    status: IncidentStatus
    category: IncidentCategory
    origin: IncidentOrigin
    branch: IncidentBranch
    created_at: datetime
    updated_at: datetime


class TicketLookupResult(BaseModel):
    status: LookupStatus
    query: TicketQuery
    tickets: list[Ticket] = []
    total: int = 0  # tickets matching the filters (the list keeps the MAX_TICKETS newest)
    error: str | None = None
    endpoint: str  # the MCP tool that answered, e.g. "MCP get_incident"
    duration_ms: float


class StockQuery(BaseModel):
    """Input of lookup_stock: the product as the coordinator named it, or its SKU."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    product: str = Field(min_length=2, max_length=80)


class ProductStock(BaseModel):
    id: int
    name: str
    sku: str
    category: str
    unit: str
    country: Literal["US", "UK"]
    current_stock: int


class StockLookupResult(BaseModel):
    status: LookupStatus
    query: StockQuery
    products: list[ProductStock] = []
    error: str | None = None
    endpoint: str
    duration_ms: float


# ---------------------------------------------------------------- MCP client


class McpCallFailed(Exception):
    """The MCP lookup could not be completed; `code` is the server's error code or a transport reason."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


def _settings() -> dict[str, str]:
    load_dotenv(REPO_ROOT / "services" / "api" / ".env")
    load_dotenv(REPO_ROOT / "mcps" / "healthcore_mcp" / ".env")
    issuer = os.environ.get("MCP_OAUTH_ISSUER", "http://localhost:8081/realms/healthcore").rstrip("/")
    return {
        "server_url": os.environ.get("MCP_SERVER_URL", "http://127.0.0.1:8765/mcp"),
        "token_url": os.environ.get("MCP_OAUTH_TOKEN_URL", f"{issuer}/protocol/openid-connect/token"),
        "client_id": os.environ.get("AGENT_MCP_CLIENT_ID", "healthcore-agent"),
        "client_secret": os.environ.get("AGENT_MCP_CLIENT_SECRET") or os.environ.get("MCP_AGENT_CLIENT_SECRET", ""),
    }


class _TokenCache:
    """OAuth client credentials token of the agent, renewed 30 s before it expires."""

    def __init__(self) -> None:
        self._token: str | None = None
        self._expires = 0.0
        self._lock = threading.Lock()

    async def get(self, settings: dict[str, str]) -> str:
        with self._lock:
            if self._token and time.monotonic() < self._expires:
                return self._token
        if not settings["client_secret"]:
            raise McpCallFailed("OAUTH_NOT_CONFIGURED", "AGENT_MCP_CLIENT_SECRET is not set")
        async with httpx.AsyncClient(timeout=tool_timeout()) as client:
            response = await client.post(settings["token_url"], data={
                "grant_type": "client_credentials", "client_id": settings["client_id"],
                "client_secret": settings["client_secret"]})
        if response.status_code != 200:
            raise McpCallFailed("OAUTH_TOKEN_REJECTED", f"HTTP {response.status_code}")
        body = response.json()
        with self._lock:
            self._token = body["access_token"]
            self._expires = time.monotonic() + max(int(body.get("expires_in", 60)) - 30, 10)
            return self._token


TOKENS = _TokenCache()


async def _call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    from langchain_core.messages import ToolCall
    from langchain_mcp_adapters.client import MultiServerMCPClient

    settings = _settings()
    token = await TOKENS.get(settings)
    client = MultiServerMCPClient({"healthcore": {
        "transport": "streamable_http", "url": settings["server_url"],
        "headers": {"Authorization": f"Bearer {token}"},
        "timeout": timedelta(seconds=tool_timeout()), "sse_read_timeout": timedelta(seconds=tool_timeout()),
    }})
    tools = {tool.name: tool for tool in await client.get_tools()}
    if name not in tools:
        raise McpCallFailed("UNKNOWN_TOOL", name)
    message = await tools[name].ainvoke(ToolCall(name=name, args=arguments, id=f"agent-{name}", type="tool_call"))
    if message.status == "error":
        text = message.content[0]["text"] if isinstance(message.content, list) else str(message.content)
        try:
            error = json.loads(text)
        except ValueError:
            error = {"code": "TOOL_ERROR", "message": text[:200]}
        raise McpCallFailed(error.get("code", "TOOL_ERROR"), error.get("message", ""))
    return (message.artifact or {}).get("structured_content") or {}


def call_mcp_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call one tool of the HealthCore MCP server; raises McpCallFailed, never hangs past the timeout."""
    try:
        return asyncio.run(asyncio.wait_for(_call(name, arguments), timeout=tool_timeout()))
    except McpCallFailed:
        raise
    except (TimeoutError, asyncio.TimeoutError) as error:
        raise McpCallFailed("TIMEOUT", f"timeout after {tool_timeout():g} s") from error
    except BaseException as error:  # connection refused, TLS, protocol: ExceptionGroup from anyio
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        raise McpCallFailed("MCP_UNREACHABLE", type(error).__name__) from error


# ---------------------------------------------------------------- lookups used by the graph


def lookup_tickets(query: TicketQuery) -> TicketLookupResult:
    start = time.perf_counter()
    if query.ticket_id is not None:
        name, arguments = "get_incident", {"incident_id": query.ticket_id}
    else:
        name = "list_incidents"
        arguments = {**query.model_dump(exclude={"ticket_id"}, exclude_none=True), "limit": MAX_TICKETS}

    def result(status: LookupStatus, **fields) -> TicketLookupResult:
        return TicketLookupResult(status=status, query=query, endpoint=f"MCP {name}",
                                  duration_ms=round((time.perf_counter() - start) * 1000, 1), **fields)

    try:
        data = call_mcp_tool(name, arguments)
    except McpCallFailed as failure:
        return result("not_found" if failure.code == "NOT_FOUND" else "unavailable", error=str(failure))
    if name == "get_incident":
        return result("ok", tickets=[Ticket.model_validate(data)], total=1)
    tickets = [Ticket.model_validate(item) for item in data.get("incidents", [])]
    if not tickets:
        return result("not_found", error="no tickets match the filters")
    return result("ok", tickets=tickets, total=data.get("total", len(tickets)))


def lookup_stock(query: StockQuery) -> StockLookupResult:
    start = time.perf_counter()

    def result(status: LookupStatus, **fields) -> StockLookupResult:
        return StockLookupResult(status=status, query=query, endpoint="MCP query_inventory",
                                 duration_ms=round((time.perf_counter() - start) * 1000, 1), **fields)

    try:
        data = call_mcp_tool("query_inventory", {"operation": "get", "product": query.product})
    except McpCallFailed as failure:
        return result("unavailable", error=str(failure))
    products = [ProductStock.model_validate(item) for item in data.get("products", [])]
    if not products:
        return result("not_found", error="no product matches")
    return result("ok", products=products)
