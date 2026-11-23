"""Nodes of the agent graph: one responsibility each.

- RAG (Part 1): retrieve_context calls retrieve() and generate_answer calls
  generate_answer(question, context) from data/pipelines/rag.py with the chunks that
  retrieve_context already produced. No node calls the monolithic query().
- Tools: plan_sources decides the sources; lookup_tickets and lookup_stock read the live
  Incidents Manager and inventory through the HealthCore MCP server (services/agent/tools.py,
  MCP client via langchain-mcp-adapters); ticket_fallback and stock_fallback answer honestly
  when a tool fails; compose_answer joins the parts.
"""

from __future__ import annotations

from datetime import datetime, timezone

from data.pipelines import rag
from packages.shared.incidents import BRANCH_LABELS

from . import planner, tools
from .state import AgentState

MIN_QUESTION_CHARS = 3
MAX_QUESTION_CHARS = 1000
NO_INFORMATION_ANSWER = (
    "No tengo información sobre eso en la base de conocimiento de HealthCore (seguros aceptados, "
    "citas y cancelaciones, referencias internas y checklist de pacientes nuevos). Para no darte un "
    "dato incorrecto, consúltalo con la supervisión de Patient Experience (Priya Nair); si es sobre "
    "seguros, coberturas o precios, verifícalo con facturación (Tom Callahan).\n\nFuente: ninguna"
)

# Keys each node may write; checked against AgentState when the graph is compiled.
NODE_OUTPUTS: dict[str, frozenset[str]] = {
    "receive_question": frozenset({"question", "status", "error"}),
    "reject_question": frozenset({"status", "error"}),
    "plan_sources": frozenset({"plan", "status"}),
    "lookup_tickets": frozenset({"ticket_result"}),
    "ticket_fallback": frozenset({"tool_fallbacks"}),
    "lookup_stock": frozenset({"stock_result"}),
    "stock_fallback": frozenset({"tool_fallbacks"}),
    "retrieve_context": frozenset({"context", "status"}),
    "generate_answer": frozenset({"rag_answer"}),
    "answer_no_information": frozenset({"rag_answer"}),
    "compose_answer": frozenset({"answer", "status"}),
}

STATUS_LABELS = {"open": "Abierto", "in_progress": "En progreso", "resolved": "Resuelto", "discarded": "Descartado"}
CATEGORY_LABELS = {
    "clinical_equipment": "equipo clínico", "it_system": "sistemas", "billing_error": "error de facturación",
    "compliance_breach": "cumplimiento normativo", "patient_experience": "experiencia del paciente",
    "staff_issue": "personal", "facility_issue": "instalaciones", "referral_issue": "referencias", "other": "otro",
}
ORIGIN_LABELS = {"customer": "cliente (paciente o representante)", "branch": "sede (personal de la clínica)",
                 "internal": "interno"}
UNIT_LABELS = {"box": "cajas", "unit": "unidades", "pack": "packs", "vial": "viales"}
COUNTRY_LABELS = {"US": "Estados Unidos", "UK": "Reino Unido"}


# ---------------------------------------------------------------- input


def receive_question(state: AgentState) -> AgentState:
    """Normalize the coordinator's question and decide whether it can be answered."""
    question = " ".join(str(state.get("question") or "").split())
    if len(question) < MIN_QUESTION_CHARS:
        return {"question": question, "status": "rejected", "error": "La pregunta está vacía o es demasiado corta."}
    if len(question) > MAX_QUESTION_CHARS:
        return {"question": question[:MAX_QUESTION_CHARS], "status": "rejected",
                "error": f"La pregunta supera los {MAX_QUESTION_CHARS} caracteres."}
    return {"question": question, "status": "received"}


def reject_question(state: AgentState) -> AgentState:
    """Terminal node for invalid input: no source is consulted."""
    return {"status": "rejected", "error": state.get("error") or "La pregunta no es válida."}


def plan_sources(state: AgentState) -> AgentState:
    """Decide which sources the question needs: knowledge base, ticket tool, stock tool."""
    return {"plan": planner.plan_sources(state["question"]).model_dump(mode="json"), "status": "planned"}


# ---------------------------------------------------------------- tools


def lookup_tickets(state: AgentState) -> AgentState:
    query = tools.TicketQuery.model_validate(state["plan"]["ticket"])
    return {"ticket_result": tools.lookup_tickets(query).model_dump(mode="json")}


def lookup_stock(state: AgentState) -> AgentState:
    query = tools.StockQuery.model_validate(state["plan"]["inventory"])
    return {"stock_result": tools.lookup_stock(query).model_dump(mode="json")}


