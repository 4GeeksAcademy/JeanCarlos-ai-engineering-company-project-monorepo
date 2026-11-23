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
