"""State that travels between the nodes of the agent graph.

Minimal on purpose: only what a node needs to decide the next step. No conversation
history: every question is answered on its own (the RAG has no memory), so carrying
previous turns would only grow the checkpoints and risk mixing one patient's context into
another coordinator's question. Tool credentials are never part of the state.
"""

import operator
from typing import Annotated, Any, Literal, TypedDict

Status = Literal["received", "rejected", "planned", "retrieved", "no_context", "answered"]


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
    question: str  # input; normalized by receive_question
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
