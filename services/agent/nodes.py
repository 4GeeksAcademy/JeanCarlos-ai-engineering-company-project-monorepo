"""Nodes of the agent graph: one responsibility each.

The RAG logic is reused from data/pipelines/rag.py, not rewritten: retrieve_context calls
retrieve() and generate_answer calls generate_answer(question, context) with the chunks
that retrieve_context already produced. No node calls the monolithic query(), so retrieval
runs exactly once per question and is visible in the trace as its own step.
"""

from __future__ import annotations

from data.pipelines import rag

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
    "retrieve_context": frozenset({"context", "status"}),
    "generate_answer": frozenset({"answer", "status"}),
    "answer_no_information": frozenset({"answer", "status"}),
}


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
    """Terminal node for invalid input: retrieval and generation never run."""
    return {"status": "rejected", "error": state.get("error") or "La pregunta no es válida."}


def retrieve_context(state: AgentState) -> AgentState:
    """Search the knowledge base (Qdrant, threshold min_score) with the RAG retrieve()."""
    context = rag.retrieve(state["question"])
    return {"context": context, "status": "retrieved" if context else "no_context"}


def generate_answer(state: AgentState) -> AgentState:
    """Answer from the chunks retrieve_context produced, with the RAG generation step."""
    return {"answer": rag.generate_answer(state["question"], state["context"]), "status": "answered"}


def answer_no_information(state: AgentState) -> AgentState:
    """No chunk passed the similarity threshold: answer honestly instead of forcing the model
    to generate over an empty context. Deterministic, so it can never invent company data."""
    return {"answer": NO_INFORMATION_ANSWER, "status": "answered"}
