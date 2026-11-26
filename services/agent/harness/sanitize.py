"""Explicit sanitization of user input before it can reach a model (NIST Protect · prompt injection).

Applied at every entry point where staff text goes to a model: the Compliance assistant (REST and
WebSocket, through AgentRuntime.screen) and the RAG endpoint (POST /knowledge/query).

1. Unicode NFKC: full-width and compatibility look-alikes become plain characters, so the
   injection and PHI detectors see the same text the model sees.
2. Invisible and direction-control characters are removed: zero-width characters (U+200B–U+200D,
   U+2060, U+FEFF) and bidi overrides/isolates (U+202A–U+202E, U+2066–U+2069) are a classic way to
   hide an instruction from a human reviewer while the model still reads it.
3. Other control characters are removed (newlines and tabs are kept); runs of blank lines collapse.
4. Length limit and non-empty check: an oversized input is refused, never truncated silently.

Then the existing guards run on the clean text: patient identifiers (rules.detect_patient_identifiers)
and instruction override (rules.detect_instruction_override), and the text always reaches the model
inside its own delimited block and role (data/pipelines/rag.py build_messages), never concatenated
into the system prompt.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤⁦-⁩﻿­]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_BLANK_LINES = re.compile(r"\n{3,}")


class InvalidInput(ValueError):
    """The input cannot be sent to a model (empty or too long after sanitization)."""


@dataclass(frozen=True)
class Sanitized:
    text: str
    removed: tuple[str, ...]  # what was stripped: "invisible", "control" (never the text itself)


def sanitize_input(text: str, *, max_chars: int = 1000, min_chars: int = 1) -> Sanitized:
    if not isinstance(text, str):
        raise InvalidInput("El mensaje debe ser texto.")
    removed: list[str] = []
    clean = unicodedata.normalize("NFKC", text)
    if _INVISIBLE.search(clean):
        removed.append("invisible")
        clean = _INVISIBLE.sub("", clean)
    if _CONTROL.search(clean):
        removed.append("control")
        clean = _CONTROL.sub("", clean)
    clean = _BLANK_LINES.sub("\n\n", clean.replace("\r\n", "\n").replace("\r", "\n")).strip()
    if len(clean) < min_chars:
        raise InvalidInput("El mensaje está vacío.")
    if len(clean) > max_chars:
        raise InvalidInput(f"El mensaje supera los {max_chars} caracteres.")
    return Sanitized(clean, tuple(removed))
