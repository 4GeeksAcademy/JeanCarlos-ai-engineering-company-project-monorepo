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

import re
from datetime import datetime, timezone

from data.pipelines import rag
from packages.shared.incidents import BRANCH_LABELS

from . import planner, tools
from .harness import isolation, output_guard, prompts, rules, scope
from .harness.events import get_events
from .memory import llm as memory_llm
from .memory.store import KINDS, MemoryPolicyError, Proposal, get_store
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
    "refuse_phi": frozenset({"answer", "status", "memory_decision"}),
    "guard_input": frozenset({"guard", "status"}),
    "refuse_guarded": frozenset({"answer", "status", "memory_decision"}),
    "classify_scope": frozenset({"scope"}),
    "answer_general": frozenset({"answer", "status"}),
    "guard_output": frozenset({"answer", "output_check"}),
    "resolve_pending_proposal": frozenset({"question", "status", "memory_decision"}),
    "recall_memories": frozenset({"memories"}),
    "plan_sources": frozenset({"plan", "status"}),
    "lookup_tickets": frozenset({"ticket_result"}),
    "ticket_fallback": frozenset({"tool_fallbacks"}),
    "lookup_stock": frozenset({"stock_result"}),
    "stock_fallback": frozenset({"tool_fallbacks"}),
    "retrieve_context": frozenset({"context", "status"}),
    "generate_answer": frozenset({"rag_answer"}),
    "answer_no_information": frozenset({"rag_answer"}),
    "compose_answer": frozenset({"answer", "status"}),
    "self_evaluate": frozenset({"answer", "memory_proposal"}),
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
    """Normalize the coordinator's message and decide whether it can be answered. With a pending
    memory proposal a short reply ("sí", "no") is valid: it answers the proposal."""
    question = " ".join(str(state.get("question") or "").split())
    minimum = 1 if state.get("pending_proposal") else MIN_QUESTION_CHARS
    if len(question) < minimum:
        return {"question": question, "status": "rejected", "error": "La pregunta está vacía o es demasiado corta."}
    if len(question) > MAX_QUESTION_CHARS:
        return {"question": question[:MAX_QUESTION_CHARS], "status": "rejected",
                "error": f"La pregunta supera los {MAX_QUESTION_CHARS} caracteres."}
    return {"question": question, "status": "received"}


def _event(state: AgentState, guard: str, action: str, failure_type: str, category: str, **details) -> None:
    get_events().record(guard, action, failure_type, category, run_id=state.get("run_id"),
                        conversation_id=state.get("conversation_id"), **details)


def reject_question(state: AgentState) -> AgentState:
    """Terminal node for invalid input: no source is consulted."""
    _event(state, "input_validation", "blocked", "structural", "invalid_input")
    return {"status": "rejected", "error": state.get("error") or "La pregunta no es válida."}


def plan_sources(state: AgentState) -> AgentState:
    """Decide which sources the question needs: knowledge base, ticket tool, stock tool."""
    plan = planner.plan_sources(state["question"])
    if plan.planner == "rules" and plan.note:  # the model's plan was malformed or unavailable
        _event(state, "planner_output", "fallback", "structural", "model_output_invalid", note=plan.note[:120])
    return {"plan": plan.model_dump(mode="json"), "status": "planned"}


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
    return (state.get("plan") or {}).get("knowledge_question") or state.get("question") or ""


def retrieve_context(state: AgentState) -> AgentState:
    """Search the knowledge base (Qdrant, threshold min_score) with the RAG retrieve()."""
    context = rag.retrieve(knowledge_question(state))
    return {"context": context, "status": "retrieved" if context else "no_context"}


def memory_context(state: AgentState) -> list[dict]:
    """Approved memories, shaped as context blocks for the generation prompt (cited as
    "Memoria del agente — aprobada por …"); they never replace documented policy."""
    return [{"source_document": "agent-memory", "chunk_index": -1,
             "section": f"aprobada por {m['approved_by']} el {m['created_at'][:10]}", "text": m["content"]}
            for m in state.get("memories") or []]


_REGULATORY = re.compile(r"\b(?:hipaa|gdpr|ico|phi|brecha\w*|breach\w*|baa|dpa|business\s+associate|data\s+processing|"
                         r"notificaci\w*|proteccion\s+de\s+datos|privacidad|permisible|permitido\s+compartir)\b", re.I)


def regulatory_context(state: AgentState) -> list[dict]:
    """The regulatory frame of the Compliance BRIEFING, for compliance questions the internal
    knowledge base does not cover yet."""
    return list(prompts.REGULATORY_BASELINE) if _REGULATORY.search(rules.plain(knowledge_question(state))) else []


