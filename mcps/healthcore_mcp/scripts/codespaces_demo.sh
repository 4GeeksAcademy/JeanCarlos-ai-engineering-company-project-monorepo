#!/usr/bin/env bash
# One command to try the HealthCore MCP server from MCP Playground in GitHub Codespaces.
#
#   bash mcps/healthcore_mcp/scripts/codespaces_demo.sh up      # start everything, print URL + token
#   bash mcps/healthcore_mcp/scripts/codespaces_demo.sh token   # a fresh token (they last 15 min)
#   bash mcps/healthcore_mcp/scripts/codespaces_demo.sh down    # stop everything
#
# What `up` starts (all inside the Codespace; state and logs in .mcp-demo/, gitignored):
#   1. Postgres (docker) for the Incidents Manager and the inventory, seeded with the repo data:
#      94 tickets (data/raw/incidents-healthcore.csv) and 10 products (services/api/seed_inventory.py).
#   2. The HealthCore API (uvicorn, port API_PORT) with a demo user and the MCP service account.
#   3. Keycloak (docker compose service `keycloak`, port KEYCLOAK_PORT) with the realm healthcore.
#   4. The MCP server (port MCP_PORT), announcing its public Codespaces URL, and makes that port public.
# Secrets are generated at random on first run (mcps/healthcore_mcp/.env, .mcp-demo/), never committed.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
STATE="$ROOT/.mcp-demo"
API_PORT="${API_PORT:-8000}"
MCP_PORT="${MCP_PORT:-8765}"
export KEYCLOAK_PORT="${KEYCLOAK_PORT:-8081}"
PG_PORT="${PG_PORT:-55440}"
PG_CONTAINER="${PG_CONTAINER:-healthcore-mcp-demo-pg}"
mkdir -p "$STATE"

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

public_mcp_url() {
  if [[ -n "${CODESPACE_NAME:-}" ]]; then
    echo "https://${CODESPACE_NAME}-${MCP_PORT}.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}/mcp"
  else
    echo "http://localhost:${MCP_PORT}/mcp"
  fi
}

wait_for() {  # url, name, seconds
  for _ in $(seq 1 "$3"); do curl -sf "$1" >/dev/null 2>&1 && return 0; sleep 1; done
  echo "$2 did not start; see $STATE/*.log" >&2; exit 1
}

ensure_uv() {
  if ! command -v uv >/dev/null 2>&1; then
    log "Installing uv"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
  fi
}

demo_env() {  # random API secrets for this demo, created once
  if [[ ! -f "$STATE/demo.env" ]]; then
    umask 077
    {
      echo "PG_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
      echo "JWT_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
      echo "DEMO_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')Aa1!"
    } > "$STATE/demo.env"
  fi
  set -a; source "$STATE/demo.env"; source mcps/healthcore_mcp/.env; set +a
  export DATABASE_URL="postgresql://postgres:${PG_PASSWORD}@127.0.0.1:${PG_PORT}/healthcore"
  export SUPPLIERS_DB_PATH="$STATE/users-and-suppliers.json"
  export JWT_ALGORITHM="${JWT_ALGORITHM:-HS256}" JWT_ACCESS_TOKEN_EXPIRE_MINUTES="${JWT_ACCESS_TOKEN_EXPIRE_MINUTES:-60}"
}

