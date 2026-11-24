"""RFP intake CLI (Hito 9 Parte 1): manual reprocess and smoke runs. Not a second API — it calls
the same pipeline (data/pipelines/rfp_intake) and the same PostgreSQL as the backend.

    python scripts/rfp_intake.py process <file.pdf>      # create a ticket and run the flow (sync)
    python scripts/rfp_intake.py reprocess <ticket_id>   # re-run a failed/stuck ticket from its stored PDF
    python scripts/rfp_intake.py show <ticket_id>        # ticket + metadata + sections + synthesis (JSON)
    python scripts/rfp_intake.py list
    python scripts/rfp_intake.py generate <ticket_id>    # Part 2: drafts + evaluation for a queued ticket (sync)
    python scripts/rfp_intake.py generate-retry <ticket_id>   # re-run a failed generation
    python scripts/rfp_intake.py generate-ready          # drain the Part 1 queue (analisis_completo + ready)
    python scripts/rfp_intake.py approval-open <ticket_id>     # Part 3: open the approval flow (Part 2 queue)
    python scripts/rfp_intake.py approval <ticket_id>          # where each checkpointed thread is paused
    python scripts/rfp_intake.py decide <ticket_id> '<json>'   # resume a department: {"department_id": "clinical",
                                                               #   "action": "approve", "approver": "Dr. Marcus Reid",
                                                               #   "confirmed_capacity": 800}
    python scripts/rfp_intake.py arbitrate <ticket_id> '<json>'  # ruling of the fixed arbiter (BRIEFING §7)
    python scripts/rfp_intake.py document <ticket_id>          # final document (Markdown)
    python scripts/rfp_intake.py trace <ticket_id>             # agent / node / status of every execution
    python scripts/rfp_intake.py scram-verifier          # SCRAM verifier for scripts/supabase_rfp_role.sql

Database: RFP_DATABASE_URL if set, otherwise DATABASE_URL (services/api/.env).
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def engine():
    url = os.environ.get("RFP_DATABASE_URL", "").strip()
    if url:
        from services.api.routes.rfp import _dedicated_engine

        return _dedicated_engine(url)
    from services.api.database import get_inventory_engine

    return get_inventory_engine()


def scram_verifier(password: str, iterations: int = 4096) -> str:
    salt = os.urandom(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda raw: base64.b64encode(raw).decode()  # noqa: E731
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(hashlib.sha256(client_key).digest())}:{b64(server_key)}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["process", "reprocess", "show", "list", "generate", "generate-retry",
                                                 "generate-ready", "approval-open", "approval", "decide", "arbitrate",
                                                 "document", "trace", "scram-verifier"])
    parser.add_argument("target", nargs="?")
    parser.add_argument("payload", nargs="?", help="JSON for decide / arbitrate")
    args = parser.parse_args(argv)
    if args.command == "scram-verifier":
        print(scram_verifier(getpass.getpass("Password for rfp_pipeline: ")))
        return 0

    from data.pipelines.rfp_intake import approval_runner, response_runner, runner, store
    from data.pipelines.rfp_intake.models import create_tables

    eng = engine()
    create_tables(eng)
    if args.command == "list":
        print(json.dumps(store.list_tickets(eng), ensure_ascii=False, indent=2))
        return 0
    if args.command == "generate-ready":
        for ticket_id in store.generation_queue(eng):
            print(f"ticket {ticket_id} → {response_runner.run_generation(eng, ticket_id)}")
        return 0
    if not args.target:
        parser.error("target required")
    if args.command in ("approval-open", "approval", "decide", "arbitrate", "document", "trace"):
        return part3(args, eng, approval_runner, store)
    if args.command == "process":
        ticket = store.receive(eng, Path(args.target).read_bytes(), created_by="cli")
        ticket_id = ticket.ticket_id
        print(f"ticket {ticket.rfp_id} ({ticket_id}) → {runner.run_intake(eng, ticket_id)}")
    elif args.command in ("generate", "generate-retry"):
        ticket_id = args.target
        print(f"ticket {ticket_id} → {response_runner.run_generation(eng, ticket_id, retry=args.command == 'generate-retry')}")
    elif args.command == "reprocess":
        ticket_id = args.target
        print(f"ticket {ticket_id} → {runner.run_intake(eng, ticket_id)}")
    else:
        ticket_id = args.target
    view = store.ticket_view(eng, ticket_id)
    if view is None:
        print("ticket not found", file=sys.stderr)
        return 1
    if args.command == "show":
        print(json.dumps(view, ensure_ascii=False, indent=2))
    return 0 if view["stage"] not in ("failed", "generation_failed") else 2


def part3(args, eng, approval_runner, store) -> int:
    ticket_id = args.target
    if args.command == "approval-open":
        result = approval_runner.open_approval(eng, ticket_id)
    elif args.command == "approval":
        result = approval_runner.status(eng, ticket_id)
    elif args.command == "decide":
        payload = json.loads(args.payload or "{}")
        result = approval_runner.submit_decision(eng, ticket_id, payload.pop("department_id"), payload)
    elif args.command == "arbitrate":
        result = approval_runner.submit_arbitration(eng, ticket_id, json.loads(args.payload or "{}"))
    elif args.command == "document":
        document = store.final_document(eng, ticket_id)
        print(document["markdown"] if document else "no final document yet", file=sys.stdout if document else sys.stderr)
        return 0 if document else 1
    else:
        for row in store.trace(eng, ticket_id):
            print(f"{row['id']:>4} {row['started_at'][11:23]} {row['status']:<11} {row['thread_id'] or '':<52} "
                  f"{row['node']:<20} {row['agent']}")
        return 0
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
