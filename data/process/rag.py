"""RAG knowledge base, phase 1: prepare the documents and index them in Qdrant.

    python -m data.process.rag          # (re)index docs/company-knowledge-base/ into Qdrant

Two public entry points, one responsibility each:

- setup(): read the source documents of the HealthCore BRIEFING (Hito 7) from
  docs/company-knowledge-base/, split them into semantic chunks and upsert them into the
  Qdrant collection `healthcore_knowledge`.
- embed(text): the single embedding function, shared by indexing (here) and querying
  (data/pipelines/rag.py). It uses the dedicated embedding model (EMBEDDING_MODEL), never
  the generation model.

Idempotency: point ids are deterministic (uuid5 of company + source_document + chunk_index),
so re-running setup() overwrites the same points instead of adding new ones; points left
over from a previous version of a document (e.g. it now has fewer chunks) are deleted.

Configuration (environment, or services/api/.env): LLM_API_URL, LLM_API_KEY,
EMBEDDING_MODEL, GENERATION_MODEL (must differ from EMBEDDING_MODEL), QDRANT_URL
(default http://localhost:6333), QDRANT_API_KEY (optional, Qdrant Cloud).
"""

from __future__ import annotations

import logging
import os
import re
import sys
import unicodedata
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_BASE_DIR = REPO_ROOT / "docs" / "company-knowledge-base"

# Values from the HealthCore BRIEFING (Hito 7): collection name, company, payload enums.
COLLECTION = "healthcore_knowledge"
COMPANY = "healthcore"
SOURCE_DOCUMENTS = {
    "healthcore-insurance-coverage.es.md": "insurance-coverage",
    "healthcore-appointment-policy.es.md": "appointment-policy",
    "healthcore-referral-process.es.md": "referral-process",
    "healthcore-new-patient-checklist.es.md": "new-patient-checklist",
}
MAX_CHUNK_CHARS = 1200  # safety net only: the HealthCore sections are 150-600 characters
POINT_NAMESPACE = uuid.UUID("6f1c2a4e-8d3b-5e7f-9a0c-1b2d3e4f5a6b")

logger = logging.getLogger("rag.setup")


class RagConfigError(RuntimeError):
    """The RAG models or Qdrant are not configured."""


# ---------------------------------------------------------------- configuration


@dataclass(frozen=True)
class RagSettings:
    api_url: str
    api_key: str
    embedding_model: str
    generation_model: str
    qdrant_url: str
    qdrant_api_key: str | None


@lru_cache(maxsize=1)
def settings() -> RagSettings:
    """Read the configuration once. The embedding and generation models are separate ids."""
    try:
        from dotenv import load_dotenv

        load_dotenv(REPO_ROOT / "services" / "api" / ".env")
    except ImportError:  # pragma: no cover - python-dotenv is a project dependency
        pass
    values = {name: os.environ.get(name, "").strip()
              for name in ("LLM_API_URL", "LLM_API_KEY", "EMBEDDING_MODEL", "GENERATION_MODEL")}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise RagConfigError(f"Missing RAG configuration: {', '.join(missing)}")
    if values["EMBEDDING_MODEL"] == values["GENERATION_MODEL"]:
        raise RagConfigError("EMBEDDING_MODEL and GENERATION_MODEL must be different models")
    return RagSettings(
        api_url=values["LLM_API_URL"].rstrip("/"),
        api_key=values["LLM_API_KEY"],
        embedding_model=values["EMBEDDING_MODEL"],
        generation_model=values["GENERATION_MODEL"],
        qdrant_url=os.environ.get("QDRANT_URL", "http://localhost:6333"),
        qdrant_api_key=os.environ.get("QDRANT_API_KEY") or None,
    )


@lru_cache(maxsize=1)
def llm_client():
    """OpenAI-compatible client for the 4Geeks gateway (used for embeddings and chat)."""
    from openai import OpenAI

    config = settings()
    return OpenAI(base_url=config.api_url, api_key=config.api_key, timeout=30.0, max_retries=2)


