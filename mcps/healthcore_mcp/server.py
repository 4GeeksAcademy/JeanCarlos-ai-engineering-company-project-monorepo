"""HealthCore MCP server: tickets of the Incidents Manager and read-only inventory.

Transport: Streamable HTTP (one endpoint, /mcp), stateless, because the server is consumed by
several remote clients (the LangGraph agent, other teams, partners) and OAuth bearer tokens
travel in the HTTP Authorization header. stdio would only serve one local process, with no
place for OAuth.

    python -m mcps.healthcore_mcp            # http://127.0.0.1:8765/mcp
"""

from __future__ import annotations

import os
import re
import time
import unicodedata
from datetime import datetime
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.exceptions import ValidationError as ArgumentsError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field, ValidationError
from starlette.applications import Starlette
from starlette.middleware import Middleware as AsgiMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from packages.shared.incidents import BRANCHES, CATEGORIES, ORIGINS, STATUSES, TRANSITIONS
from packages.shared.incidents.catalog import BRANCH_LABELS

from . import audit
from .api_client import API
from .auth import (AUTH_CONTEXT, SCOPE_INCIDENTS_READ, SCOPE_INCIDENTS_WRITE, SCOPE_INVENTORY_READ, SCOPES,
                   audience, build_mcp_auth, require_scope, resource_url)
from .errors import ERROR_DESCRIPTIONS, ErrorCode, McpToolError

IncidentStatus = Literal[STATUSES]  # type: ignore[valid-type]
IncidentCategory = Literal[CATEGORIES]  # type: ignore[valid-type]
IncidentOrigin = Literal[ORIGINS]  # type: ignore[valid-type]
IncidentBranch = Literal[BRANCHES]  # type: ignore[valid-type]

INVENTORY_READ_OPERATIONS = ("list", "get")
INVENTORY_WRITE_OPERATIONS = ("create", "add", "update", "set", "adjust", "increment", "decrement", "consume",
                              "receive", "delete", "remove", "patch", "put", "post")

INSTRUCTIONS = f"""HealthCore MCP server. Exposes two live HealthCore systems to authorized MCP clients:

1. Incidents Manager tickets (field names and values exactly as GET /api/incidents returns them):
   get_incident, list_incidents (scope {SCOPE_INCIDENTS_READ}); create_incident and
   update_incident_status (scope {SCOPE_INCIDENTS_WRITE}). Status changes follow the ticket lifecycle
   {dict(TRANSITIONS)}; resolved and discarded are final.
2. Inventory stock: query_inventory (scope {SCOPE_INVENTORY_READ}). READ ONLY: any write operation is
   rejected with INVENTORY_READ_ONLY. There is no inventory write scope.

Authentication: OAuth 2.1 bearer token (JWT) from the authorization server listed in the Protected
Resource Metadata (/.well-known/oauth-protected-resource/mcp), audience '{audience()}'.
Without a valid token every request gets HTTP 401. A tool whose scope is not in the token returns
INSUFFICIENT_SCOPE. Tool errors are JSON {{"code", "message", "details"}}; codes:
{", ".join(code.value for code in ErrorCode)}. The catalog of allowed values is the resource
healthcore://incidents/catalog."""

mcp = FastMCP(name="healthcore-mcp", instructions=INSTRUCTIONS, version="1.0.0", mask_error_details=True)


# ---------------------------------------------------------------- output contracts


class Incident(BaseModel):
    """A ticket of the Incidents Manager, as returned by GET /api/incidents/{id}."""

    id: int = Field(description="Ticket id (incident_id in the other tools).")
    title: str
    description: str
    category: IncidentCategory
    status: IncidentStatus
    origin: IncidentOrigin
    branch: IncidentBranch = Field(description="Clinic (branch) code; see healthcore://incidents/catalog.")
    created_at: datetime
    updated_at: datetime


class IncidentList(BaseModel):
    total: int = Field(description="Tickets that match the filters.")
    returned: int = Field(description="Tickets included in `incidents` (newest first, at most `limit`).")
    incidents: list[Incident]


