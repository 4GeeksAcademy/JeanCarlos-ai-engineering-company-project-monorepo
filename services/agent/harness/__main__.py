"""Guardrail activations of the compliance assistant.

    python -m services.agent.harness summary [--since 2026-11-23T10:00:00] [--conversation <id>]
    python -m services.agent.harness events  [--since …] [--conversation …]
"""

import argparse
import json
import sys

from .events import get_events


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Guardrail activations (blocks, redirects, sanitizations)")
    parser.add_argument("command", choices=["summary", "events"])
    parser.add_argument("--since", help="ISO timestamp: only activations from then on (a test session)")
    parser.add_argument("--conversation", help="only one conversation")
    args = parser.parse_args(argv)
    store = get_events()
    data = (store.summary(since=args.since, conversation_id=args.conversation) if args.command == "summary"
            else store.events(since=args.since, conversation_id=args.conversation))
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
