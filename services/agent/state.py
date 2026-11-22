"""State that travels between the nodes of the agent graph.

Minimal on purpose: only what a node needs to decide the next step. No conversation
history: every question is answered on its own from the knowledge base (the RAG has no
memory), so carrying previous turns would only grow the checkpoints and risk mixing one
patient's context into another coordinator's question.
"""

from typing import Literal, TypedDict

Status = Literal["received", "rejected", "retrieved", "no_context", "answered"]


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
    status: Status  # what the last node concluded; the conditional edges read it
    context: list[RetrievedChunk]  # output of retrieve_context, input of generate_answer
    answer: str  # final answer (generated, or the honest "no information" answer)
    error: str  # why the question was rejected


STATE_KEYS = frozenset(AgentState.__annotations__)
