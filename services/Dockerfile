FROM ghcr.io/astral-sh/uv:0.9.26 AS uv
FROM python:3.12-slim

COPY --from=uv /uv /uvx /bin/
WORKDIR /app

COPY api/requirements.txt ./services/api/requirements.txt
RUN uv pip install --system --no-cache -r services/api/requirements.txt
COPY api ./services/api

EXPOSE 8000
CMD ["uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "/app/services/api"]