class Product(BaseModel):
    """A product of the inventory, as returned by GET /inventory/products."""

    id: int
    name: str
    sku: str
    category: str
    unit: str = Field(description="box, unit, pack or vial: the unit of current_stock.")
    country: Literal["US", "UK"]
    product_category: str | None = None
    expiry_date: str | None = None
    current_stock: int = Field(description="Units in stock across the network (deliveries minus consumptions).")


class InventoryResult(BaseModel):
    operation: Literal["list", "get"]
    products: list[Product]


# ---------------------------------------------------------------- governance middleware

TOOL_SCOPES = {
    "get_incident": SCOPE_INCIDENTS_READ,
    "list_incidents": SCOPE_INCIDENTS_READ,
    "create_incident": SCOPE_INCIDENTS_WRITE,
    "update_incident_status": SCOPE_INCIDENTS_WRITE,
    "query_inventory": SCOPE_INVENTORY_READ,
}


class GovernanceMiddleware(Middleware):
    """Every tool call goes through here, valid or not:

    1. least privilege: the tool's scope must be in the access token (INSUFFICIENT_SCOPE);
    2. errors normalized to a stable JSON code (VALIDATION_ERROR, UNKNOWN_TOOL, …), never a
       stack trace or a library message;
    3. one audit line per call: tool, client, scopes, result and error code.
    """

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        start = time.perf_counter()
        name = context.message.name
        arguments = dict(context.message.arguments or {})
        info = AUTH_CONTEXT.get()
        who = {"client_id": info.client_id if info else None, "subject": info.subject if info else None,
               "scopes": info.scopes if info else []}

        def log(result: str, code: str | None) -> None:
            audit.record(name, **who, arguments=arguments, result=result, error_code=code,
                         duration_ms=(time.perf_counter() - start) * 1000)

        try:
            if name not in TOOL_SCOPES:
                raise McpToolError(ErrorCode.UNKNOWN_TOOL, f"There is no tool named '{name}'.",
                                   {"available_tools": sorted(TOOL_SCOPES)})
            require_scope(TOOL_SCOPES[name])
            result = await call_next(context)
        except McpToolError as error:
            log("error", error.code.value)
            raise
        except (ValidationError, ArgumentsError) as error:  # arguments outside the input schema
            log("error", ErrorCode.VALIDATION_ERROR.value)
            cause = error if isinstance(error, ValidationError) else error.__cause__
            errors = ([{"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]}
                       for e in cause.errors(include_url=False, include_input=False)]
                      if isinstance(cause, ValidationError) else [])
            raise McpToolError(ErrorCode.VALIDATION_ERROR, "Some arguments are missing or invalid.",
                               {"errors": errors}) from None
        except ToolError as error:
            cause = error.__cause__
            if isinstance(cause, McpToolError):
                log("error", cause.code.value)
                raise cause from None
            if isinstance(cause, ValidationError):
                log("error", ErrorCode.VALIDATION_ERROR.value)
                raise McpToolError(ErrorCode.VALIDATION_ERROR, "Some arguments are missing or invalid.",
                                   {"errors": [{"field": ".".join(str(p) for p in e["loc"]), "message": e["msg"]}
                                               for e in cause.errors(include_url=False, include_input=False)]}) from None
            log("error", ErrorCode.VALIDATION_ERROR.value)
            raise McpToolError(ErrorCode.VALIDATION_ERROR, str(error)[:300]) from None
        except Exception as error:
            log("error", ErrorCode.UPSTREAM_UNAVAILABLE.value)
            raise McpToolError(ErrorCode.UPSTREAM_UNAVAILABLE, "Unexpected error while calling HealthCore.") from error
        log("ok", None)
        return result


mcp.add_middleware(GovernanceMiddleware())


READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


# ---------------------------------------------------------------- Incidents Manager


@mcp.tool(
    name="get_incident",
    title="Get a ticket",
    description=(f"Current state of one Incidents Manager ticket (GET /api/incidents/{{id}}): status, category, "
                 f"origin, branch and dates. Requires scope {SCOPE_INCIDENTS_READ}. Errors: NOT_FOUND if the "
                 "ticket does not exist."),
    annotations=READ_ONLY,
)
def get_incident(incident_id: Annotated[int, Field(gt=0, description="Ticket id, e.g. 482.")]) -> Incident:
    return Incident.model_validate(API.get_incident(incident_id))


@mcp.tool(
    name="list_incidents",
    title="List tickets",
    description=(f"Tickets of the Incidents Manager filtered by status, category, origin and/or branch, newest "
                 f"first (GET /api/incidents). Requires scope {SCOPE_INCIDENTS_READ}. Omit a filter to not "
                 "filter by it."),
    annotations=READ_ONLY,
)
def list_incidents(
    status: Annotated[IncidentStatus | None, Field(description="open, in_progress, resolved or discarded.")] = None,
    category: IncidentCategory | None = None,
    origin: IncidentOrigin | None = None,
    branch: IncidentBranch | None = None,
    limit: Annotated[int, Field(ge=1, le=100, description="Maximum tickets returned.")] = 20,
) -> IncidentList:
    filters = {k: v for k, v in {"status": status, "category": category, "origin": origin, "branch": branch}.items() if v}
    incidents = [Incident.model_validate(item) for item in API.list_incidents(filters)]
    return IncidentList(total=len(incidents), returned=min(limit, len(incidents)), incidents=incidents[:limit])


@mcp.tool(
    name="create_incident",
    title="Create a ticket",
    description=(f"Creates a ticket in the Incidents Manager (POST /api/incidents); it starts in status 'open'. "
                 f"Requires scope {SCOPE_INCIDENTS_WRITE}. Do not include patient data (names, diagnoses, record "
                 "numbers). Errors: VALIDATION_ERROR with the invalid fields."),
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False),
)
def create_incident(
    title: Annotated[str, Field(min_length=1, max_length=120, description="Short summary, at most 120 characters.")],
    description: Annotated[str, Field(min_length=5, max_length=4000, description="What happened, 5-4000 characters.")],
    category: IncidentCategory,
    origin: Annotated[IncidentOrigin, Field(description="customer, branch or internal.")],
    branch: Annotated[IncidentBranch, Field(description="Clinic where it happened.")],
) -> Incident:
    return Incident.model_validate(API.create_incident(
        {"title": title, "description": description, "category": category, "origin": origin, "branch": branch}))