@lru_cache(maxsize=1)
def qdrant_client():
    from qdrant_client import QdrantClient

    config = settings()
    return QdrantClient(url=config.qdrant_url, api_key=config.qdrant_api_key, timeout=10)


# ---------------------------------------------------------------- embeddings


def normalize_text(text: str) -> str:
    """Same preprocessing for chunks and questions: Unicode NFC (accents written either way
    become identical), Markdown list markers dropped, whitespace collapsed."""
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"^\s*(?:[-*]|\d+\.)\s+", "", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip()


def embed(text: str) -> list[float]:
    """Vector for `text` with the dedicated embedding model (EMBEDDING_MODEL)."""
    cleaned = normalize_text(text)
    if not cleaned:
        raise ValueError("cannot embed empty text")
    response = llm_client().embeddings.create(model=settings().embedding_model, input=cleaned)
    return list(response.data[0].embedding)


# ---------------------------------------------------------------- chunking


@dataclass(frozen=True)
class Chunk:
    source_document: str
    title: str
    section: str
    chunk_index: int
    text: str

    def embedding_text(self) -> str:
        """Text sent to embed(): the chunk with its document and section as a header, so a
        short rule ("sin cargo") still carries what it is about."""
        return f"{self.title}. {self.section}.\n{self.text}"

    def payload(self) -> dict[str, Any]:
        return {
            "company": COMPANY,
            "source_document": self.source_document,
            "section": self.section,
            "language": "es",
            "chunk_index": self.chunk_index,
            "text": self.text,
        }


_LIST_ITEM = re.compile(r"^\s*(?:[-*]|\d+\.)\s+")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$")
_LABEL_LINE = re.compile(r"^([^:]{3,90}):$")  # "Política de cancelación:" alone on its line
_INLINE_LABEL = re.compile(r"^([^:.]{3,60}):\s+\S")  # "Recordatorios automáticos: el sistema…"


def _unwrap(block: list[str]) -> str:
    """Join hard-wrapped lines: list items start a new line, continuation lines are glued
    to the previous line. Nothing is cut or reordered."""
    lines: list[str] = []
    for raw in block:
        line = raw.strip()
        if lines and not _LIST_ITEM.match(raw) and not lines[-1].endswith(":"):
            lines[-1] = f"{lines[-1]} {line}"
        else:
            lines.append(line)
    return "\n".join(lines)


def _label(block_text: str) -> str | None:
    first = block_text.split("\n", 1)[0]
    if match := _LABEL_LINE.match(first):
        return match.group(1).strip()
    if (match := _INLINE_LABEL.match(first)) and len(match.group(1).split()) <= 6:
        return match.group(1).strip()
    return None


def _split_oversized(text: str) -> list[str]:
    """Safety net for long sections: split between list items, then between sentences,
    never inside one."""
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]
    units = [u for u in re.split(r"\n(?=(?:[-*]|\d+\.)\s)|(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÑ¿¡])", text) if u.strip()]
    parts, current = [], ""
    for unit in units:
        if current and len(current) + len(unit) + 1 > MAX_CHUNK_CHARS:
            parts.append(current)
            current = unit
        else:
            current = f"{current}\n{unit}" if current else unit
    return parts + ([current] if current else [])


