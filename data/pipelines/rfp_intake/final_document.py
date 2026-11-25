"""Ultimate document synthesizer (Hito 9 Parte 3): consolidates the APPROVED sections into the
final economic proposal delivered to Revenue Cycle (HealthCore's "Sales", BRIEFING §1).

Deterministic on purpose: the text that goes to the client is exactly the text each named
approver signed off — no model rewrites it after the approval. The synthesizer only adds the
cover (client, country, regulatory instrument, currency, population), the approvals table and
the arbitration record, all from structured state.

Guards (the synthesizer refuses to run, it never "fixes" a document):
- every required department approved, compliance always (§5, §6);
- no section contains PHI, and the consolidated Markdown passes the PHI rules again (§6, §7);
- the quote currency matches client_country (§5).
"""

from __future__ import annotations

from datetime import datetime

from . import approval, phi
from .departments import DEPARTMENTS


class SynthesisBlocked(RuntimeError):
    pass


def _date(value) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M UTC")
    return str(value or "")


def build(ticket, metadata, sections: list, required: list[str], arbitration: list[dict], generated_at: datetime) -> dict:
    rows = {s.department_id: s for s in sections}
    missing = [d for d in required if d not in rows or rows[d].approval_status != "approved"]
    if missing or "compliance" not in required:
        raise SynthesisBlocked(f"approvals missing: {', '.join(missing) or 'compliance'}")
    with_phi = [d for d in required if approval.section_phi(rows[d])]
    if with_phi:
        raise SynthesisBlocked(f"phi-detected in {', '.join(with_phi)}")
    country = metadata.client_country if metadata else None
    currency = {"US": "USD", "UK": "GBP"}.get(country or "")
    if metadata and metadata.currency and currency and metadata.currency != currency:
        raise SynthesisBlocked("currency does not match client_country")

    instrument = {"US": "HIPAA — Business Associate Agreement (BAA)",
                  "UK": "UK GDPR — Data Processing Agreement (DPA)"}.get(country or "", "to be confirmed")
    population = approval.agreed_population(ticket, metadata)
    unit = (metadata.population_unit if metadata else None) or "people"
    ordered = [rows[d] for d in DEPARTMENTS if d in required]
    lines = [
        f"# Economic Proposal — HealthCore for {(metadata.client_name if metadata else None) or 'the client'}",
        "",
        f"- **Client:** {(metadata.client_name if metadata else None) or '—'} ({country or '—'})",
        f"- **Program:** {', '.join((metadata.program_types or []) if metadata else []) or '—'}",
        f"- **RFP reference:** {(metadata.rfp_reference if metadata else None) or '—'} · **Ticket:** {ticket.rfp_id}",
        f"- **Covered population:** {f'{population} {unit}' if population else 'to be confirmed'}",
        f"- **Currency:** {currency or (metadata.currency if metadata else None) or '—'}",
        f"- **Regulatory framework:** {instrument}",
        f"- **Prepared for:** Revenue Cycle — {DEPARTMENTS['revenue'].owner} (owner of the ticket)",
        f"- **Generated:** {_date(generated_at)}",
        "",
    ]
    document_sections, approvals = [], []
    for row in ordered:
        department = DEPARTMENTS[row.department_id]
        lines += [row.draft_content.strip(), "",
                  f"_Approved by {row.approver} ({department.owner_role}) on {_date(row.approved_at)}._", ""]
        document_sections.append({"department_id": row.department_id, "department": department.name,
                                  "content": row.draft_content.strip(), "approver": row.approver,
                                  "approved_at": _date(row.approved_at), "revision_rounds": row.revision_rounds,
                                  "iterations": row.iterations, "generation_status": row.generation_status})
        approvals.append({"department_id": row.department_id, "approver": row.approver, "decision": "approved",
                          "approved_at": _date(row.approved_at), "revision_rounds": row.revision_rounds,
                          "confirmed_capacity": row.confirmed_capacity})
    lines += ["## Approvals", "", "| Department | Approver | Decision | Date | Revision rounds |", "|---|---|---|---|---|"]
    lines += [f"| {DEPARTMENTS[a['department_id']].name} | {a['approver']} | approved | {a['approved_at']} | "
              f"{a['revision_rounds']} |" for a in approvals]
    if arbitration:
        lines += ["", "## Arbitration record", "", "| Round | Trigger | Fixed arbiter | Resolution |", "|---|---|---|---|"]
        lines += [f"| {e.get('round', '')} | {e['trigger']} | {e['arbiter']} | {e['resolution']}"
                  f"{' — population ' + str(e['agreed_population']) if e.get('agreed_population') else ''} |"
                  for e in arbitration]
    markdown = "\n".join(lines).strip() + "\n"
    if phi.find_phi(markdown):  # fail closed
        raise SynthesisBlocked("phi-detected in the consolidated document")
    return {"ticket_id": ticket.ticket_id, "rfp_id": ticket.rfp_id,
            "client_name": metadata.client_name if metadata else None, "client_country": country,
            "currency": currency or (metadata.currency if metadata else None),
            "regulation": metadata.regulation if metadata else None,
            "agreement_type": metadata.agreement_type if metadata else None,
            "sections": document_sections, "approvals": approvals, "arbitration": arbitration, "markdown": markdown}
