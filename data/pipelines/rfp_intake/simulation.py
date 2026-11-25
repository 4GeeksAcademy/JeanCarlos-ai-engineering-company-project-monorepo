"""Reproducible end-to-end run of the RFP flow (Parts 1 → 2 → 3) with SIMULATED human decisions:
each approval is a programmatic resume of its department thread (`approval_runner.submit_decision`
→ `Command(resume=...)`), exactly what the backoffice buttons call.

Used by scripts/rfp_e2e.py (real model, real database) and by tests/pipelines/test_rfp_e2e.py
(scripted model, SQLite). The plan is a list of steps; the report records the ticket status after
every step, so a jump or an inconsistency between parts is visible.

Plans:
- "standard": Clinical approves first while Revenue and Compliance are still paused; Tom Callahan
  requests one change (back to the Part 2 generator), then approves; Claire Whitfield approves
  last → final document.
- "capacity": Clinical confirms less capacity than the covered population → after Compliance
  approves, the arbitration node raises capacity-vs-population and Tom Callahan (fixed arbiter)
  reduces the population; Revenue (and any section that quoted the original population) revises
  and is approved again — Clinical re-confirming the capacity → final document.
"""

from __future__ import annotations

import time
from pathlib import Path

from sqlalchemy.engine import Engine

from . import approval_runner, response_runner, runner, store

APPROVERS = {"revenue": "Tom Callahan", "clinical": "Dr. Marcus Reid", "compliance": "Claire Whitfield"}


def _approve(department_id: str, capacity: int | None = None, comment: str | None = None) -> dict:
    decision = {"action": "approve", "approver": APPROVERS[department_id], "comment": comment}
    if department_id == "clinical":
        decision["confirmed_capacity"] = capacity
    return decision


def plan_for(name: str, covered_population: int | None) -> list[tuple[str, str, dict]]:
    population = covered_population or 100
    if name == "standard":
        return [
            ("decision", "clinical", _approve("clinical", population, "Sites and staff confirmed for the full population.")),
            ("decision", "revenue", {"action": "request_changes", "approver": "Tom Callahan",
                                     "comment": "State that invoices are monthly and in the client's currency; keep "
                                                "the amount as to be confirmed."}),
            ("decision", "revenue", _approve("revenue", comment="Commercial terms approved.")),
            ("decision", "compliance", _approve("compliance", comment="Instrument and approval clause verified.")),
        ]
    if name == "capacity":
        capacity = max(1, population * 2 // 3)
        return [
            ("decision", "clinical", _approve("clinical", capacity, "Current staff covers only part of the population.")),
            ("decision", "revenue", _approve("revenue", comment="Commercial terms approved.")),
            ("decision", "compliance", _approve("compliance", comment="No patient data; instrument verified.")),
            ("arbitration", "revenue", {"trigger": "capacity-vs-population", "arbiter": "Tom Callahan",
                                        "resolution": "reduce_population", "covered_population": capacity,
                                        "comment": "Start with the population the clinics can cover."}),
            ("reopened", "", {"capacity": capacity}),  # every section the ruling sent back, approved again
        ]
    raise ValueError(f"unknown plan {name}")


def _snapshot(engine: Engine, ticket_id: str, step: str, started: float) -> dict:
    view = store.ticket_view(engine, ticket_id)
    return {"step": step, "seconds": round(time.monotonic() - started, 1), "status": view["status"],
            "stage": view["stage"],
            "sections": {s["department_id"]: {"generation": s["generation_status"], "approval": s["approval_status"]}
                         for s in view["sections"]}}


def run(engine: Engine, pdf: Path, plan: str = "standard", *, created_by: str = "e2e") -> dict:
    started = time.monotonic()
    ticket = store.receive(engine, Path(pdf).read_bytes(), created_by=created_by)
    ticket_id = ticket.ticket_id
    timeline = [_snapshot(engine, ticket_id, "upload (Parte 1)", started)]
    runner.run_intake(engine, ticket_id)
    timeline.append(_snapshot(engine, ticket_id, "intake (Parte 1)", started))
    response_runner.run_generation(engine, ticket_id)
    timeline.append(_snapshot(engine, ticket_id, "generation + evaluation (Parte 2)", started))
    approval_runner.open_approval(engine, ticket_id)
    timeline.append({**_snapshot(engine, ticket_id, "open approval (Parte 3)", started),
                     "threads": approval_runner.status(engine, ticket_id)["threads"]})
    metadata = store.ticket_view(engine, ticket_id)["metadata"] or {}
    steps = []
    for kind, department_id, payload in plan_for(plan, metadata.get("covered_population")):
        if kind == "reopened":
            waiting = [d for d, t in approval_runner.status(engine, ticket_id)["threads"].items()
                       if d != "ticket" and t["paused_at"] == "await_approval"]
            for d in waiting:
                decision = _approve(d, payload["capacity"], "Re-approved after the arbiter's ruling.")
                result = approval_runner.submit_decision(engine, ticket_id, d, decision)
                steps.append({"step": f"{decision['approver']} → approve ({d}, reopened)", "input": decision,
                              "threads": {k: v["paused_at"] for k, v in result["threads"].items()}})
                timeline.append(_snapshot(engine, ticket_id, steps[-1]["step"], started))
            continue
        if kind == "decision":
            result = approval_runner.submit_decision(engine, ticket_id, department_id, payload)
            label = f"{payload['approver']} → {payload['action']} ({department_id})"
        else:
            result = approval_runner.submit_arbitration(engine, ticket_id, payload)
            label = f"{payload['arbiter']} → arbitration {payload['resolution']}"
        steps.append({"step": label, "input": payload, "threads": {k: v["paused_at"] for k, v in result["threads"].items()}})
        timeline.append(_snapshot(engine, ticket_id, label, started))
    view = store.ticket_view(engine, ticket_id)
    return {"ticket_id": ticket_id, "rfp_id": ticket.rfp_id, "plan": plan, "status": view["status"],
            "stage": view["stage"], "timeline": timeline, "steps": steps, "arbitration": view.get("arbitration") or [],
            "final_document": store.final_document(engine, ticket_id),
            "trace": [{k: r[k] for k in ("id", "thread_id", "node", "agent", "status", "started_at")}
                      for r in store.trace(engine, ticket_id)]}