def generate_answer(state: AgentState) -> AgentState:
    """Answer from the chunks retrieve_context produced (plus approved memories and the
    regulatory frame), with the RAG generation step and the compliance assistant's system prompt.
    External content is isolated first: a block carrying instructions never reaches the model."""
    blocks = state["context"] + memory_context(state) + regulatory_context(state)
    safe, findings = isolation.isolate_context(blocks)
    for finding in findings:
        _event(state, "external_content_isolation", "sanitized", "security", "instructions_in_external_content",
               source=finding["source"], rules=finding["rules"])
    return {"rag_answer": rag.generate_answer(knowledge_question(state), safe, system_prompt=prompts.AGENT_SYSTEM_PROMPT)}


def answer_no_information(state: AgentState) -> AgentState:
    """No chunk passed the similarity threshold: answer honestly instead of forcing the model
    to generate over an empty context. Deterministic, so it can never invent company data."""
    return {"rag_answer": NO_INFORMATION_ANSWER}


# ---------------------------------------------------------------- answer


def _date(value: str) -> str:
    return datetime.fromisoformat(value).strftime("%d/%m/%Y")


ACTIVE_BREACH_STATUSES = {"open", "in_progress"}


def _safe_tickets(state: AgentState, tickets: list[dict]) -> list[dict]:
    """Tool data is data: a title carrying instructions is replaced, and a compliance breach
    still open or under investigation shows no details (BRIEFING §3)."""
    safe = []
    for t in tickets:
        if t["category"] == "compliance_breach" and t["status"] in ACTIVE_BREACH_STATUSES:
            _event(state, "tool_output_filter", "blocked", "content", "active_breach_details", ticket_id=t["id"])
            safe.append({**t, "title": "incidente de cumplimiento en investigación: detalles reservados a Compliance",
                         "branch": "reservada"})
            continue
        title, injection = isolation.isolate_tool_text(t["title"])
        if injection:
            _event(state, "external_content_isolation", "sanitized", "security", "instructions_in_tool_data",
                   ticket_id=t["id"], rules=injection)
        safe.append({**t, "title": title})
    return safe


def format_tickets(result: dict, state: AgentState | None = None) -> str:
    """Tool data rendered as text, without a model: the status shown is the one the API returned."""
    checked = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    tickets = _safe_tickets(state or {}, result["tickets"])
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


DECISION_ACKS = {
    "approved": "Listo, lo recordaré: «{content}».",
    "approved_edited": "Listo, lo recordaré con tu corrección: «{content}».",
    "rejected": "Entendido, no lo guardo.",
    "discarded_ambiguous": ("No quedó claro si querías que lo recordara, así que no lo guardé. Si quieres, "
                            "vuelve a contármelo."),
    "discarded_topic_change": "(No guardé la propuesta de memoria anterior porque no la respondiste.)",
    "blocked_phi": ("No lo guardé: tu corrección parece incluir información de pacientes, y eso nunca se "
                    "guarda en la memoria (HIPAA y UK GDPR)."),
}


def compose_answer(state: AgentState) -> AgentState:
    """Join the parts: the outcome of the pending memory proposal (if any), then the sources in
    the order they were consulted: tools, then knowledge base."""
    parts = []
    decision = state.get("memory_decision") or {}
    if decision.get("status") in DECISION_ACKS:
        parts.append(DECISION_ACKS[decision["status"]].format(content=decision.get("content") or ""))
    if (state.get("ticket_result") or {}).get("status") == "ok":
        parts.append(format_tickets(state["ticket_result"], state))
    if (state.get("stock_result") or {}).get("status") == "ok":
        parts.append(format_stock(state["stock_result"]))
    parts.extend(state.get("tool_fallbacks") or [])
    if state.get("rag_answer"):
        parts.append(state["rag_answer"])
    return {"answer": "\n\n".join(parts), "status": "answered"}


# ---------------------------------------------------------------- memory (Hito 8)

PHI_REFUSAL = (
    "No puedo procesar ni recordar información que identifique a un paciente (nombre, historia clínica, fecha "
    "de nacimiento, número de seguro, o una combinación como edad + diagnóstico + clínica que permita "
    "reidentificarlo): en HealthCore está prohibido por HIPAA en EE. UU. y UK GDPR en Reino Unido, y no guardé "
    "nada. Reformula la consulta sin datos identificables, por ejemplo: «¿qué política aplica a un paciente adulto "
    "con un diagnóstico crónico en una clínica de EE. UU.?», y te respondo con la política correspondiente."
)


