"""One structured log line per tool invocation: which tool, which client, what result.

Written as JSON to the `mcp.audit` logger (stdout) and appended to MCP_AUDIT_LOG
(default mcps/healthcore_mcp/logs/tool-invocations.jsonl, gitignored). Arguments are logged
by name and identifiers only: free text (title, description) is never written to the log.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("mcp.audit")
_lock = threading.Lock()
SAFE_ARGUMENTS = {"incident_id", "status", "category", "origin", "branch", "limit", "operation", "sku", "product"}


def audit_path() -> Path:
    default = Path(__file__).resolve().parent / "logs" / "tool-invocations.jsonl"
    return Path(os.environ.get("MCP_AUDIT_LOG", default))


def record(tool: str, *, client_id: str | None, subject: str | None, scopes: list[str], arguments: dict[str, Any],
           result: str, error_code: str | None, duration_ms: float, upstream: str | None = None) -> dict[str, Any]:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "tool": tool,
        "client_id": client_id,
        "subject": subject,
        "scopes": scopes,
        "arguments": {k: v for k, v in arguments.items() if k in SAFE_ARGUMENTS and v is not None},
        "argument_names": sorted(arguments),
        "result": result,
        "error_code": error_code,
        "upstream": upstream,
        "duration_ms": round(duration_ms, 1),
    }
    line = json.dumps(entry, ensure_ascii=False)
    logger.info(line)
    path = audit_path()
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    return entry
