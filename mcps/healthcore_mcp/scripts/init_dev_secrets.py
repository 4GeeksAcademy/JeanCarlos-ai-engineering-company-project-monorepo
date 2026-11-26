"""Generate the local secrets of the HealthCore MCP stack into mcps/healthcore_mcp/.env.

    python mcps/healthcore_mcp/scripts/init_dev_secrets.py

Creates the file only if it does not exist (it is gitignored and never committed):
- KC_BOOTSTRAP_ADMIN_USERNAME / KC_BOOTSTRAP_ADMIN_PASSWORD: admin of the local Keycloak
  (docker compose service `keycloak` reads this file as its env_file).
- MCP_AGENT_CLIENT_SECRET / MCP_OPS_CLIENT_SECRET / MCP_INVENTORY_CLIENT_SECRET: secrets of the
  OAuth clients defined in keycloak/healthcore-realm.json (Keycloak reads them on import).
- MCP_API_USERNAME / MCP_API_PASSWORD: the HealthCore API service account the MCP server uses to
  reach the Incidents Manager and the inventory (create it with create_api_service_account.py).

Values are random (secrets.token_urlsafe) and never printed.
"""

import secrets
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parents[1] / ".env"


def main() -> int:
    if TARGET.exists():
        print(f"{TARGET} already exists; nothing changed.")
        return 0
    values = {
        "KC_BOOTSTRAP_ADMIN_USERNAME": "admin",
        "KC_BOOTSTRAP_ADMIN_PASSWORD": secrets.token_urlsafe(24),
        "MCP_AGENT_CLIENT_SECRET": secrets.token_urlsafe(32),
        "MCP_OPS_CLIENT_SECRET": secrets.token_urlsafe(32),
        "MCP_INVENTORY_CLIENT_SECRET": secrets.token_urlsafe(32),
        "MCP_API_USERNAME": "mcp-service",
        "MCP_API_PASSWORD": secrets.token_urlsafe(24) + "aA1!",
    }
    TARGET.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    TARGET.chmod(0o600)
    print(f"Wrote {len(values)} values to {TARGET} (not printed).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