def refuse_phi(state: AgentState) -> AgentState:
    """The runner's guard found patient information in the message: refuse explicitly and record
    the attempt (categories only). A pending proposal is discarded, never approved."""
    store = get_store()
    categories = state.get("phi_categories") or ["patient_information"]
    _event(state, "patient_identifiers", "blocked", "content", "patient_identifiable_case", categories=categories)
    decision: dict = {}
    pending = state.get("pending_proposal")
    if pending:
        closed = store.resolve(pending["id"], label="ambiguous", confidence=0.0, classifier="phi_guard",
                               decided_by=state.get("actor") or "unknown", decision_message=state["question"])
        decision = {"proposal_id": pending["id"], "status": closed["status"], "label": "ambiguous"}
    store.record_blocked(conversation_id=state["conversation_id"], proposed_to=state.get("actor") or "unknown",
                         source_message=state["question"], categories=categories)
    return {"answer": PHI_REFUSAL, "status": "phi_refused", "memory_decision": decision}


def resolve_pending_proposal(state: AgentState) -> AgentState:
    """The conversation has an open proposal: classify this message against it first
    (approve / reject / edit / unrelated / ambiguous) and close it. Only approve and edit write
    memory; anything unclear discards it. What is left of the message continues as the question."""
    pending = state["pending_proposal"]
    decision = memory_llm.classify_decision(pending, state["question"])
    edited = decision.get("edited_content") or ""
    if decision["label"] == "edit" and (rules.detect_instruction_override(edited) or memory_llm.touches_documented_policy(edited)):
        _event(state, "memory_edit_guard", "blocked", "security", "poisoned_memory_edit")
        decision = {**decision, "label": "ambiguous", "classifier": "guardrail", "edited_content": None}
    closed = get_store().resolve(
        pending["id"], label=decision["label"], confidence=decision["confidence"], classifier=decision["classifier"],
        decided_by=state.get("actor") or "unknown", decision_message=state["question"],
        edited_content=decision.get("edited_content"))
    remaining = decision.get("remaining_question") or ""
    return {
        "question": " ".join(remaining.split()),
        "status": "decided",
        "memory_decision": {"proposal_id": pending["id"], "label": decision["label"], "model_label": decision.get("model_label"),
                            "confidence": decision["confidence"], "classifier": decision["classifier"],
                            "status": closed["status"], "content": closed["final_content"] or pending["content"],
                            "memory_id": closed["memory_id"]},
    }


def recall_memories(state: AgentState) -> AgentState:
    """Read approved memories relevant to the question (explicit read interface, never the prompt)."""
    try:
        memories = get_store().recall(state["question"], actor=state.get("actor"))
    except Exception:  # embeddings down: answer without memory rather than fail the run
        memories = []
    return {"memories": memories}


def self_evaluate(state: AgentState) -> AgentState:
    """After answering: is there something new or corrected worth remembering? One structured
    model call (services/agent/memory/llm.py) plus the PHI guard. A proposal is registered as
    pending and asked in the same answer; nothing is written to memory here."""
    answer = state["answer"]
    question = state.get("question") or ""
    store = get_store()
    if not question:  # this turn only answered the previous proposal
        return {"answer": answer, "memory_proposal": {"outcome": "skipped", "reason": "decision-only turn"}}
    if store.pending(state["conversation_id"]):
        return {"answer": answer, "memory_proposal": {"outcome": "skipped", "reason": "a proposal is already pending"}}
    evaluation = memory_llm.self_evaluate(question, answer, state.get("memories") or [])
    if evaluation.get("reason", "").startswith("self-evaluation unavailable"):
        _event(state, "self_evaluation_output", "fallback", "structural", "model_output_invalid")
    actor = state.get("actor") or "unknown"
    if evaluation["outcome"] == "blocked_phi":
        record = store.record_blocked(conversation_id=state["conversation_id"], proposed_to=actor,
                                      source_message=question, categories=evaluation["categories"])
        return {"answer": f"{answer}\n\n{PHI_REFUSAL}",
                "memory_proposal": {"outcome": "blocked_phi", "proposal_id": record["id"],
                                    "categories": evaluation["categories"]}}
    if evaluation["outcome"] != "propose" or evaluation["kind"] not in KINDS:
        return {"answer": answer, "memory_proposal": {"outcome": "none", "reason": evaluation.get("reason", "")}}
    try:
        record = store.propose(Proposal(kind=evaluation["kind"], content=evaluation["content"], reason=evaluation["reason"],
                                        scope_clinic=evaluation.get("scope_clinic"),
                                        scope_country=evaluation.get("scope_country"),
                                        scope_staff=evaluation.get("scope_staff")),
                               conversation_id=state["conversation_id"], proposed_to=actor, source_message=question)
    except MemoryPolicyError as error:
        return {"answer": answer, "memory_proposal": {"outcome": "none", "reason": f"policy: {error}"}}
    ask = (f"\n\n¿Quieres que recuerde esto para la próxima vez?\nPropuesta de memoria: «{record['content']}»\n"
           "Responde «sí» para guardarlo, «no» para descartarlo, o corrígelo con el texto que prefieras.")
    return {"answer": answer + ask,
            "memory_proposal": {"outcome": "proposed", "proposal_id": record["id"], "kind": record["kind"],
                                "content": record["content"], "reason": evaluation.get("reason", "")}}


