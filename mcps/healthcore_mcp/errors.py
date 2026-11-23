"""Error codes of the HealthCore MCP server.

Two layers, each with its own codes:

- HTTP layer (MCP Auth bearer middleware, before any MCP message is processed):
  401 + WWW-Authenticate (resource_metadata=…) when the token is missing or invalid,
  403 when the token lacks a scope required for every request.
- Tool layer (inside a successful MCP request): the tool result has isError=true and its text is
  a JSON object {"code", "message", "details"} with one of the codes below.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

from fastmcp.exceptions import ToolError


class ErrorCode(str, Enum):
    INSUFFICIENT_SCOPE = "INSUFFICIENT_SCOPE"  # authorization: the token lacks the tool's scope
    VALIDATION_ERROR = "VALIDATION_ERROR"  # input outside the contract or rejected by the API (400)
    UNKNOWN_TOOL = "UNKNOWN_TOOL"  # the client called a tool this server does not have
    NOT_FOUND = "NOT_FOUND"  # the ticket does not exist (404)
    INVALID_STATUS_TRANSITION = "INVALID_STATUS_TRANSITION"  # lifecycle rule of the Incidents Manager
    INVENTORY_READ_ONLY = "INVENTORY_READ_ONLY"  # any inventory write attempt
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"  # the HealthCore API did not answer in time
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"  # the HealthCore API is down or failed (5xx)
    UPSTREAM_AUTH_FAILED = "UPSTREAM_AUTH_FAILED"  # the server's own service account was rejected


ERROR_DESCRIPTIONS = {
    ErrorCode.INSUFFICIENT_SCOPE: "The access token is valid but does not grant the scope this tool requires.",
    ErrorCode.VALIDATION_ERROR: "An argument is missing, has the wrong type or a value outside the catalog.",
    ErrorCode.UNKNOWN_TOOL: "There is no tool with that name; list_tools shows the available ones.",
    ErrorCode.NOT_FOUND: "The requested ticket does not exist.",
    ErrorCode.INVALID_STATUS_TRANSITION: "The status change is not allowed by the ticket lifecycle.",
    ErrorCode.INVENTORY_READ_ONLY: "The inventory is read-only through this server; writes are always rejected.",
    ErrorCode.UPSTREAM_TIMEOUT: "The HealthCore API did not answer within the timeout; retry later.",
    ErrorCode.UPSTREAM_UNAVAILABLE: "The HealthCore API is unavailable or returned a server error; retry later.",
    ErrorCode.UPSTREAM_AUTH_FAILED: "The MCP server could not authenticate against the HealthCore API.",
}


class McpToolError(ToolError):
    """A tool failure with a stable code. Its text is JSON so clients can branch on `code`."""

    def __init__(self, code: ErrorCode, message: str, details: dict[str, Any] | None = None):
        self.code, self.message, self.details = code, message, details or {}
        super().__init__(json.dumps({"code": code.value, "message": message, "details": self.details},
                                    ensure_ascii=False))
