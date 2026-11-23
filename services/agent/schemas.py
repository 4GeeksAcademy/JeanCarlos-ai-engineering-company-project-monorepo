from pydantic import BaseModel, ConfigDict, Field


class AgentQuestion(BaseModel):
    """No minimum length here on purpose: an empty question is routed by the graph itself
    (receive_question → reject_question) and answered with a clear 422."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(max_length=2000)


class AgentAnswer(BaseModel):
    answer: str  # model-generated (or the honest "no information" answer); never raw chunks
    run_id: str  # thread id of the run: its trace and checkpoints
