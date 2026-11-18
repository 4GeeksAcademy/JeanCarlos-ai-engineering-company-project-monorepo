#!/bin/sh
# API container entrypoint: makes `docker compose up` work from a clean clone.
#
# 1. JWT_SECRET_KEY: if it is not provided (.env or environment), a random key is
#    generated on first start and kept in the api_data volume (/data), so sessions
#    survive restarts. No secret is ever stored in Git.
# 2. Inventory tables: created in PostgreSQL if they do not exist (idempotent).
#    A failure here is logged and the API still starts (inventory routes return 503).
set -eu

SECRET_FILE="${JWT_SECRET_FILE:-/data/jwt_secret}"

if [ -z "${JWT_SECRET_KEY:-}" ]; then
  if [ ! -s "$SECRET_FILE" ]; then
    mkdir -p "$(dirname "$SECRET_FILE")"
    umask 077
    python -c 'import secrets; print(secrets.token_urlsafe(48))' > "$SECRET_FILE"
    echo "entrypoint: generated a development JWT_SECRET_KEY in $SECRET_FILE"
  fi
  JWT_SECRET_KEY="$(cat "$SECRET_FILE")"
  export JWT_SECRET_KEY
fi

if [ -n "${DATABASE_URL:-}" ]; then
  python - <<'PY' || echo "entrypoint: inventory tables not created (database unavailable); continuing"
from sqlmodel import SQLModel

import services.api.models.inventory  # noqa: F401  (registers the inventory tables)
from services.api.database import get_inventory_engine

SQLModel.metadata.create_all(get_inventory_engine())
print("entrypoint: inventory tables ready")
PY
fi

exec "$@"
