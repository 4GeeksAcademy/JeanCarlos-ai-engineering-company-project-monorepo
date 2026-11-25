"""Print an access token for one of the OAuth clients of the HealthCore MCP server.

    python mcps/healthcore_mcp/scripts/get_token.py healthcore-ops

Uses the client credentials grant against MCP_OAUTH_ISSUER (default the local Keycloak realm).
Paste the token as "Bearer <token>" in an MCP client such as MCP Playground. Tokens live 15
minutes. Clients: healthcore-ops (incidents:read, incidents:write, inventory:read),
healthcore-agent (incidents:read, inventory:read), healthcore-inventory-reader (inventory:read).
"""

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
SECRETS = {"healthcore-ops": "MCP_OPS_CLIENT_SECRET", "healthcore-agent": "MCP_AGENT_CLIENT_SECRET",
           "healthcore-inventory-reader": "MCP_INVENTORY_CLIENT_SECRET"}


def main() -> int:
    client_id = sys.argv[1] if len(sys.argv) > 1 else "healthcore-ops"
    if client_id not in SECRETS or not os.environ.get(SECRETS[client_id]):
        print(f"Unknown client or missing {SECRETS.get(client_id, 'secret')}; clients: {', '.join(SECRETS)}", file=sys.stderr)
        return 2
    issuer = os.environ.get("MCP_OAUTH_ISSUER", "http://localhost:8081/realms/healthcore").rstrip("/")
    response = httpx.post(f"{issuer}/protocol/openid-connect/token", timeout=10, data={
        "grant_type": "client_credentials", "client_id": client_id, "client_secret": os.environ[SECRETS[client_id]]})
    if response.status_code != 200:
        print(f"Token request failed: HTTP {response.status_code} {response.text[:200]}", file=sys.stderr)
        return 1
    print(response.json()["access_token"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
