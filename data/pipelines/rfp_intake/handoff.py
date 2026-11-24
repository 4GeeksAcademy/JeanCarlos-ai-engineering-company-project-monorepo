"""Routing handoff to Part 2 (contract `rfp_intake.handoff/v1`).

Stored in `rfp_ticket.handoff` (JSON) together with the queue flag
`rfp_ticket.ready_for_generation`. Part 2 picks up work with

    SELECT ticket_id, handoff FROM rfp_ticket
    WHERE status = 'analisis_completo' AND ready_for_generation

and starts from `workstreams` (key aspects per department) and `rfp_ticket.document_markdown`
(already converted and PHI-redacted): it never re-parses the PDF. A ticket whose RFP contained
PHI is routed to `compliance_review` with ready_for_generation = false: Claire Whitfield must
clear it before any generator runs (BRIEFING: the phi-detected trigger is a hard stop).
"""

from __future__ import annotations

from .departments import DEPARTMENTS

CONTRACT = "rfp_intake.handoff/v1"


def build_handoff(ticket_id: str, state: dict, synthesis: dict) -> dict:
    metadata = state["metadata"]
    phi = state["phi"]
    country = metadata.get("client_country")
    clauses = {"US": ["BAA (Business Associate Agreement, HIPAA)"],
               "UK": ["DPA (Data Processing Agreement, UK GDPR)"]}.get(country, ["Por confirmar: BAA (US) o DPA (UK)"])
    return {
        "contract": CONTRACT,
        "ticket_id": ticket_id,
        "route": "compliance_review" if phi["detected"] else "proposal_generation",
        "next_status": "generando_borrador",
        "metadata": {k: metadata.get(k) for k in ("rfp_reference", "client_name", "client_type", "client_country",
                                                  "program_type", "program_types", "covered_population",
                                                  "population_unit", "deadline", "deadline_text", "budget_range",
                                                  "contract_term", "currency", "regulation", "agreement_type",
                                                  "departments_needed")},
        "currency": metadata.get("currency"),
        "required_clauses": clauses,
        "workstreams": [{"department_id": w["department_id"], "owner": w["owner"], "key_aspects": w["key_aspects"],
                         "requests": w["requests"], "open_questions": w["open_questions"]}
                        for w in synthesis["workstreams"]],
        "conflicts": synthesis["conflicts"],
        "open_questions": synthesis["open_questions"],
        "phi": {"detected": phi["detected"], "categories": phi["categories"]},
        "document_ref": "rfp_ticket.document_markdown",
    }


# ================================================================ Part 2 → Part 3

RESPONSE_CONTRACT = "rfp_response.handoff/v1"


def _signals(sections) -> list[dict]:
    """Part 3 arbitration triggers detectable in the structured evaluation (BRIEFING §7). Part 2
    only signals them; the arbitration node of Part 3 resolves them."""
    signals = []
    for s in sections:
        result = s.evaluation_results or {}
        if result.get("contains_phi"):
            signals.append({"trigger": "phi-detected", "department_id": s.department_id, "arbiter": "compliance"})
        rules = {v["rule_id"] for v in (result.get("compliance") or {}).get("violations", [])}
        if rules & {"HC-BAA-US", "HC-DPA-UK"}:
            signals.append({"trigger": "baa-dpa-mismatch", "department_id": s.department_id, "arbiter": "compliance"})
    return signals


def build_response_handoff(ticket, sections, max_iterations: int) -> dict:
    """Stored in `rfp_ticket.response_handoff` with the queue flag `ready_for_approval`. Part 3
    picks up `status IN ('en_evaluación', 'needs_human_review') AND ready_for_approval`. Every
    department travels with its generated content AND its structured EvaluationResult; sections
    that exhausted the iteration limit go as needs_human_review — never removed."""
    intake = ticket.handoff or {}
    order = list(DEPARTMENTS)
    sections = sorted(sections, key=lambda s: order.index(s.department_id))  # BRIEFING order, as the final document
    return {
        "contract": RESPONSE_CONTRACT,
        "ticket_id": ticket.ticket_id,
        "intake_contract": intake.get("contract"),
        "next_status": "esperando_aprobación",
        "client_country": (intake.get("metadata") or {}).get("client_country"),
        "currency": intake.get("currency"),
        "required_clauses": intake.get("required_clauses", []),
        "max_iterations": max_iterations,
        "sections": [{"department_id": s.department_id, "owner": s.owner, "approver": s.owner,
                      "status": s.generation_status, "iterations": s.iterations, "draft_content": s.draft_content,
                      "evaluation_result": s.evaluation_results,
                      "contains_phi": bool((s.evaluation_results or {}).get("contains_phi"))} for s in sections],
        "needs_human_review": [s.department_id for s in sections if s.generation_status == "needs_human_review"],
        "compliance_approval_required": True,  # BRIEFING §5: always, whatever the other departments
        "signals": _signals(sections),
    }
