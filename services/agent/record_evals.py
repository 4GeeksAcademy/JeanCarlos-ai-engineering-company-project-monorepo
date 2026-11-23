"""Run the agent eval cases once against the real sources and store their traces, so the
evals in tests/pipelines/test_agent_evals.py run against the traces instead of executing
the agent every time.

    AGENT_EVAL_USERNAME=... AGENT_EVAL_PASSWORD=... python -m services.agent.record_evals

- Sources: the knowledge base (Qdrant + the 4Geeks gateway) and the live incident and
  inventory APIs at AGENT_TOOLS_API_URL (default http://127.0.0.1:8000, the API must be up).
- Credentials: the tools need a Bearer token like any API client; the recorder logs in with
  AGENT_EVAL_USERNAME / AGENT_EVAL_PASSWORD (environment, never in the repository).
- Faults: a case with "fault" points the tools, for that case only, at a server that accepts
  connections and never answers ("timeout": the real 4 s timeout fires) or at a port with
  nothing listening ("down").

Writes data/eval/agent_eval_traces.json; the runs also stay queryable in
data/agent_state/evals.sqlite.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import sys
import threading
from collections.abc import Iterator
from datetime import datetime, timezone

import httpx

from data.process.rag import REPO_ROOT

from .runner import AgentRejected, AgentRuntime, state_database
from .tools import api_base_url

CASES = REPO_ROOT / "data" / "eval" / "agent-eval-cases.json"
OUTPUT = REPO_ROOT / "data" / "eval" / "agent_eval_traces.json"


def login() -> str:
    username, password = os.environ.get("AGENT_EVAL_USERNAME"), os.environ.get("AGENT_EVAL_PASSWORD")
    if not username or not password:
        raise SystemExit("Set AGENT_EVAL_USERNAME and AGENT_EVAL_PASSWORD (a backoffice user) to record the evals")
    response = httpx.post(f"{api_base_url()}/login", data={"username": username, "password": password}, timeout=10)
    response.raise_for_status()
    return response.json()["access_token"]


@contextlib.contextmanager
def incident_api_fault(kind: str | None) -> Iterator[None]:
    """Point the tools at a broken incident API for one case."""
    if kind is None:
        yield
        return
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    accepted: list[socket.socket] = []
    if kind == "timeout":  # accept and keep the connection open without answering
        listener.listen()
        listener.settimeout(0.2)
        stop = threading.Event()

        def hold() -> None:
            while not stop.is_set():
                with contextlib.suppress(OSError):
                    accepted.append(listener.accept()[0])

        thread = threading.Thread(target=hold, daemon=True)
        thread.start()
    else:  # "down": the port was reserved and is closed again, nothing listens
        listener.close()
    previous = os.environ.get("AGENT_TOOLS_API_URL")
    os.environ["AGENT_TOOLS_API_URL"] = f"http://127.0.0.1:{port}"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("AGENT_TOOLS_API_URL", None)
        else:
            os.environ["AGENT_TOOLS_API_URL"] = previous
        if kind == "timeout":
            stop.set()
            thread.join()
            for connection in accepted:
                connection.close()
            listener.close()


def main() -> int:
    token = login()
    runtime = AgentRuntime(state_database().with_name("evals.sqlite"))
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    traces = []
    for case in cases:
        with incident_api_fault(case.get("fault")):
            try:
                run_id = runtime.run(case["question"], credentials=token).run_id
            except AgentRejected as rejected:
                run_id = rejected.run_id
        trace = runtime.trace(run_id)
        traces.append({"case_id": case["id"], "fault": case.get("fault"), **trace})
        print(f"{case['id']:<22} {trace['status']:<9} [{trace['sources'] or '-'}] {trace['route']}")
    OUTPUT.write_text(json.dumps({
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "traces": traces,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(traces)} traces → {OUTPUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
