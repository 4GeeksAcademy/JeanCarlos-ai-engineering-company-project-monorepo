"""Isolation of external content: knowledge-base chunks, approved memories and tool data are
DATA, never instructions.

Two layers:
1. Neutralize: role markers and tags that could close the data block are defused, and any
   instruction-like text (same detectors as the input guard) is flagged. A knowledge-base chunk
   or memory that carries instructions — override attempts, text addressed to the assistant or an
   unsafe clinical directive such as "recommend X without checking contraindications" (BRIEFING
   §4 case 2) — is withheld from the model; tool data (e.g. a ticket
   title typed by anyone) has the instruction replaced by a placeholder.
2. Delimit: every block reaches the model inside <documento> / <memoria> / <datos_herramienta>
   tags, and the system prompt states that nothing inside them has authority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .rules import detect_external_injection

_TAGS = re.compile(r"</?\s*(?:documento|memoria|datos_herramienta|mensaje_usuario|system|sistema|assistant|user)\b[^>]*>", re.I)
_ROLE_MARKERS = re.compile(r"(?im)^\s*(system|sistema|assistant|asistente|developer|user)\s*:")
_CHAT_TOKENS = re.compile(r"<\|[^|>]{1,20}\|>|\[/?INST\]", re.I)
PLACEHOLDER = "[texto omitido: contenía instrucciones]"


@dataclass
class Isolated:
    text: str
    injection: list[str] = field(default_factory=list)


def neutralize(text: str) -> str:
    text = _TAGS.sub(lambda m: m.group(0).replace("<", "‹").replace(">", "›"), text or "")
    text = _CHAT_TOKENS.sub("", text)
    return _ROLE_MARKERS.sub(lambda m: f"{m.group(1)} (texto citado):", text)


def isolate(text: str) -> Isolated:
    """Neutralized text plus the injection rules it trips (empty = clean)."""
    return Isolated(neutralize(text), detect_external_injection(text or ""))


def isolate_context(blocks: list[dict]) -> tuple[list[dict], list[dict]]:
    """Context blocks for the generation prompt: neutralized; blocks with instructions are
    withheld. Returns (safe_blocks, findings) — findings name the source, never the text."""
    safe, findings = [], []
    for block in blocks:
        result = isolate(block.get("text", ""))
        if result.injection:
            findings.append({"source": block.get("source_document"), "section": block.get("section"),
                             "rules": result.injection})
            continue
        safe.append({**block, "text": result.text})
    return safe, findings


def isolate_tool_text(text: str) -> tuple[str, list[str]]:
    """Tool data shown to the user (ticket titles…): instructions replaced by a placeholder."""
    result = isolate(text)
    return (PLACEHOLDER, result.injection) if result.injection else (result.text, [])