up() {
  ensure_uv
  log "Python dependencies (uv sync)"
  uv sync --quiet
  uv run python mcps/healthcore_mcp/scripts/init_dev_secrets.py
  demo_env

  log "Postgres (incidents + inventory)"
  if ! docker ps --format '{{.Names}}' | grep -qx "$PG_CONTAINER"; then
    docker rm -f "$PG_CONTAINER" >/dev/null 2>&1 || true
    docker run -d --name "$PG_CONTAINER" -e POSTGRES_PASSWORD="$PG_PASSWORD" -e POSTGRES_DB=healthcore \
      -p "127.0.0.1:${PG_PORT}:5432" postgres:16-alpine >/dev/null
  fi
  for _ in $(seq 1 30); do docker exec "$PG_CONTAINER" pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done

  log "Users: demo admin (seeds) + MCP service account (role user)"
  uv run python - <<'PY'
import os
from uuid import uuid4
from tinydb import Query
from services.api.core.security import hash_password
from services.api.database import open_database
from services.api.models.auth import Role, User

db = open_database()
try:
    for username, password, role in (("demo-admin", os.environ["DEMO_PASSWORD"], Role.admin),
                                     (os.environ["MCP_API_USERNAME"], os.environ["MCP_API_PASSWORD"], Role.user)):
        users = db.table("users")
        if users.get(Query().username_key == username.lower()):
            continue
        user = User(id=str(uuid4()), username=username, email=None, role=role, hashed_password=hash_password(password))
        data = user.model_dump(mode="json")
        data["username_key"], data["email_key"] = username.lower(), None
        users.insert(data)
    print(db.table("users").get(Query().username_key == "demo-admin")["id"], file=open(os.environ["SUPPLIERS_DB_PATH"] + ".admin-id", "w"))
finally:
    db.close()
PY

  log "Seed data: 10 inventory products and the historical tickets"
  uv run python -m services.api.seed_inventory --user-id "$(cat "$SUPPLIERS_DB_PATH.admin-id")" > "$STATE/seed.log" 2>&1
  uv run python scripts/seed_incidents.py >> "$STATE/seed.log" 2>&1
  tail -n 4 "$STATE/seed.log"

  log "HealthCore API on :$API_PORT"
  if ! curl -sf "http://127.0.0.1:${API_PORT}/docs" >/dev/null 2>&1; then
    nohup uv run uvicorn services.api.main:app --port "$API_PORT" > "$STATE/api.log" 2>&1 &
    echo $! > "$STATE/api.pid"
  fi
  wait_for "http://127.0.0.1:${API_PORT}/docs" "The HealthCore API" 60

  log "Keycloak on :$KEYCLOAK_PORT (realm healthcore)"
  docker compose up -d keycloak >/dev/null
  wait_for "http://localhost:${KEYCLOAK_PORT}/realms/healthcore/.well-known/openid-configuration" "Keycloak" 180

  log "HealthCore MCP server on :$MCP_PORT"
  MCP_URL="$(public_mcp_url)"
  if ! curl -sf "http://127.0.0.1:${MCP_PORT}/healthz" >/dev/null 2>&1; then
    MCP_RESOURCE_URL="$MCP_URL" MCP_PORT="$MCP_PORT" MCP_OAUTH_ISSUER="http://localhost:${KEYCLOAK_PORT}/realms/healthcore" \
      HEALTHCORE_API_URL="http://127.0.0.1:${API_PORT}" MCP_AUDIT_LOG="$STATE/tool-invocations.jsonl" \
      nohup uv run python -m mcps.healthcore_mcp > "$STATE/mcp.log" 2>&1 &
    echo $! > "$STATE/mcp.pid"
  fi
  wait_for "http://127.0.0.1:${MCP_PORT}/healthz" "The MCP server" 60

  if [[ -n "${CODESPACE_NAME:-}" ]]; then
    log "Making port $MCP_PORT public (required by MCP Playground)"
    gh codespace ports visibility "${MCP_PORT}:public" -c "$CODESPACE_NAME" \
      || echo "Could not change it automatically: in the PORTS tab, right click $MCP_PORT > Port Visibility > Public."
  fi

  summary "$MCP_URL"
}

token() {
  set -a; source mcps/healthcore_mcp/.env; set +a
  MCP_OAUTH_ISSUER="http://localhost:${KEYCLOAK_PORT}/realms/healthcore" \
    uv run python mcps/healthcore_mcp/scripts/get_token.py "${1:-healthcore-ops}"
}

summary() {
  local url="$1"
  cat <<EOF

────────────────────────────────────────────────────────────────────────────
 HealthCore MCP server is up.

 MCP Playground
   Server URL : $url
   Transport  : Streamable HTTP
   Header     : Authorization: Bearer <token below>   (valid 15 min)

 Token for client healthcore-ops (incidents:read incidents:write inventory:read):
$(token healthcore-ops)

 Other clients (least privilege):
   bash $0 token healthcore-agent             # incidents:read inventory:read
   bash $0 token healthcore-inventory-reader  # inventory:read only

 Without the header the server answers 401 with resource_metadata=…/.well-known/oauth-protected-resource/mcp
 Flows to try: get_incident 2 · list_incidents status=open · create_incident · update_incident_status
 (open→in_progress→resolved, then an invalid one) · query_inventory list/get · query_inventory operation=update
 (→ INVENTORY_READ_ONLY) · create_incident with the inventory-reader token (→ INSUFFICIENT_SCOPE)

 Audit log : $STATE/tool-invocations.jsonl
 Logs      : $STATE/api.log  $STATE/mcp.log
 Stop      : bash $0 down
────────────────────────────────────────────────────────────────────────────
EOF
}

down() {
  for name in mcp api; do
    if [[ -f "$STATE/$name.pid" ]]; then kill "$(cat "$STATE/$name.pid")" 2>/dev/null || true; rm -f "$STATE/$name.pid"; fi
  done
  docker compose stop keycloak >/dev/null 2>&1 || true
  docker rm -f "$PG_CONTAINER" >/dev/null 2>&1 || true
  echo "Stopped. Data and secrets remain in $STATE and mcps/healthcore_mcp/.env (delete them to start from scratch)."
}

case "${1:-up}" in
  up) up ;;
  token) token "${2:-healthcore-ops}" ;;
  down) down ;;
  *) echo "usage: $0 [up|token <client>|down]" >&2; exit 2 ;;
esac
