"""Validation of the answer before it is returned to the user.

Checks, each with its failure type:
- structural: empty answer, oversized answer, raw JSON / chat markup instead of text;
- security: leak of the system prompt (canary token or its characteristic phrases), of
  internal prompts, endpoints or secrets;
- content: PHI or patient identifiers in the output (BRIEFING Hito 8 Parte 2 §3: detected in the
  model output itself, not only instructed), details of an active breach.

A failing answer is replaced by a safe fixed message; the findings are logged by category only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .prompts import CANARY
from .rules import detect_identifiers_in_output

MAX_ANSWER_CHARS = 6000

_PROMPT_FINGERPRINTS = re.compile(
    r"jerarquia\s+de\s+autoridad|solo\s+estas\s+instrucciones\s+de\s+sistema|devuelve\s+solo\s+json|reglas\s+obligatorias|"
    r"<\s*/?\s*(?:mensaje_usuario|documento|memoria|datos_herramienta)\s*>|self_evaluation_prompt|planner_prompt|"
    r"eres\s+el\s+enrutador|paso\s+de\s+auto-evaluacion", re.I)
_SECRETS = re.compile(
    r"\bsk-[A-Za-z0-9_-]{16,}|\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|\b(?:LLM_API_KEY|JWT_SECRET_KEY|MCP_[A-Z_]*SECRET|"
    r"client_secret|DATABASE_URL|KC_BOOTSTRAP_ADMIN_PASSWORD)\b|https?://(?:localhost|127\.0\.0\.1|llm\.4geeks\.ai|keycloak|"
    r"qdrant)\S*|postgres(?:ql)?://", re.I)
_STRUCTURE = re.compile(r"^\s*[\[{]\s*\"|<\|im_start\|>|<\|im_end\|>", re.I)


@dataclass
class OutputCheck:
    ok: bool
    findings: list[tuple[str, str]] = field(default_factory=list)  # (failure_type, category)


def _plain(text: str) -> str:
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def check_output(answer: str | None) -> OutputCheck:
    findings: list[tuple[str, str]] = []
    text = answer or ""
    if not text.strip():
        findings.append(("structural", "empty_answer"))
    if len(text) > MAX_ANSWER_CHARS:
        findings.append(("structural", "answer_too_long"))
    if _STRUCTURE.search(text):
        findings.append(("structural", "raw_markup_or_json"))
    if CANARY in text:
        findings.append(("security", "system_prompt_leak:canary"))
    if _PROMPT_FINGERPRINTS.search(_plain(text)):
        findings.append(("security", "system_prompt_leak:fingerprint"))
    if _SECRETS.search(text):
        findings.append(("security", "internal_secret_or_endpoint"))
    for category in detect_identifiers_in_output(text):
        findings.append(("content", f"phi_in_output:{category}"))
    return OutputCheck(not findings, findings)
