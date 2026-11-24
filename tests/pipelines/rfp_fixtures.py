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
