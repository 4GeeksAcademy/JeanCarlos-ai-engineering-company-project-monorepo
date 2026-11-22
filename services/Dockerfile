FROM ghcr.io/astral-sh/uv:0.9.26 AS uv
FROM python:3.12-slim

COPY --from=uv /uv /uvx /bin/
WORKDIR /app

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

ENV PYTHONPATH=/app
EXPOSE 8000
CMD ["uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "/app/services/api", "--reload-dir", "/app/services/telemetry", "--reload-dir", "/app/services/reporting", "--reload-dir", "/app/data"]
