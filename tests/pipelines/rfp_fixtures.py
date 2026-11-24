"""Shared stubs for the rfp_intake tests: a scripted model that answers each agent from its
prompt, records every call, and never touches the network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlmodel import create_engine

from data.pipelines.rfp_intake import agents
from data.pipelines.rfp_intake.models import create_tables

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "rfp"
FORMAL, INFORMAL, INVALID, WITH_PHI = (FIXTURES / f"healthcore-request-{n}.pdf" for n in ("1", "2", "3", "4-phi"))

CLASSIFICATIONS = {
    "Meridian": {"is_rfp": True, "confidence": 0.97, "document_type": "formal_rfp", "requester_role": "client",
                 "reasons": ["Empleador de EE. UU. solicita propuesta de salud ocupacional."]},
    "Thames Valley": {"is_rfp": True, "confidence": 0.86, "document_type": "informal_request", "requester_role": "client",
                      "reasons": ["Universidad del Reino Unido pide una propuesta de red de referidos."]},
    "MedFlow": {"is_rfp": False, "confidence": 0.95, "document_type": "vendor_pitch", "requester_role": "vendor",
                "reasons": ["Un proveedor de EHR intenta vender su producto a HealthCore."]},
    "Gulfline": {"is_rfp": True, "confidence": 0.93, "document_type": "formal_rfp", "requester_role": "client",
                 "reasons": ["Empleador de EE. UU. solicita propuesta de salud ocupacional."]},
}
METADATA = {
    "Meridian": {"rfp_reference": "MM-OH-2026-002", "client_name": "Meridian Manufacturing, Inc.", "client_type": "employer",
                 "client_country": "US", "program_types": ["occupational_health", "corporate_wellness"],
                 "covered_population": 800, "population_unit": "employees", "deadline": "2026-08-23",
                 "deadline_text": "August 23, 2026", "budget_range": None, "contract_term": "12 months, with option to renew",
                 "other_departments_mentioned": []},
    "Thames Valley": {"rfp_reference": None, "client_name": "Thames Valley University", "client_type": "university",
                      "client_country": "UK", "program_types": ["referral_network"], "covered_population": 3000,
                      "population_unit": "students", "deadline": None, "deadline_text": "around August 28th",
                      "budget_range": None, "contract_term": None, "other_departments_mentioned": []},
    "Gulfline": {"rfp_reference": "GL-OH-2026-014", "client_name": "Gulfline Logistics, Inc.", "client_type": "employer",
                 "client_country": "US", "program_types": ["occupational_health"], "covered_population": 450,
                 "population_unit": "employees", "deadline": "2026-09-30", "deadline_text": "September 30, 2026",
                 "budget_range": "USD 180,000 - 220,000 per year", "contract_term": None, "other_departments_mentioned": []},
}
WORKERS = {
    "revenue": {"key_aspects": [{"aspect": "Moneda y términos", "detail": "Cotización en la moneda del país del cliente; "
                                 "estructura de pago por definir.", "source": "metadata"}],
                "requests": ["Modelo de precios por empleado/estudiante"], "open_questions": [],
                "facts": {"covered_population": None, "currency": None, "agreement_type": None}},
    "clinical": {"key_aspects": [{"aspect": "Capacidad clínica", "detail": "Definir sedes y personal para el servicio "
                                  "solicitado.", "source": "P2"}],
                 "requests": ["Confirmar sedes y personal disponibles"], "open_questions": [],
                 "facts": {"covered_population": None, "currency": None, "agreement_type": None}},
    "compliance": {"key_aspects": [{"aspect": "Instrumento regulatorio", "detail": "Cláusula del acuerdo de datos según "
                                    "el país del cliente.", "source": "metadata"}],
                   "requests": ["Preparar la cláusula BAA o DPA"], "open_questions": [],
                   "facts": {"covered_population": None, "currency": None, "agreement_type": None}},
}


def draft(department_id: str, country: str) -> str:
    """A section that follows the HealthCore format and passes the three evaluators."""
    currency = {"US": "USD", "UK": "GBP"}[country]
    if department_id == "revenue":
        return f"""## Commercial Terms — Revenue Cycle
### Pricing and currency
All prices in this proposal are quoted in {currency}. We propose a simple price per person per month. The amount is \
[amount to be confirmed by Revenue Cycle]. It covers the services in the scope of the request. There are no hidden fees. \
Extra services are quoted on request. The price model keeps the terms clear for the client.
### Payment structure
We invoice once a month. Each invoice lists the services we delivered. Payment is due when the client receives the \
invoice. We can also agree on a plan with fewer invoices. Tom Callahan owns the commercial terms. He is the contact for \
billing questions.
### Contract term
The term follows the request of the client. We can renew the contract at the end of the term. Both parties review the \
prices before a renewal. Any change is agreed in writing.
### Open questions
- What budget does the client plan for this program?
- Does the client prefer monthly invoices?
"""
    if department_id == "clinical":
        sites = {"US": "our clinics in Texas", "UK": "our clinics in London and Manchester"}[country]
        return f"""## Clinical Delivery — Clinical Operations
