"""Indirect prompt injection screening of an uploaded RFP (NIST Protect · BRIEFING §3/§4).

The PDF is external content that every agent of the flow reads (classifier, extractor,
orchestrator, workers, synthesizer, generators). The prompts already treat it as data — it travels
inside <documento_rfp> and the system rule says never to follow instructions in it — but that is an
instruction to a model, not a control. This screen is deterministic and runs right after the PHI
screen, BEFORE any agent: every paragraph that carries an instruction to the system ("ignore all
previous instructions…", fake `SYSTEM:` role markers, prompt extraction, obfuscated/base64
variants — the same detectors as the Compliance assistant's input guard) is replaced by a fixed
marker. Only the screened Markdown continues and is stored; the ticket records which rules fired
(never the removed text) so Revenue Cycle and Compliance can see the attempt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from services.agent.harness.rules import detect_external_injection

MARKER = "[RETIRADO: el documento contenía instrucciones dirigidas al sistema]"


@dataclass
class InjectionScreen:
    detected: bool
    rules: list[str] = field(default_factory=list)
    removed_paragraphs: int = 0
    markdown: str = ""


def screen(markdown: str) -> InjectionScreen:
    blocks = re.split(r"\n\s*\n", markdown or "")
    fired: set[str] = set()
    out: list[str] = []
    removed = 0
    for block in blocks:
        hits = detect_external_injection(block)
        if hits:
            fired.update(hits)
            removed += 1
            if not out or out[-1] != MARKER:
                out.append(MARKER)
            continue
        out.append(block)
    return InjectionScreen(bool(fired), sorted(fired), removed, "\n\n".join(out))
