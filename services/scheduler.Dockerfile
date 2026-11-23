# Dedicated scheduler for scripts/nightly_export.py (ticket DEV-53). It is a separate
# container from the API: the nightly job never shares a process or thread with FastAPI.
FROM ghcr.io/astral-sh/uv:0.9.26 AS uv
FROM python:3.12-slim

ARG TARGETARCH
# supercronic: cron for containers (passes the environment through, logs to stdout,
# forwards SIGTERM to the job). Pinned version, verified by SHA1 from its release notes.
ARG SUPERCRONIC_VERSION=v0.2.50
ARG SUPERCRONIC_SHA1_AMD64=0795da10bf6303e9680a2c2bd8dd6b9aa33ebda2
ARG SUPERCRONIC_SHA1_ARM64=e0023a0346354a153a4a2708c657308890823d14
ADD https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH} /usr/local/bin/supercronic
RUN case "${TARGETARCH}" in \
      amd64) sum="${SUPERCRONIC_SHA1_AMD64}" ;; \
      arm64) sum="${SUPERCRONIC_SHA1_ARM64}" ;; \
      *) echo "unsupported architecture ${TARGETARCH}" >&2; exit 1 ;; \
    esac \
 && echo "${sum}  /usr/local/bin/supercronic" | sha1sum -c - \
 && chmod 0755 /usr/local/bin/supercronic

COPY --from=uv /uv /uvx /bin/
WORKDIR /app
COPY api/requirements.txt ./services/api/requirements.txt
RUN uv pip install --system --no-cache -r services/api/requirements.txt

COPY job_runner.py ./services/job_runner.py
COPY --from=pipelines __init__.py ./data/__init__.py
COPY --from=pipelines pipelines ./data/pipelines
COPY --from=pipelines process ./data/process
COPY --from=pipelines raw/clinics.csv ./data/raw/clinics.csv
COPY --from=scripts nightly_export.py nightly_export.crontab ./scripts/

RUN useradd --system --create-home scheduler && mkdir -p /app/data/raw && chown -R scheduler /app/data/raw
USER scheduler
ENV TZ=UTC \
    PYTHONUNBUFFERED=1 \
    PREFECT_HOME=/home/scheduler/.prefect
CMD ["supercronic", "-passthrough-logs", "/app/scripts/nightly_export.crontab"]