@mcp.tool(
    name="update_incident_status",
    title="Change a ticket's status",
    description=(f"Moves a ticket through its lifecycle with PATCH /api/incidents/{{id}}/status (the only way a "
                 f"ticket is updated). Allowed: open → in_progress or discarded; in_progress → resolved or "
                 f"discarded; resolved and discarded are final. Requires scope {SCOPE_INCIDENTS_WRITE}. Errors: "
                 "NOT_FOUND, INVALID_STATUS_TRANSITION."),
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False),
)
def update_incident_status(
    incident_id: Annotated[int, Field(gt=0)],
    status: Annotated[IncidentStatus, Field(description="Target status.")],
) -> Incident:
    return Incident.model_validate(API.change_incident_status(incident_id, status))


# ---------------------------------------------------------------- inventory (read only)

_STOPWORDS = {"de", "del", "la", "las", "el", "los", "para", "con", "en", "un", "una", "y", "o"}


def _tokens(text: str) -> set[str]:
    text = unicodedata.normalize("NFKD", text.lower())
    words = re.findall(r"[a-z0-9]+", "".join(ch for ch in text if not unicodedata.combining(ch)))
    return {re.sub(r"(es|s)$", "", w) if len(w) > 4 else w for w in words if w not in _STOPWORDS}


