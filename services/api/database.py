import os
import logging
from collections.abc import Iterator
from functools import lru_cache
from json import JSONDecodeError
from pathlib import Path

from fastapi import HTTPException, Request
from dotenv import load_dotenv
from data.pipelines.connections import require_tls
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, create_engine
from tinydb import TinyDB

logger = logging.getLogger(__name__)


def open_database() -> TinyDB:
    path = Path(
        os.environ.get(
            "SUPPLIERS_DB_PATH", str(Path(__file__).parent / "data" / "suppliers.json")
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    return TinyDB(path, ensure_ascii=False, indent=2)


def get_database(request: Request) -> Iterator[TinyDB]:
    # TinyDB is not thread-safe; serialize each complete read/write operation.
    try:
        with request.app.state.database_lock:
            yield request.app.state.database
    except (OSError, JSONDecodeError) as exc:
        logger.error("Database operation failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Database temporarily unavailable") from exc


@lru_cache(maxsize=1)
def get_inventory_engine() -> Engine:
    load_dotenv(Path(__file__).parent / ".env")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for inventory")
    url = make_url(database_url)
    if url.drivername not in {"postgres", "postgresql", "postgresql+psycopg"}:
        raise RuntimeError("DATABASE_URL must use PostgreSQL")
    return create_engine(require_tls(url.set(drivername="postgresql+psycopg")), pool_pre_ping=True)


def get_db() -> Iterator[Session]:
    try:
        engine = get_inventory_engine()
    except (RuntimeError, ValueError, SQLAlchemyError) as exc:
        logger.error("Inventory database configuration failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Inventory database unavailable") from exc
    try:
        with Session(engine) as session:
            yield session
    except SQLAlchemyError as exc:
        logger.error("Inventory database operation failed: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Inventory database unavailable") from exc
