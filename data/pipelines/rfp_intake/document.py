"""PDF → Markdown (MarkItDown) and readability metrics (py-readability-metrics).

The PDF is converted as soon as it is received: no agent ever reads the PDF, only the
Markdown, which is far cheaper in tokens. The readability metrics are computed on that Markdown
to anticipate the processing cost (length, density), not as a literary grade.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MIN_WORDS_FOR_READABILITY = 100  # py-readability-metrics requires at least 100 words
CHARS_PER_TOKEN = 4  # rough estimate for English/Spanish text with the gateway tokenizers


@dataclass
class Paragraph:
    id: int
    text: str


def pdf_to_markdown(path: str) -> str:
    from markitdown import MarkItDown

    text = MarkItDown(enable_plugins=False).convert(path).text_content or ""
    return clean_markdown(text)


def clean_markdown(text: str) -> str:
    text = text.replace("\x0c", "\n")
    text = re.sub(r"\(cid:\d+\)\s*", "- ", text)  # PDF bullet glyphs MarkItDown cannot map
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def paragraphs(markdown: str) -> list[Paragraph]:
    """Numbered paragraphs: the orchestrator refers to them by id, the workers get only theirs."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", markdown) if b.strip()]
    return [Paragraph(i, " ".join(b.split())) for i, b in enumerate(blocks, 1)]


def pdf_pages(path: str) -> int | None:
    try:
        import pypdfium2

        return len(pypdfium2.PdfDocument(path))
    except Exception:  # metrics are best effort; never block the intake
        return None


@dataclass
class DocumentMetrics:
    words: int
    sentences: int | None
    characters: int
    estimated_tokens: int
    pages: int | None = None
    flesch_kincaid_grade: float | None = None
    flesch_reading_ease: float | None = None
    gunning_fog: float | None = None
    smog: float | None = None
    cost_band: str = "low"
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _ensure_punkt() -> None:
    import nltk

    for resource in ("tokenizers/punkt_tab", "tokenizers/punkt"):
        try:
            nltk.data.find(resource)
            return
        except LookupError:
            continue
    nltk.download("punkt_tab", quiet=True)


def compute_metrics(markdown: str, pages: int | None = None) -> DocumentMetrics:
    plain = re.sub(r"[#*_`>|-]+", " ", markdown)
    words = len(re.findall(r"\b\w+\b", plain))
    metrics = DocumentMetrics(words=words, sentences=None, characters=len(markdown),
                              estimated_tokens=max(1, len(markdown) // CHARS_PER_TOKEN), pages=pages)
    if words < MIN_WORDS_FOR_READABILITY:
        metrics.notes.append(f"menos de {MIN_WORDS_FOR_READABILITY} palabras: sin índices de legibilidad")
    else:
        try:
            _ensure_punkt()
            from readability import Readability

            r = Readability(plain)
            metrics.sentences = r.statistics().get("num_sentences")
            metrics.flesch_kincaid_grade = round(r.flesch_kincaid().score, 2)
            metrics.flesch_reading_ease = round(r.flesch().score, 2)
            metrics.gunning_fog = round(r.gunning_fog().score, 2)
            try:
                metrics.smog = round(r.smog(all_sentences=True).score, 2)
            except Exception:
                metrics.notes.append("SMOG requiere 30 frases")
        except Exception as error:  # never block the intake on metrics
            metrics.notes.append(f"legibilidad no disponible: {type(error).__name__}")
    metrics.cost_band = cost_band(metrics)
    return metrics


def cost_band(m: DocumentMetrics) -> str:
    """Anticipated processing cost: tokens per LLM call x density of the text."""
    dense = (m.gunning_fog or 0) >= 16 or (m.flesch_kincaid_grade or 0) >= 14
    if m.estimated_tokens > 12000 or (m.estimated_tokens > 6000 and dense):
        return "high"
    if m.estimated_tokens > 3000 or dense:
        return "medium"
    return "low"
