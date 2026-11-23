"""State that travels between the nodes of the agent graph.

Minimal on purpose: only what a node needs to decide the next step. Still no conversation
history: what the agent learns across conversations lives in the explicit memory store
(services/agent/memory), read by recall_memories and written only after an approved proposal,
never by appending turns to the state or the prompt. Tool credentials and PHI never enter.
"""

import operator
from typing import Annotated, Any, Literal, TypedDict

Status = Literal["received", "rejected", "phi_refused", "guarded", "decided", "planned", "retrieved", "no_context", "answered"]


class RetrievedChunk(TypedDict, total=False):
    """A payload returned by data.pipelines.rag.retrieve() (BRIEFING §3) plus its score."""

    company: str
    source_document: str
    section: str
    language: str
    chunk_index: int
    text: str
    score: float


class AgentState(TypedDict, total=False):
    question: str  # input; normalized by receive_question (already PHI-screened by the runner)
    run_id: str  # input: links guardrail events to the run's trace
    conversation_id: str  # input: memory proposals live per conversation
    actor: str  # input: authenticated staff user (who approves a memory)
    phi_categories: list[str]  # input: PHI found by the runner's guard (the text never enters)
    pending_proposal: dict[str, Any] | None  # input: the open memory proposal of this conversation
    memory_decision: dict[str, Any]  # resolve_pending_proposal: label, status, content, memory id
    memories: list[dict[str, Any]]  # recall_memories: approved memories relevant to the question
    memory_proposal: dict[str, Any]  # self_evaluate: proposed / blocked_phi / none
    guard: dict[str, Any]  # guard_input: deterministic verdict (category, failure type, rules that fired)
    scope: dict[str, Any]  # classify_scope: domain / general / personal_task / instruction_override
    output_check: dict[str, Any]  # guard_output: ok, or the findings that replaced the answer
    status: Status  # what the last node concluded
    error: str  # why the question was rejected
    plan: dict[str, Any]  # SourcePlan: which sources the question needs (plan_sources)
    ticket_result: dict[str, Any]  # TicketLookupResult of lookup_tickets (incident manager)
    stock_result: dict[str, Any]  # StockLookupResult of lookup_stock (inventory)
    tool_fallbacks: Annotated[list[str], operator.add]  # honest answers of failed tools
    context: list[RetrievedChunk]  # output of retrieve_context, input of generate_answer
    rag_answer: str  # knowledge-base part of the answer (generated, or "no information")
    answer: str  # final answer, composed from the parts above


STATE_KEYS = frozenset(AgentState.__annotations__)
