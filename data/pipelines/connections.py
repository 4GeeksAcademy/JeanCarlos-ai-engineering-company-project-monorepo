"""Database connections of the business pipeline.

Credentials never live in code or in the repository (PIPELINE_DESIGN.md §7, "Blocks").
Each connection is resolved, in order, from:

1. an environment variable (services/api/.env is loaded if present, gitignored);
2. the Prefect Secret block of the same purpose, for deployments.

| Purpose | Env var | Prefect block | Role |
| --- | --- | --- | --- |
| Pipeline: read telemetry_events, write `reporting` | REPORTING_PIPELINE_DATABASE_URL | healthcore-reporting-pipeline | reporting_pipeline |
| Backend: read `reporting` | REPORTING_DATABASE_URL | healthcore-reporting-readonly | reporting_reader |
| Reconciliation: read inventory | INVENTORY_READONLY_DATABASE_URL (or DATABASE_URL) | healthcore-inventory-readonly | inventory reader |
"""

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url

REPO_ROOT = Path(__file__).resolve().parents[2]
_POSTGRES_DRIVERS = {"postgres", "postgresql", "postgresql+psycopg"}


class MissingConnection(RuntimeError):
    """No URL configured for a connection (neither env var nor Prefect block)."""


def _secret_block(name: str) -> str | None:
    try:
        from prefect.blocks.system import Secret

        return Secret.load(name).get()
    except Exception:  # Block missing or no Prefect API: treated as "not configured".
        return None


def database_url(env_vars: tuple[str, ...], block_name: str) -> str:
    load_dotenv(REPO_ROOT / "services" / "api" / ".env")
    for variable in env_vars:
        if os.environ.get(variable):
            return os.environ[variable]
    url = _secret_block(block_name)
    if url:
        return url
    raise MissingConnection(f"Set {' or '.join(env_vars)} or the Prefect Secret block '{block_name}'")


def engine_for(url: str) -> Engine:
    parsed = make_url(url)
    if parsed.drivername not in _POSTGRES_DRIVERS:
        raise MissingConnection("The pipeline only supports PostgreSQL connections")
    # prepare_threshold=None keeps psycopg compatible with Supabase's poolers.
    return create_engine(
        parsed.set(drivername="postgresql+psycopg"),
        pool_pre_ping=True,
        connect_args={"prepare_threshold": None},
    )


@lru_cache(maxsize=1)
def pipeline_engine() -> Engine:
    """reporting_pipeline. Use the session pooler (port 5432): the run lock is a session advisory lock."""
    return engine_for(database_url(("REPORTING_PIPELINE_DATABASE_URL",), "healthcore-reporting-pipeline"))


@lru_cache(maxsize=1)
def reporting_reader_engine() -> Engine:
    """reporting_reader: what services/reporting uses to read the `reporting` schema."""
    return engine_for(database_url(("REPORTING_DATABASE_URL",), "healthcore-reporting-readonly"))


@lru_cache(maxsize=1)
def inventory_engine() -> Engine:
    """Inventory Postgres, read-only use (reconciliation only)."""
    return engine_for(
        database_url(("INVENTORY_READONLY_DATABASE_URL", "DATABASE_URL"), "healthcore-inventory-readonly")
    )
