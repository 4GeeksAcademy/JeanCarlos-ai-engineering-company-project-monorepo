from pydantic import BaseModel, ConfigDict, Field


class AgentQuestion(BaseModel):
    """No minimum length here on purpose: an empty question is routed by the graph itself
    (receive_question → reject_question) and answered with a clear 422."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(max_length=2000)
    conversation_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                                        description="Send it back to continue the conversation (memory proposals).")


class MemoryProposal(BaseModel):
    id: str
    kind: str
    content: str


class MemoryDecision(BaseModel):
    proposal_id: str
    status: str  # approved, approved_edited, rejected, discarded_ambiguous, discarded_topic_change, blocked_phi
    content: str | None = None


class MemoryInfo(BaseModel):
    proposal: MemoryProposal | None = None  # pending: answer it in the next message
    decision: MemoryDecision | None = None  # what happened to the previous proposal this turn
    blocked_phi: bool = False


class AgentAnswer(BaseModel):
    answer: str  # model-generated (or the honest "no information" answer); never raw chunks
    run_id: str  # thread id of the run: its trace and checkpoints
    conversation_id: str
    memory: MemoryInfo
