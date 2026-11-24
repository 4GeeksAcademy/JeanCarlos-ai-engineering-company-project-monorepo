"""PHI screening of the converted RFP (BRIEFING Hito 9: detection rate goal 100%).

Runs on the Markdown right after the conversion, BEFORE any agent reads it, and again on every
agent output. An RFP legitimately talks about health in general ("chronic condition management
(diabetes, hypertension)", "student health data"); what is PHI is information about an individual
patient: a patient name, identifiers with a value (DOB, MRN, NHS, SSN), an attached clinical
summary, or a diagnosis tied to a person.

When PHI is found the offending paragraphs (and an attached clinical summary up to the next
section) are replaced by a fixed marker; only the redacted Markdown continues through the flow
and is stored. The categories (never the text) are recorded on the ticket and Compliance review
is required before the handoff.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

REDACTION = "[REDACTADO: posible PHI — retenido para revisión humana de Compliance]"

_NAME = r"[A-Z][a-zA-Z'’-]+(?:\s+[A-Z]\.?)?(?:\s+[A-Z][a-zA-Z'’-]+)"

RULES: list[tuple[str, re.Pattern[str]]] = [
    ("patient_name", re.compile(
        rf"\b(?i:patient|paciente|pt\.?)(?:\s+(?i:name|nombre))?\s*[:\-–]\s*{_NAME}|"
        rf"\b(?i:patient|paciente)\s+(?:(?i:mr|mrs|ms|miss|sr|sra|srta)\.?\s+)?{_NAME}\b(?=[^.\n]{{0,80}}"
        r"(?i:diagnos|years?\s*old|a[nñ]os|dob|born|mrn|admitted|ingres))")),
    ("named_person_with_condition", re.compile(
        rf"\b(?:Mr|Mrs|Ms|Miss|Sr|Sra)\.?\s+{_NAME}?[A-Z][a-z]+[^.\n]{{0,80}}\b(?i:diagnos\w*|suffers|padece|treated\s+for|"
        r"tratad[oa]\s+(?:de|por))")),
    ("date_of_birth", re.compile(r"\b(?i:dob|d\.o\.b\.?|date\s+of\s+birth|fecha\s+de\s+nacimiento|born\s+on|nacid[oa]\s+el)\b"
                                 r"\s*[:\-–]?\s*\d")),
    ("medical_record_number", re.compile(r"\b(?i:mrn|medical\s+record(?:\s+(?:number|no\.?|#))?|historia\s+cl[ií]nica|"
                                         r"n[ºo°]\s*de\s+historia|patient\s+id)\b\s*[:#\-–]?\s*[A-Z0-9-]*\d{3,}")),
    ("nhs_number", re.compile(r"\b(?i:nhs)\s*(?i:number|no\.?|#)?\s*[:\-–]?\s*\d{3}\s?\d{3}\s?\d{4}\b")),
    ("ssn", re.compile(r"\b(?i:ssn|social\s+security(?:\s+number)?)\b\s*[:#\-–]?\s*\d{3}-?\d{2}-?\d{4}\b|\b\d{3}-\d{2}-\d{4}\b")),
    ("diagnosis_field", re.compile(r"(?m)^\W*(?i:diagnosis|diagn[oó]stico|primary\s+diagnosis|condition|icd-?10)\s*[:\-–]\s*\S")),
    ("clinical_summary", re.compile(r"\b(?i:clinical\s+summary|patient\s+summary|discharge\s+summary|resumen\s+cl[ií]nico|"
                                    r"informe\s+cl[ií]nico|case\s+summary|medical\s+history\s+of|historial\s+m[eé]dico\s+de)\b")),
    ("age_with_diagnosis", re.compile(r"\b\d{1,3}[- ](?i:years?[- ]old|y/?o|a[nñ]os)\b[^.\n]{0,80}\b(?i:diagnos\w*|with|con)\b"
                                      r"[^.\n]{0,40}\b(?i:diabetes|cancer|c[aá]ncer|hiv|vih|depress\w*|hypertension|asthma|"
                                      r"hepatitis|disorder|syndrome|disease|enfermedad)")),
]
_SECTION_START = re.compile(r"^(?:#{1,6}\s|\d+\.\s+[A-Z]|(?i:attachment|annex|anexo|appendix|ap[eé]ndice)\b)")


@dataclass
class PhiScreen:
    detected: bool
    categories: list[str] = field(default_factory=list)
    redacted_markdown: str = ""
    redacted_paragraphs: int = 0


def find_phi(text: str | None) -> list[str]:
    """Categories found (never the matched text)."""
    if not text:
        return []
    return sorted({name for name, rule in RULES if rule.search(text)})


def screen(markdown: str) -> PhiScreen:
    blocks = re.split(r"\n\s*\n", markdown)
    categories: set[str] = set()
    out: list[str] = []
    redacted = 0
    in_clinical_attachment = False
    for block in blocks:
        hits = find_phi(block)
        if in_clinical_attachment and _SECTION_START.match(block.strip()) and not hits:
            in_clinical_attachment = False
        if "clinical_summary" in hits:
            in_clinical_attachment = True
        if hits or in_clinical_attachment:
            categories.update(hits or ["clinical_summary"])
            redacted += 1
            if not out or out[-1] != REDACTION:
                out.append(REDACTION)
            continue
        out.append(block)
    text = "\n\n".join(out)
    if find_phi(text):  # fail closed: never continue with PHI left in the document
        raise RuntimeError("PHI redaction left identifiable content")
    return PhiScreen(bool(categories), sorted(categories), text, redacted)
