"""Create the HealthCore API service account used by the MCP server.

    HEALTHCORE_ADMIN_USERNAME=... HEALTHCORE_ADMIN_PASSWORD=... \
        python mcps/healthcore_mcp/scripts/create_api_service_account.py

Reads MCP_API_USERNAME / MCP_API_PASSWORD from the environment or mcps/healthcore_mcp/.env and
creates that user with the plain `user` role (not admin) through POST /users, logged in as an
existing administrator. Idempotent: an existing username is reported, not changed.
"""

import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
API = os.environ.get("HEALTHCORE_API_URL", "http://127.0.0.1:8000").rstrip("/")


def main() -> int:
    missing = [n for n in ("HEALTHCORE_ADMIN_USERNAME", "HEALTHCORE_ADMIN_PASSWORD", "MCP_API_USERNAME",
                           "MCP_API_PASSWORD") if not os.environ.get(n)]
    if missing:
        print(f"Missing: {', '.join(missing)}", file=sys.stderr)
        return 2
    login = httpx.post(f"{API}/login", data={"username": os.environ["HEALTHCORE_ADMIN_USERNAME"],
                                              "password": os.environ["HEALTHCORE_ADMIN_PASSWORD"]}, timeout=10)
    login.raise_for_status()
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    response = httpx.post(f"{API}/users", headers=headers, timeout=10, json={
        "username": os.environ["MCP_API_USERNAME"], "password": os.environ["MCP_API_PASSWORD"], "role": "user"})
    if response.status_code == 409:
        print(f"User {os.environ['MCP_API_USERNAME']} already exists; nothing changed.")
        return 0
    response.raise_for_status()
    print(f"Created service account {os.environ['MCP_API_USERNAME']} (role user).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