def match_products(wanted: str, products: list["Product"]) -> list["Product"]:
    """SKU equal to `wanted`, or a name containing every word of it (accents, case and plurals
    ignored): «vacunas antigripales» finds «Vacuna antigripal (dosis)»."""
    exact = [p for p in products if p.sku.upper() == wanted.strip().upper()]
    if exact:
        return exact
    words = _tokens(wanted)
    return [p for p in products if words and words <= _tokens(p.name)]



@mcp.tool(
    name="query_inventory",
    title="Query inventory stock (read only)",
    description=(f"Reads current stock from the inventory (GET /inventory/products). Requires scope "
                 f"{SCOPE_INVENTORY_READ}. operation='list' returns every product; operation='get' returns the "
                 "products whose SKU equals `sku` or whose name contains `product`. READ ONLY: operations that "
                 "would change stock (create, update, adjust, consume, delete, …) are always rejected with "
                 "INVENTORY_READ_ONLY; stock only changes through the inventory's own delivery and consumption "
                 "records."),
    annotations=READ_ONLY,
)
def query_inventory(
    operation: Annotated[str, Field(description="'list' or 'get'. Anything that writes is rejected (INVENTORY_READ_ONLY).")] = "list",
    sku: Annotated[str | None, Field(description="Exact SKU for operation='get', e.g. HCR-PPE-001.")] = None,
    product: Annotated[str | None, Field(description="Words of the product name for operation='get'.")] = None,
) -> InventoryResult:
    op = operation.strip().lower()
    if op in INVENTORY_WRITE_OPERATIONS or op not in INVENTORY_READ_OPERATIONS and any(w in op for w in INVENTORY_WRITE_OPERATIONS):
        raise McpToolError(ErrorCode.INVENTORY_READ_ONLY,
                           "The inventory is read-only through the HealthCore MCP server; no stock was changed.",
                           {"operation": operation, "allowed_operations": list(INVENTORY_READ_OPERATIONS)})
    if op not in INVENTORY_READ_OPERATIONS:
        raise McpToolError(ErrorCode.VALIDATION_ERROR, f"Unknown operation '{operation}'.",
                           {"allowed_operations": list(INVENTORY_READ_OPERATIONS)})
    products = [Product.model_validate(item) for item in API.list_products()]
    if op == "get":
        if not sku and not product:
            raise McpToolError(ErrorCode.VALIDATION_ERROR, "operation='get' needs `sku` or `product`.")
        products = match_products(sku or product or "", products)
    return InventoryResult(operation=op, products=products)


# ---------------------------------------------------------------- discovery resource


@mcp.resource("healthcore://incidents/catalog", name="incident-catalog", mime_type="application/json",
              description="Allowed values of the Incidents Manager (status, category, origin, branch) and the "
                          "status lifecycle, as enforced by the API.")
def incident_catalog() -> dict[str, Any]:
    return {"statuses": list(STATUSES), "categories": list(CATEGORIES), "origins": list(ORIGINS),
            "branches": [{"value": b, "label": BRANCH_LABELS.get(b, b)} for b in BRANCHES],
            "transitions": {k: list(v) for k, v in dict(TRANSITIONS).items()},
            "tool_scopes": TOOL_SCOPES,
            "error_codes": {code.value: text for code, text in ERROR_DESCRIPTIONS.items()}}


# ---------------------------------------------------------------- ASGI app


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def create_app(auth_server=None) -> Starlette:
    """Streamable HTTP app: public Protected Resource Metadata + /mcp behind MCP Auth."""
    mcp_auth = build_mcp_auth(auth_server)
    allowed_hosts = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
    mcp_app = mcp.http_app(
        path="/mcp", stateless_http=True, json_response=True,
        middleware=[AsgiMiddleware(mcp_auth.bearer_auth_middleware("jwt", audience=audience(), resource=resource_url()))],
        **({"allowed_hosts": allowed_hosts} if allowed_hosts else {}),
    )
    return Starlette(
        routes=[*mcp_auth.resource_metadata_router().routes, Route("/healthz", health), Mount("/", app=mcp_app)],
        lifespan=mcp_app.lifespan,
    )


__all__ = ["SCOPES", "create_app", "mcp"]