# ---------------------------------------------------------------- harness (Hito 8 Parte 2)


def guard_input(state: AgentState) -> AgentState:
    """Deterministic guards on the whole message, before any model call: instruction override /
    jailbreak (security), personal-chatbot use, active-breach probing and confidential contract
    terms (content). A hit blocks; nothing here can allow what another layer blocks."""
    message = state["question"]
    conversation = state.get("conversation_id")
    events = get_events()
    checks = [
        ("instruction_override", "security", rules.detect_instruction_override(message)),
        ("personal_task", "content", rules.detect_personal_task(message)),
        ("breach_probe", "content", rules.detect_breach_probe(
            message, breach_topic_in_conversation=bool(conversation) and events.has_flag(conversation, "breach_topic"))),
        ("confidential_contract", "content", rules.detect_confidential_contract(message)),
    ]
    if conversation and rules.mentions_breach(message):
        events.set_flag(conversation, "breach_topic")
    for category, failure_type, fired in checks:
        if fired:
            attempts = (events.count(conversation, category) + 1) if conversation else 1
            _event(state, "input_rules", "blocked", failure_type, category, rules=fired, attempt=attempts)
            return {"guard": {"category": category, "failure_type": failure_type, "rules": fired,
                              "classifier": "rules", "attempt": attempts}, "status": "guarded"}
    return {"guard": {"category": "allowed", "classifier": "rules"}, "status": state.get("status", "received")}


def refuse_guarded(state: AgentState) -> AgentState:
    """Firm, fixed refusal (never generated, so it cannot be talked into complying). A pending
    memory proposal is discarded: an abusive turn never approves anything."""
    guard = state.get("guard") or {}
    scope_verdict = state.get("scope") or {}
    category = guard.get("category") if guard.get("category") not in (None, "allowed") else scope_verdict.get("category")
    answer = prompts.REFUSALS.get(category, prompts.REFUSALS["instruction_override"])
    if category == "instruction_override" and (guard.get("attempt") or 1) >= 3:
        answer += " Este es un intento repetido de cambiar mis instrucciones y queda registrado."
    decision: dict = {}
    pending = state.get("pending_proposal")
    if pending and get_store().pending(state["conversation_id"]):
        closed = get_store().resolve(pending["id"], label="ambiguous", confidence=0.0, classifier="guardrail",
                                     decided_by=state.get("actor") or "unknown", decision_message="[bloqueado por guardrail]")
        decision = {"proposal_id": pending["id"], "status": closed["status"], "label": "ambiguous"}
    return {"answer": answer, "status": "guarded", "memory_decision": decision}


def classify_scope(state: AgentState) -> AgentState:
    """Second layer for what the rules did not decide: domain, general (answer + redirect),
    personal task or instruction override (refuse)."""
    verdict = scope.classify_scope(state["question"])
    if verdict["classifier"].startswith("unavailable"):
        _event(state, "scope_classifier", "fallback", "structural", "model_output_invalid")
    elif verdict["category"] == "general":
        _event(state, "scope_classifier", "redirected", "content", "out_of_domain", confidence=verdict["confidence"])
    elif verdict["category"] in ("personal_task", "instruction_override"):
        failure = "security" if verdict["category"] == "instruction_override" else "content"
        _event(state, "scope_classifier", "blocked", failure, verdict["category"], confidence=verdict["confidence"])
    return {"scope": verdict}


def answer_general(state: AgentState) -> AgentState:
    """Small talk / general question: one or two sentences, then always back to compliance."""
    return {"answer": scope.answer_general(state["question"]), "status": "answered"}


def guard_output(state: AgentState) -> AgentState:
    """Last check before the user sees anything: format, leaks of the system prompt or internal
    secrets, and PHI in the model output (BRIEFING: detected, not only instructed)."""
    check = output_guard.check_output(state.get("answer"))
    if check.ok:
        return {"answer": state["answer"], "output_check": {"ok": True}}
    for failure_type, category in check.findings:
        _event(state, "output_guard", "blocked", failure_type, category.split(":")[0], detail=category)
    return {"answer": prompts.OUTPUT_FALLBACK,
            "output_check": {"ok": False, "findings": [f"{t}:{c}" for t, c in check.findings]}}
