FROM ghcr.io/astral-sh/uv:0.9.26 AS uv
FROM python:3.12-slim

COPY --from=uv /uv /uvx /bin/
WORKDIR /app

# Build context: repository root (see docker-compose.yml), so the API can import
# the shared incident rules in packages/shared/incidents.
COPY services/api/requirements.txt ./services/api/requirements.txt
RUN uv pip install --system --no-cache -r services/api/requirements.txt
COPY packages/shared/incidents ./packages/shared/incidents
COPY scripts/seed_incidents.py ./scripts/seed_incidents.py
COPY data/raw/incidents-healthcore.csv ./data/raw/incidents-healthcore.csv
COPY services/api ./services/api

EXPOSE 8000
CMD ["uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "/app/services/api", "--reload-dir", "/app/packages/shared"]
