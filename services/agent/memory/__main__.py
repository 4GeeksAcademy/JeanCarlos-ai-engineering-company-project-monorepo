"""Inspect and maintain the agent memory.

    python -m services.agent.memory list          # active memories
    python -m services.agent.memory proposals     # every proposal and how it ended
    python -m services.agent.memory audit         # append-only event log
    python -m services.agent.memory consolidate   # PHI re-check, expiry, capacity, stale proposals
"""

import json
import sys

from .store import get_store


def main(argv: list[str] | None = None) -> int:
    command = (argv or sys.argv[1:] or ["list"])[0]
    store = get_store()
    if command == "list":
        output = store.list_memories()
    elif command == "proposals":
        output = store.proposals()
    elif command == "audit":
        output = store.audit_trail()
    elif command == "consolidate":
        output = store.consolidate()
    else:
        print(__doc__, file=sys.stderr)
        return 2
    print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