def ticket_fallback(state: AgentState) -> AgentState:
    """The ticket tool failed or found nothing: say so, never guess a status."""
    result = state["ticket_result"]
    ticket_id = result["query"].get("ticket_id")
    if result["status"] == "not_found":
        text = (f"No encontré el ticket #{ticket_id} en el gestor de incidentes. Revisa el número: no puedo "
                "confirmar su estado." if ticket_id else
                "No encontré tickets con esos criterios en el gestor de incidentes.")
    else:
        text = ((f"No pude confirmar el estado del ticket #{ticket_id} ahora mismo" if ticket_id else
                 "No pude consultar los tickets ahora mismo") +
                ": el gestor de incidentes no respondió a través del servidor MCP. Inténtalo de nuevo en unos minutos o revísalo en "
                "la sección Incidentes del backoffice.")
    return {"tool_fallbacks": [text]}


def stock_fallback(state: AgentState) -> AgentState:
    """The stock tool failed or the product is not in the catalog: say so, never guess stock."""
    result = state["stock_result"]
    product = result["query"]["product"]
    if result["status"] == "not_found":
        text = f"No encontré «{product}» en el inventario de HealthCore, así que no puedo confirmar si hay stock."
    else:
        text = (f"No pude consultar el stock de «{product}» ahora mismo: el inventario no respondió. "
                "Inténtalo de nuevo en unos minutos.")
    return {"tool_fallbacks": [text]}


# ---------------------------------------------------------------- knowledge base (Part 1)


def knowledge_question(state: AgentState) -> str:
    """What the RAG is asked: the policy part chosen by the planner for mixed questions, or
    the whole question. The RAG never sees the ticket or the patient's case."""
    return (state.get("plan") or {}).get("knowledge_question") or state["question"]


def retrieve_context(state: AgentState) -> AgentState:
    """Search the knowledge base (Qdrant, threshold min_score) with the RAG retrieve()."""
    context = rag.retrieve(knowledge_question(state))
    return {"context": context, "status": "retrieved" if context else "no_context"}


def generate_answer(state: AgentState) -> AgentState:
    """Answer from the chunks retrieve_context produced, with the RAG generation step."""
    return {"rag_answer": rag.generate_answer(knowledge_question(state), state["context"])}


def answer_no_information(state: AgentState) -> AgentState:
    """No chunk passed the similarity threshold: answer honestly instead of forcing the model
    to generate over an empty context. Deterministic, so it can never invent company data."""
    return {"rag_answer": NO_INFORMATION_ANSWER}


# ---------------------------------------------------------------- answer


def _date(value: str) -> str:
    return datetime.fromisoformat(value).strftime("%d/%m/%Y")


def format_tickets(result: dict) -> str:
    """Tool data rendered as text, without a model: the status shown is the one the API returned."""
    checked = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    tickets = result["tickets"]
    if result["query"].get("ticket_id"):
        t = tickets[0]
        body = (f"El ticket #{t['id']} «{t['title']}» está en estado {STATUS_LABELS[t['status']]}. "
                f"Categoría: {CATEGORY_LABELS.get(t['category'], t['category'])}; origen: "
                f"{ORIGIN_LABELS.get(t['origin'], t['origin'])}; sede: {BRANCH_LABELS.get(t['branch'], t['branch'])}. "
                f"Creado el {_date(t['created_at'])} y actualizado por última vez el {_date(t['updated_at'])}.")
    else:
        lines = [f"- #{t['id']} «{t['title']}» — {STATUS_LABELS[t['status']]} · "
                 f"{BRANCH_LABELS.get(t['branch'], t['branch'])} · actualizado el {_date(t['updated_at'])}"
                 for t in tickets]
        shown = f"; estos son los {len(tickets)} más recientes" if result["total"] > len(tickets) else ""
        body = f"Hay {result['total']} ticket{'s' if result['total'] != 1 else ''} con esos criterios{shown}:\n" + "\n".join(lines)
    return f"{body}\n\nFuente: gestor de incidentes vía servidor MCP ({result['endpoint']}), consultado en vivo el {checked}."


def format_stock(result: dict) -> str:
    checked = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    lines = [f"- {p['name']} (SKU {p['sku']}, {COUNTRY_LABELS[p['country']]}): {p['current_stock']} "
             f"{UNIT_LABELS.get(p['unit'], p['unit'])} en stock en la red." for p in result["products"]]
    return "Stock actual:\n" + "\n".join(lines) + f"\n\nFuente: inventario vía servidor MCP ({result['endpoint']}), consultado en vivo el {checked}."


def compose_answer(state: AgentState) -> AgentState:
    """Join the parts in the order the sources were consulted: tools, then knowledge base."""
    parts = []
    if (state.get("ticket_result") or {}).get("status") == "ok":
        parts.append(format_tickets(state["ticket_result"]))
    if (state.get("stock_result") or {}).get("status") == "ok":
        parts.append(format_stock(state["stock_result"]))
    parts.extend(state.get("tool_fallbacks") or [])
    if state.get("rag_answer"):
        parts.append(state["rag_answer"])
    return {"answer": "\n\n".join(parts), "status": "answered"}
