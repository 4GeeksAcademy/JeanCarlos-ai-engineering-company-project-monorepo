"""Read-only tools of the agent: live operational data, not knowledge.

Each tool has one responsibility, a typed input/output contract and an explicit timeout:

- lookup_tickets: the centralized incident manager (GET /api/incidents/{id} or
  GET /api/incidents?status=&category=&origin=&branch=). Tickets change state all the time,
  so they are read live instead of being indexed in the RAG.
- lookup_stock: the inventory manager (GET /inventory/products), current stock per product.

Both call the real HealthCore API over HTTP (AGENT_TOOLS_API_URL, default
http://127.0.0.1:8000) with only GET requests, never POST/PATCH/DELETE. The incident and
inventory APIs require a Bearer token: the tools forward the token of the coordinator who
asked (same as the backoffice → API calls), taken from a context variable set by the
endpoint. The token never enters the graph state, the checkpoints or the trace.

Failures never raise into the graph: a timeout, a connection error, a 5xx, a 404 or a
missing credential come back as a result with status unavailable / not_found, and the graph
routes to its fallback node.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import re
import time
import unicodedata
from collections.abc import Iterator
from datetime import datetime
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.shared.incidents import BRANCHES, CATEGORIES, ORIGINS, STATUSES

TOOL_TIMEOUT_SECONDS = 4.0  # per HTTP call: connect + read; the graph never hangs longer
MAX_TICKETS = 5

IncidentStatus = Literal[STATUSES]  # type: ignore[valid-type]
IncidentCategory = Literal[CATEGORIES]  # type: ignore[valid-type]
IncidentOrigin = Literal[ORIGINS]  # type: ignore[valid-type]
IncidentBranch = Literal[BRANCHES]  # type: ignore[valid-type]
LookupStatus = Literal["ok", "not_found", "unavailable"]

_credentials: contextvars.ContextVar[str | None] = contextvars.ContextVar("agent_tool_credentials", default=None)


@contextlib.contextmanager
def tool_credentials(token: str | None) -> Iterator[None]:
    """Make the caller's Bearer token available to the tools for the duration of a run."""
    reset = _credentials.set(token)
    try:
        yield
    finally:
        _credentials.reset(reset)


def api_base_url() -> str:
    return os.environ.get("AGENT_TOOLS_API_URL", "http://127.0.0.1:8000").rstrip("/")


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
    """Fields of IncidentResponse the agent may repeat (the description is left out: it can
    carry free text from patients)."""

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
    endpoint: str
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


# ---------------------------------------------------------------- HTTP (GET only)


class _ToolUnavailable(Exception):
    pass


def _get(path: str, params: dict[str, str] | None = None) -> httpx.Response:
    token = _credentials.get()
    if not token:
        raise _ToolUnavailable("no credentials for the API")
    try:
        with httpx.Client(base_url=api_base_url(), timeout=httpx.Timeout(tool_timeout())) as client:
            response = client.get(path, params=params, headers={"Authorization": f"Bearer {token}"})
    except httpx.TimeoutException as error:
        raise _ToolUnavailable(f"timeout after {tool_timeout():g} s") from error
    except httpx.HTTPError as error:
        raise _ToolUnavailable(f"{type(error).__name__}") from error
    if response.status_code in (401, 403):
        raise _ToolUnavailable(f"HTTP {response.status_code} (credentials rejected)")
    if response.status_code >= 500:
        raise _ToolUnavailable(f"HTTP {response.status_code}")
    return response


def lookup_tickets(query: TicketQuery) -> TicketLookupResult:
    """Read tickets from the incident manager. Never writes."""
    start = time.perf_counter()
    if query.ticket_id is not None:
        path, params = f"/api/incidents/{query.ticket_id}", None
    else:
        path = "/api/incidents"
        params = {key: value for key, value in query.model_dump(exclude={"ticket_id"}).items() if value}
    endpoint = f"GET {path}" + (f"?{'&'.join(f'{k}={v}' for k, v in params.items())}" if params else "")

    def result(status: LookupStatus, **fields) -> TicketLookupResult:
        return TicketLookupResult(status=status, query=query, endpoint=endpoint,
                                  duration_ms=round((time.perf_counter() - start) * 1000, 1), **fields)

    try:
        response = _get(path, params)
        if response.status_code == 404:
            return result("not_found", error="ticket not found")
        if response.status_code != 200:
            return result("unavailable", error=f"HTTP {response.status_code}")
        body = response.json()
        tickets = [Ticket.model_validate(item) for item in (body if isinstance(body, list) else [body])]
    except _ToolUnavailable as error:
        return result("unavailable", error=str(error))
    except ValueError as error:  # malformed JSON or a response outside the contract
        return result("unavailable", error=f"invalid response: {type(error).__name__}")
    if not tickets:
        return result("not_found", error="no tickets match the filters")
    return result("ok", tickets=tickets[:MAX_TICKETS], total=len(tickets))


_STOPWORDS = {"de", "del", "la", "las", "el", "los", "para", "con", "en", "un", "una", "y", "o"}


def _tokens(text: str) -> set[str]:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    words = re.findall(r"[a-z0-9]+", text)
    return {re.sub(r"(es|s)$", "", w) if len(w) > 4 else w for w in words if w not in _STOPWORDS}


def match_products(product: str, catalog: list[ProductStock]) -> list[ProductStock]:
    """Products whose SKU equals the query, or whose name contains every query word
    (accents, case and plurals ignored)."""
    wanted = product.strip().upper()
    exact = [p for p in catalog if p.sku.upper() == wanted]
    if exact:
        return exact
    query_tokens = _tokens(product)
    if not query_tokens:
        return []
    return [p for p in catalog if query_tokens <= _tokens(p.name)]


def lookup_stock(query: StockQuery) -> StockLookupResult:
    """Current stock of a product from the inventory manager. Never writes."""
    start = time.perf_counter()
    endpoint = "GET /inventory/products"

    def result(status: LookupStatus, **fields) -> StockLookupResult:
        return StockLookupResult(status=status, query=query, endpoint=endpoint,
                                 duration_ms=round((time.perf_counter() - start) * 1000, 1), **fields)

    try:
        response = _get("/inventory/products")
        if response.status_code != 200:
            return result("unavailable", error=f"HTTP {response.status_code}")
        catalog = [ProductStock.model_validate(item) for item in response.json()]
    except _ToolUnavailable as error:
        return result("unavailable", error=str(error))
    except ValueError as error:
        return result("unavailable", error=f"invalid response: {type(error).__name__}")
    products = match_products(query.product, catalog)
    if not products:
        return result("not_found", error="no product matches")
    return result("ok", products=products)
