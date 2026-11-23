"""HealthCore departments that take part in an institutional proposal (BRIEFING Hito 9 §2.1).

The only valid department ids are `revenue`, `clinical` and `compliance`. No agent can create
another one: anything else the RFP asks for (IT, legal, HR…) is mapped to one of these or
returned to the ticket owner as an unmapped request (see orchestrator).
"""

from __future__ import annotations

from dataclasses import dataclass

TICKET_OWNER = "revenue"
ALWAYS_REQUIRED = ("revenue", "compliance")  # owner of the ticket + mandatory regulatory review


@dataclass(frozen=True)
class Department:
    id: str
    name: str
    owner: str
    owner_role: str
    contributes: str
    asks: tuple[str, ...]
    keywords: tuple[str, ...]  # used only by the deterministic excerpt fallback


DEPARTMENTS: dict[str, Department] = {
    "revenue": Department(
        id="revenue", name="Revenue Cycle", owner="Tom Callahan", owner_role="Revenue Cycle Director",
        contributes="Términos financieros, moneda y estructura de pago. Dueño del ticket.",
        asks=("Estructura de precios y modelo de cobro", "Moneda de la cotización", "Condiciones de pago y plazo del contrato"),
        keywords=("price", "pricing", "budget", "cost", "fee", "payment", "invoice", "term", "renew", "contract", "deadline",
                  "due", "precio", "presupuesto", "pago", "coste", "costo", "contrato", "plazo", "proposal", "propuesta",
                  "evaluation", "criteria", "submission", "award"),
    ),
    "clinical": Department(
        id="clinical", name="Clinical Operations", owner="Dr. Marcus Reid", owner_role="Clinical Operations",
        contributes="Viabilidad clínica: qué clínicas y qué capacidad de personal pueden cubrir el contrato.",
        asks=("Clínicas/sedes que cubren el contrato", "Capacidad de personal clínico y calendario", "Servicios clínicos viables"),
        keywords=("clinic", "on-site", "onsite", "staff", "screening", "triage", "treatment", "care", "referral", "specialist",
                  "wellness", "occupational", "health", "employees", "students", "campus", "chronic", "injury", "satellite",
                  "clinica", "personal", "atencion", "derivacion", "referido", "capacity", "capacidad", "days/week", "gp"),
    ),
    "compliance": Department(
        id="compliance", name="Compliance and Data Governance", owner="Claire Whitfield", owner_role="Chief Compliance Officer",
        contributes="Revisión regulatoria (HIPAA/UK GDPR) y cláusulas de BAA o DPA según el país del cliente.",
        asks=("Instrumento contractual según país (BAA en EE. UU., DPA con UK GDPR en Reino Unido)",
              "Revisión de tratamiento de datos de salud y seguridad", "Aprobación regulatoria obligatoria antes del cierre"),
        keywords=("hipaa", "gdpr", "baa", "business associate", "dpa", "data", "privacy", "security", "compliance", "compliant",
                  "confidential", "health data", "regulat", "datos", "privacidad", "seguridad", "cumplimiento"),
    ),
}

DEPARTMENT_IDS = tuple(DEPARTMENTS)


def contact(department_id: str) -> dict[str, str]:
    d = DEPARTMENTS[department_id]
    return {"department_id": d.id, "department": d.name, "owner": d.owner, "owner_role": d.owner_role}