### Sites and coverage
We will deliver the program from {sites}. Each site has a clinical lead. The lead plans the work with the client. \
We pick the site that is closest to the people we serve. The client can visit each site before the start.
### Staffing and capacity
Dr. Marcus Reid confirms the staff plan before we sign. We do not assume free capacity. We check the schedule of each \
clinic first. Then we confirm the number of staff for the program.
### Clinical services
We offer the services that the request asks for. Our teams follow the same care standards at every site. Results go to \
the person who received the care. The client receives only aggregate reports.
### Open questions
- How many people will use the service each month?
- Which days of the week does the client prefer?
"""
    clause = {"US": ("Business Associate Agreement (BAA)", "HIPAA", "We sign a Business Associate Agreement (BAA) with "
                     "the client under HIPAA before any service starts."),
              "UK": ("Data Processing Agreement (DPA) under UK GDPR", "UK GDPR", "We sign a Data Processing Agreement "
                     "(DPA) with the client under UK GDPR before any service starts.")}[country]
    return f"""## Data Protection and Compliance — Compliance and Data Governance
### Regulatory framework
This program follows {clause[1]}. We protect health data at every step. Only the care team can see the records. The \
client receives aggregate reports only. We never share an individual health record with the client. Our staff receive privacy training every year. \
Access to the data is logged and reviewed.
### {clause[0]}
{clause[2]} The agreement sets how we handle and protect the data. It also sets how we report a breach. Our standard \
template is the starting point.
### Compliance approval
Claire Whitfield must approve this proposal before signature. Her approval is required for every institutional \
proposal. No contract is signed without it.
### Open questions
- Does the client want to use its own agreement template?
- Who is the data protection contact at the client?
"""


class ScriptedModel:
    """Answers each agent according to its system prompt; `overrides[agent]` replaces an answer
    (a dict, a raw string, or an Exception to raise)."""

    def __init__(self):
        self.calls: list[tuple[str, str, str]] = []  # (agent, system, user)
        self.overrides: dict[str, object] = {}

    @staticmethod
    def agent_of(system: str) -> str:
        for department, name in (("revenue", "Revenue Cycle"), ("clinical", "Clinical Operations"),
                                 ("compliance", "Compliance and Data Governance")):
            if system.startswith(f"Eres el worker de {name}"):
                return f"worker:{department}"
            if system.startswith(f"Eres el generador de {name}"):
                return f"generator:{department}"
        if "evaluador de pertinencia" in system:
            return "evaluator:relevance"
        if "agente clasificador" in system:
            return "classifier"
        if "extractor de metadatos" in system:
            return "extractor"
        if "orquestador" in system:
            return "orchestrator"
        if "synthesizer" in system:
            return "synthesizer"
        raise AssertionError("unknown agent prompt")

    @staticmethod
    def client(user: str) -> str:
        return next((k for k in CLASSIFICATIONS if k in user), "MedFlow")

    def __call__(self, messages):
        system, user = messages[0]["content"], messages[1]["content"]
        agent = self.agent_of(system)
        self.calls.append((agent, system, user))
        override = self.overrides.get(agent)
        if isinstance(override, list):  # one answer per call, the last one repeats
            override = override.pop(0) if len(override) > 1 else override[0]
        if isinstance(override, Exception):
            raise override
        if isinstance(override, str):
            return override
        if override is not None:
            return json.dumps(override)
        if agent == "classifier":
            return json.dumps(CLASSIFICATIONS[self.client(user)])
        if agent == "extractor":
            return json.dumps(METADATA[self.client(user)])
        if agent == "orchestrator":
            return json.dumps({"workstreams": [{"department_id": d, "paragraph_ids": [1, 2, 3], "task": "analizar"}
                                               for d in ("revenue", "clinical", "compliance")], "unmapped": []})
        if agent == "synthesizer":
            return json.dumps({"summary": "El cliente pide una propuesta institucional; Revenue, Clinical y Compliance "
                                          "deben aportar su parte antes de cotizar.",
                               "next_steps": ["Pedir a Clinical Operations la capacidad de sedes — clinical"]})
        if agent.startswith("generator:"):
            country = "UK" if '"client_country": "UK"' in user else "US"
            return json.dumps({"content": draft(agent.split(":")[1], country), "open_questions": []})
        if agent == "evaluator:relevance":
            section = user.split("<seccion>")[1]
            evidence = next(line for line in section.splitlines() if line.strip() and not line.startswith("#"))
            count = user.split("<seccion>")[0].count("\n") - 2
            return json.dumps({"aspects": [{"index": i, "covered": True, "evidence": evidence.split(". ")[0]}
                                           for i in range(max(count, 1))]})
        return json.dumps(WORKERS[agent.split(":")[1]])

    def agents_called(self) -> list[str]:
        return [c[0] for c in self.calls]


@pytest.fixture
def model(monkeypatch):
    scripted = ScriptedModel()
    monkeypatch.setattr(agents, "_chat", scripted)
    return scripted


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("RFP_RAW_DIR", str(tmp_path / "raw" / "rfp"))
    # a file database: the department workers write in parallel threads (PostgreSQL in production)
    eng = create_engine(f"sqlite:///{tmp_path / 'rfp.sqlite'}", connect_args={"check_same_thread": False, "timeout": 30})
    create_tables(eng)
    return eng
