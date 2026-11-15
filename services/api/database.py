import os
from collections.abc import Iterator
from pathlib import Path

from fastapi import Request
from tinydb import TinyDB


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
    with request.app.state.database_lock:
        yield request.app.state.database
