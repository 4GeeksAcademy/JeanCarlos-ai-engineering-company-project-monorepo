"""Run the agent eval cases once (live: embeddings, Qdrant and the generation model) and
store their traces, so the evals in tests/pipelines/test_agent_evals.py run against the
traces instead of executing the agent every time.

    python -m services.agent.record_evals

Writes data/eval/agent_eval_traces.json (one full trace per case: steps in order, what each
node produced, checkpoints, final status and answer). The runs also stay queryable in
data/agent_state/evals.sqlite.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from data.process.rag import REPO_ROOT

from .runner import AgentRejected, AgentRuntime, state_database

CASES = REPO_ROOT / "data" / "eval" / "agent-eval-cases.json"
OUTPUT = REPO_ROOT / "data" / "eval" / "agent_eval_traces.json"


def main() -> int:
    runtime = AgentRuntime(state_database().with_name("evals.sqlite"))
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    traces = []
    for case in cases:
        try:
            result = runtime.run(case["question"])
            run_id = result.run_id
        except AgentRejected as rejected:
            run_id = rejected.run_id
        trace = runtime.trace(run_id)
        traces.append({"case_id": case["id"], **trace})
        print(f"{case['id']:<20} {trace['status']:<9} {trace['route']}")
    OUTPUT.write_text(json.dumps({
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "traces": traces,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(traces)} traces → {OUTPUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
