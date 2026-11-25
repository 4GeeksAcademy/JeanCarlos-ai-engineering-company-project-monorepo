from pydantic import BaseModel, ConfigDict, Field, field_validator


class KnowledgeQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=3, max_length=1000)

    @field_validator("question")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 3:
            raise ValueError("La pregunta no puede estar vacía.")
        return value


class KnowledgeAnswer(BaseModel):
    """Only the model-generated answer: no chunks, scores or Qdrant ids reach the client."""

    answer: str
