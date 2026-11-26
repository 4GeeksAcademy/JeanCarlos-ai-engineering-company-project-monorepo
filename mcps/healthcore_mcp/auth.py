"""OAuth 2.1 for the MCP server with MCP Auth (mcpauth), in resource-server mode.

- Protected Resource Metadata (RFC 9728) at /.well-known/oauth-protected-resource/mcp tells any
  client which authorization server issues tokens for this resource and which scopes exist.
- The bearer middleware validates every request to /mcp: JWT signature against the issuer's
  JWKS, issuer, expiry and audience. Without a valid token nothing is listed or executed.
- Scopes are checked per tool (least privilege): incidents:read, incidents:write, inventory:read.
  There is no inventory write scope at all.

The authorization server is any OAuth 2.1 / OIDC provider; locally, Keycloak (realm healthcore).
"""

from __future__ import annotations

import os
from contextvars import ContextVar

from mcpauth import MCPAuth
from mcpauth.config import AuthServerConfig, AuthServerType
from mcpauth.types import AuthInfo, ResourceServerConfig, ResourceServerMetadata
from mcpauth.utils import fetch_server_config

from .errors import ErrorCode, McpToolError

SCOPE_INCIDENTS_READ = "incidents:read"
SCOPE_INCIDENTS_WRITE = "incidents:write"
SCOPE_INVENTORY_READ = "inventory:read"
SCOPES = [SCOPE_INCIDENTS_READ, SCOPE_INCIDENTS_WRITE, SCOPE_INVENTORY_READ]

AUTH_CONTEXT: ContextVar[AuthInfo | None] = ContextVar("healthcore_mcp_auth", default=None)


def issuer() -> str:
    return os.environ.get("MCP_OAUTH_ISSUER", "http://localhost:8081/realms/healthcore")


def resource_url() -> str:
    """Canonical URI of this MCP server (RFC 8707 resource indicator)."""
    return os.environ.get("MCP_RESOURCE_URL", "http://localhost:8765/mcp")


def audience() -> str:
    """The `aud` the authorization server puts in tokens meant for this server."""
    return os.environ.get("MCP_AUDIENCE", "healthcore-mcp")


def build_mcp_auth(auth_server: AuthServerConfig | None = None) -> MCPAuth:
    server = auth_server or fetch_server_config(issuer(), AuthServerType.OIDC)
    return MCPAuth(
        protected_resources=ResourceServerConfig(metadata=ResourceServerMetadata(
            resource=resource_url(),
            authorization_servers=[server],
            scopes_supported=SCOPES,
            bearer_methods_supported=["header"],
            resource_name="HealthCore MCP server (Incidents Manager + inventory)",
            resource_documentation="https://github.com/4GeeksAcademy/JeanCarlos-ai-engineering-company-project-monorepo/tree/main/mcps/healthcore_mcp",
        )),
        context_var=AUTH_CONTEXT,
    )


def current_auth() -> AuthInfo:
    info = AUTH_CONTEXT.get()
    if info is None:  # unreachable behind the middleware; defensive
        raise McpToolError(ErrorCode.INSUFFICIENT_SCOPE, "No authenticated client for this request.")
    return info


def require_scope(scope: str) -> AuthInfo:
    info = current_auth()
    if scope not in info.scopes:
        raise McpToolError(ErrorCode.INSUFFICIENT_SCOPE, f"This tool requires the scope '{scope}'.",
                           {"required_scope": scope, "granted_scopes": info.scopes, "client_id": info.client_id})
    return info
