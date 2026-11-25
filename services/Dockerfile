FROM ghcr.io/astral-sh/uv:0.9.26 AS uv
FROM python:3.12-slim

COPY --from=uv /uv /uvx /bin/
WORKDIR /app

# Build context: ./services (see docker-compose.yml); data/, packages/shared and scripts/ come
# from the named additional contexts below.
COPY api/requirements.txt ./services/api/requirements.txt
RUN uv pip install --system --no-cache -r services/api/requirements.txt
COPY api ./services/api
COPY telemetry ./services/telemetry
COPY reporting ./services/reporting
COPY agent ./services/agent
# Celery app + DLQ records: shared by the API (producer), the worker and Flower.
COPY celery_app.py task_dead_letters.py ./services/
# Business pipeline imported by services/reporting (build context "pipelines" = ./data in compose).
COPY --from=pipelines __init__.py ./data/__init__.py
COPY --from=pipelines pipelines ./data/pipelines
COPY --from=pipelines process ./data/process
COPY --from=pipelines raw/clinics.csv ./data/raw/clinics.csv
# Centralized incident manager: shared rules, seed script and its CSV (contexts in compose).
COPY --from=shared incidents ./packages/shared/incidents
COPY --from=scripts seed_incidents.py ./scripts/seed_incidents.py
COPY --from=pipelines raw/incidents-healthcore.csv ./data/raw/incidents-healthcore.csv

# OWASP A05: the API, the Celery worker and Flower run as an unprivileged user, not root. Runtime
# state lives in the /data volume (TinyDB, agent state, RFP uploads), owned by that user; the code
# under /app stays root-owned and read-only for it.
RUN useradd --system --uid 10001 --create-home --home-dir /home/app app \
 && mkdir -p /data/agent /data/rfp && chown -R app:app /data
USER app

ENV PYTHONPATH=/app
EXPOSE 8000
CMD ["uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "/app/services/api", "--reload-dir", "/app/services/telemetry", "--reload-dir", "/app/services/reporting", "--reload-dir", "/app/services/agent", "--reload-dir", "/app/packages/shared", "--reload-dir", "/app/data"]