def chunk_document(file_name: str, markdown: str) -> list[Chunk]:
    """Split one source document into self-contained semantic units.

    - Blocks are separated by blank lines; Markdown headings (#, ##) start a new section.
    - A block that ends with ":" introduces what follows ("Política de cancelación:",
      "el proceso estándar es:"), so it is kept together with the next block. Rules and
      their conditions (the no-show fee and its Medicare/Medicaid exception) never split.
    - Section = the label of the block ("Reino Unido (Londres y Manchester)",
      "Recordatorios automáticos") or, for an unlabeled paragraph, the nearest heading
      (the document title).
    """
    source_document = SOURCE_DOCUMENTS[file_name]
    title, heading = file_name, None
    groups: list[tuple[str | None, list[str]]] = []  # (heading, blocks)
    blocks: list[str] = []
    current: list[str] = []

    def close_block() -> None:
        nonlocal current
        if current:
            blocks.append(_unwrap(current))
            current = []

    def close_group() -> None:
        nonlocal blocks
        close_block()
        if blocks:
            groups.append((heading, blocks))
            blocks = []

    for raw in unicodedata.normalize("NFC", markdown).splitlines():
        if match := _HEADING.match(raw.strip()):
            close_group()
            if len(match.group(1)) == 1:
                title = heading = match.group(2)
            else:
                heading = match.group(2)
            continue
        if not raw.strip():
            close_block()
        else:
            current.append(raw)
    close_group()

    chunks: list[Chunk] = []
    for group_heading, group_blocks in groups:
        merged: list[list[str]] = []
        for block in group_blocks:
            if merged and merged[-1][-1].rstrip().endswith(":"):
                merged[-1].append(block)
            else:
                merged.append([block])
        for unit in merged:
            labels = [label for block in unit if (label := _label(block))]
            section = labels[-1] if labels else (group_heading or title)
            for text in _split_oversized("\n".join(unit)):
                chunks.append(Chunk(source_document, title, section, len(chunks), text))
    return chunks


def load_chunks(directory: Path = KNOWLEDGE_BASE_DIR) -> list[Chunk]:
    """Chunks of the four BRIEFING documents. A missing document is an error, not a skip."""
    chunks: list[Chunk] = []
    for file_name in SOURCE_DOCUMENTS:
        path = directory / file_name
        if not path.is_file():
            raise FileNotFoundError(f"{path} is missing: copy it from 00-general-contexts/healthcore/")
        chunks.extend(chunk_document(file_name, path.read_text(encoding="utf-8")))
    return chunks


def point_id(chunk: Chunk) -> str:
    return str(uuid.uuid5(POINT_NAMESPACE, f"{COMPANY}:{chunk.source_document}:{chunk.chunk_index}"))


# ---------------------------------------------------------------- indexing


def ensure_collection(client, vector_size: int) -> None:
    """Create the collection, or recreate it if the vector size changed (new model)."""
    from qdrant_client.models import Distance, PayloadSchemaType, VectorParams

    if client.collection_exists(COLLECTION):
        params = client.get_collection(COLLECTION).config.params.vectors
        if params.size == vector_size and params.distance == Distance.COSINE:
            return
        logger.warning("Recreating %s: vector size %s -> %s", COLLECTION, params.size, vector_size)
        client.delete_collection(COLLECTION)
    client.create_collection(COLLECTION, vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE))
    client.create_payload_index(COLLECTION, "source_document", PayloadSchemaType.KEYWORD)


def setup(directory: Path = KNOWLEDGE_BASE_DIR, client=None) -> dict[str, Any]:
    """Index docs/company-knowledge-base/ into Qdrant. Safe to run any number of times."""
    from qdrant_client.models import PointIdsList, PointStruct

    client = client or qdrant_client()
    chunks = load_chunks(directory)
    vectors = [embed(chunk.embedding_text()) for chunk in chunks]
    ensure_collection(client, len(vectors[0]))
    points = [PointStruct(id=point_id(chunk), vector=vector, payload=chunk.payload())
              for chunk, vector in zip(chunks, vectors)]
    client.upsert(COLLECTION, points=points, wait=True)

    current = {point.id for point in points}
    stale, offset = [], None
    while True:
        records, offset = client.scroll(COLLECTION, limit=256, offset=offset, with_payload=False, with_vectors=False)
        stale += [record.id for record in records if str(record.id) not in current]
        if offset is None:
            break
    if stale:
        client.delete(COLLECTION, points_selector=PointIdsList(points=stale), wait=True)

    per_document: dict[str, int] = {}
    for chunk in chunks:
        per_document[chunk.source_document] = per_document.get(chunk.source_document, 0) + 1
    summary = {
        "collection": COLLECTION,
        "points": client.count(COLLECTION, exact=True).count,
        "chunks_per_document": per_document,
        "vector_size": len(vectors[0]),
        "stale_points_deleted": len(stale),
    }
    logger.info("setup %s", summary)
    return summary


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    try:
        summary = setup()
    except RagConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
