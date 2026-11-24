"""Source planning: which sources a question needs (knowledge base, ticket tool, stock
tool, or several). The coordinator never says which one: the agent decides.

1. The generation model is asked for a JSON plan (no answer), validated against the tool
   contracts and the incident catalog. A ticket id is only accepted if it is written in
   the question, so the model cannot invent one.
2. If the model fails or returns an invalid plan, deterministic rules decide instead.
The trace records which planner decided and why.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from data.process.rag import llm_client, settings
from packages.shared.incidents import BRANCHES, CATEGORIES, ORIGINS, STATUSES

from .tools import StockQuery, TicketQuery


class SourcePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_base: bool
    # The policy part of the question alone, when the question also needs a tool: the RAG
    # answers the policy, not the coordinator's case (no ticket or patient details).
    knowledge_question: str | None = Field(default=None, max_length=300)
    ticket: TicketQuery | None = None
    inventory: StockQuery | None = None
    planner: Literal["model", "rules"] = "model"
    note: str | None = None


PLANNER_PROMPT = f"""Eres el enrutador del asistente de los coordinadores de pacientes de HealthCore. \
No respondas la pregunta: decide qué fuentes necesita y devuelve SOLO un objeto JSON.

Fuentes disponibles:
- "knowledge_base": políticas y procedimientos documentados (seguros aceptados, citas y \
cancelaciones, no-shows, referencias internas, checklist y documentos de pacientes nuevos).
- "ticket": datos en vivo del gestor de incidentes (estado, categoría, origen, sede y fechas de \
un ticket por su número, o listados filtrados de tickets).
- "inventory": stock actual en vivo de un producto o insumo médico del inventario.

Formato exacto:
{{"knowledge_base": true|false,
 "knowledge_question": null | "solo la parte de política o procedimiento, como pregunta independiente",
 "ticket": null | {{"ticket_id": entero|null, "status": valor|null, "category": valor|null, "origin": valor|null, "branch": valor|null}},
 "inventory": null | {{"product": "producto tal como lo nombra la pregunta"}}}}

Valores permitidos: status {list(STATUSES)}; category {list(CATEGORIES)}; origin {list(ORIGINS)}; \
branch {list(BRANCHES)}.

Reglas:
- "ticket" solo si la pregunta pide datos de tickets o incidencias concretas (un número) o un listado \
de tickets. Nunca inventes un ticket_id que no esté escrito en la pregunta.
- "inventory" solo si pregunta por stock, existencias o disponibilidad de un producto.
- "knowledge_base": true si pregunta por una política o un procedimiento. Si no aplica ninguna tool, \
knowledge_base es true.
- Puede necesitar varias fuentes a la vez. Si knowledge_base es true y además usa una tool, escribe en \
"knowledge_question" solo la parte de política como pregunta general (sin el número de ticket ni detalles \
del caso o del paciente); si solo usa knowledge_base, deja "knowledge_question" en null."""


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _check(plan: SourcePlan, question: str) -> SourcePlan:
    if plan.ticket and plan.ticket.ticket_id is not None and str(plan.ticket.ticket_id) not in question:
        raise ValueError(f"ticket_id {plan.ticket.ticket_id} is not in the question")
    if not plan.ticket and not plan.inventory and not plan.knowledge_base:
        plan = plan.model_copy(update={"knowledge_base": True})
    if not plan.knowledge_base or not (plan.ticket or plan.inventory):
        plan = plan.model_copy(update={"knowledge_question": None})  # only for mixed questions
    return plan


def plan_with_model(question: str) -> SourcePlan:
    response = llm_client().chat.completions.create(
        model=settings().generation_model, temperature=0,
        messages=[{"role": "system", "content": PLANNER_PROMPT}, {"role": "user", "content": question}],
    )
    content = response.choices[0].message.content or ""
    match = re.search(r"\{.*\}", content, re.S)
    if not match:
        raise ValueError("the planner did not return JSON")
    raw: dict[str, Any] = json.loads(match.group(0))
    ticket = raw.get("ticket")
    if isinstance(ticket, dict):
        ticket = {k: v for k, v in ticket.items() if v is not None}
    knowledge_question = raw.get("knowledge_question") or None
    plan = SourcePlan(knowledge_base=bool(raw.get("knowledge_base")), ticket=ticket or None,
                      inventory=raw.get("inventory") or None, planner="model",
                      knowledge_question=knowledge_question if isinstance(knowledge_question, str) else None)
    return _check(plan, question)


# ---------------------------------------------------------------- deterministic rules

_TICKET_ID = re.compile(r"(?:ticket|tiquet|tique|incidente|incidencia|caso|reporte)s?\s*"
                        r"(?:n(?:umero|o|º|°)?\.?\s*)?#?\s*(\d{1,9})\b|#(\d{1,9})\b")
_TICKET_WORDS = re.compile(r"\b(tickets?|incidentes?|incidencias?)\b")
_STATUS_WORDS = {"abiert": "open", "en progreso": "in_progress", "en curso": "in_progress",
                 "resuelt": "resolved", "cerrad": "resolved", "descartad": "discarded"}
_STOCK = re.compile(r"\b(stock|existencias?|inventario|unidades disponibles)\b")
_STOCK_PRODUCT = re.compile(r"(?:stock|existencias?|inventario|unidades)\s+(?:disponibles\s+)?(?:de|del|para)?\s*"
                            r"([^?¿!.,]+)")
_POLICY = re.compile(r"\b(segur|cobertur|cancel|cita|cargo|no-?show|referenci|especialista|paciente nuevo|"
                     r"primera|politica|procedimiento|medicare|medicaid|nhs|self-pay|consentimiento|traer)")


def plan_with_rules(question: str, note: str | None = None) -> SourcePlan:
    text = _plain(question)
    ticket = None
    if match := _TICKET_ID.search(text):
        ticket = TicketQuery(ticket_id=int(match.group(1) or match.group(2)))
    elif _TICKET_WORDS.search(text):
        status = next((value for word, value in _STATUS_WORDS.items() if word in text), None)
        branch = next((b for b in BRANCHES if b.replace("_", " ") in text), None)
        if status or branch:
            ticket = TicketQuery(status=status, branch=branch)
    inventory = None
    if _STOCK.search(text) and (match := _STOCK_PRODUCT.search(text)) and len(match.group(1).strip()) >= 2:
        inventory = StockQuery(product=match.group(1).strip()[:80])
    knowledge_base = bool(_POLICY.search(text)) or not (ticket or inventory)
    return SourcePlan(knowledge_base=knowledge_base, ticket=ticket, inventory=inventory, planner="rules", note=note)


def plan_sources(question: str) -> SourcePlan:
    try:
        return plan_with_model(question)
    except (ValidationError, ValueError, KeyError) as error:
        return plan_with_rules(question, note=f"model plan rejected: {type(error).__name__}: {str(error)[:160]}")
    except Exception as error:  # gateway down or timeout: the agent still routes
        return plan_with_rules(question, note=f"model unavailable: {type(error).__name__}")
