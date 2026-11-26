"""Deterministic PHI guard of the agent memory (HealthCore BRIEFING, Hito 8 Parte 1).

Nothing that identifies a patient, nor any clinical content, may enter the agent memory, its
proposals or its audit log, under HIPAA (US) and UK GDPR (UK) at the same time. This guard runs:

1. on every memory proposal, before it is shown to the user;
2. on an edited proposal, before it is approved;
3. on every write and on every consolidation pass (the BRIEFING asks to re-verify);
4. on the text stored in the audit log (the originating message is redacted if it trips).

It is deliberately conservative: a false positive only means "the agent does not remember
this"; a false negative would be a compliance breach.

Coverage:
- HIPAA Safe Harbor identifiers (45 CFR 164.514(b)(2)): names in a patient context, dates tied to
  a person (birth, admission, discharge, death), phone/fax numbers, emails, SSN, medical record
  and health-plan/insurance numbers, account/certificate/licence/device numbers, URLs, IPs.
- UK GDPR: NHS numbers (10 digits), National Insurance numbers, postcodes next to a person, and
  special-category health data (Art. 9): diagnoses, conditions, medication of a person, lab
  results and clinical notes.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_FLAGS = re.IGNORECASE | re.UNICODE


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


# (category, regulation, pattern) — patterns run on the accent-stripped text.
_RULES: list[tuple[str, str, re.Pattern[str]]] = [
    # ---- a named or numbered patient (HIPAA names / UK GDPR identification)
    ("patient_name", "HIPAA + UK GDPR",
     re.compile(r"\b(?i:paciente|patient|pt\.?|enfermo|enferma|usuario del servicio)s?\s+(?i:(?:de\s+)?(?:apellido|llamad[oa]|named)\s+)?"
                r"(?i:(?:sr|sra|srta|mr|mrs|ms|miss|dr|dra|don|dona)\.?\s+)?[A-Z][a-z'-]+", re.UNICODE)),
    ("patient_name", "HIPAA + UK GDPR",
     re.compile(r"\b(?i:sr|sra|srta|mr|mrs|ms|miss)\.?\s+[A-Z][a-z'-]+", re.UNICODE)),
    ("patient_reference", "HIPAA + UK GDPR",
     re.compile(r"\b(?:el|la|al|del|this|that|the)\s+paciente\s+(?:de\s+la\s+(?:cama|habitacion)|que\s+se\s+llama)\b", _FLAGS)),
    # ---- record, plan and national identifiers
    ("medical_record_number", "HIPAA", re.compile(r"\b(?:mrn|nhc|historia\s+clinica|medical\s+record|n[uº°o]\.?\s*de\s+historia)\b", _FLAGS)),
    ("ssn", "HIPAA", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("nhs_number", "UK GDPR", re.compile(r"\b\d{3}[ -]?\d{3}[ -]?\d{4}\b")),
    ("national_insurance_number", "UK GDPR", re.compile(r"\b[A-CEGHJ-PR-TW-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b")),
    ("insurance_member_id", "HIPAA",
     re.compile(r"\b(?:poliza|policy|member|afiliad[oa]|beneficiari[oa]|subscriber|numero\s+de\s+seguro|insurance\s+(?:id|number)|medicare\s+(?:id|number)|medicaid\s+(?:id|number))\b", _FLAGS)),
    ("long_identifier", "HIPAA", re.compile(r"\b(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z])[A-Z0-9]{2,}-?[A-Z0-9]{4,}\b")),
    # ---- contact data and other direct identifiers
    ("phone_number", "HIPAA + UK GDPR", re.compile(r"(?:\+\d{1,3}[\s.-]?)?\(?\d{2,4}\)?[\s.-]\d{3,4}[\s.-]\d{3,4}\b")),
    ("email", "HIPAA + UK GDPR", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("url_or_ip", "HIPAA", re.compile(r"\bhttps?://\S+|\b\d{1,3}(?:\.\d{1,3}){3}\b")),
    ("uk_postcode", "UK GDPR", re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b")),
    # ---- dates tied to a person
    ("date_of_birth", "HIPAA + UK GDPR",
     re.compile(r"\b(?:fecha\s+de\s+nacimiento|nacid[oa]\s+el|f\.?\s?nac|date\s+of\s+birth|dob|born\s+on|d\.o\.b)\b", _FLAGS)),
    ("personal_date", "HIPAA",
     re.compile(r"\b(?:ingres[oó]|alta|admitid[oa]|admitted|discharged|fallecio|died)\b[^.]{0,40}\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", _FLAGS)),
    # ---- clinical content (HIPAA PHI when linked to a person; UK GDPR special category)
    ("diagnosis", "HIPAA + UK GDPR (Art. 9)",
     re.compile(r"\b(?:diagnostic[oa]|diagnosticad[oa]|diagnosis|diagnosed|padece|sufre\s+de|tiene\s+(?:diabetes|cancer|vih|hiv|asma|hepatitis|depresion)|"
                r"historial\s+medico\s+de|condicion\s+medica|enfermedad\s+de)\b", _FLAGS)),
    ("icd_code", "HIPAA + UK GDPR (Art. 9)", re.compile(r"\b[A-TV-Z]\d{2}(?:\.\d{1,2})\b")),
    ("lab_result", "HIPAA + UK GDPR (Art. 9)",
     re.compile(r"\b(?:resultado[s]?\s+de\s+(?:laboratorio|analitica|la\s+prueba)|lab\s+results?|hba1c|glucosa\s+(?:de|en)\s+\d|"
                r"\d+(?:[.,]\d+)?\s*(?:mg/dl|mmol/l|g/dl|ui/l|u/l))\b", _FLAGS)),
    ("clinical_note", "HIPAA + UK GDPR (Art. 9)",
     re.compile(r"\b(?:nota\s+clinica|nota\s+medica|clinical\s+note|receta(?:do)?\s+(?:a|para)\s+|prescrib|tratamiento\s+de\s+(?:el|la)\s+paciente|"
                r"alergic[oa]\s+a|embarazada|pregnant)\b", _FLAGS)),
]


@dataclass(frozen=True)
class PhiFinding:
    category: str
    regulation: str


def find_phi(text: str | None) -> list[PhiFinding]:
    """Every PHI rule the text trips (empty list = clean). The matched text is never returned,
    so callers cannot leak it into a log by accident."""
    if not text:
        return []
    plain = _plain(text)
    found: dict[str, PhiFinding] = {}
    for category, regulation, pattern in _RULES:
        if category not in found and (pattern.search(plain) or pattern.search(text)):
            found[category] = PhiFinding(category, regulation)
    return list(found.values())


def contains_phi(text: str | None) -> bool:
    return bool(find_phi(text))


def redact_for_audit(text: str | None) -> str | None:
    """What the audit log may keep of a user message: the text if clean, otherwise only the
    categories that tripped (never the content)."""
    if text is None:
        return None
    findings = find_phi(text)
    if not findings:
        return text[:500]
    return f"[redactado: posible PHI ({', '.join(sorted({f.category for f in findings}))})]"
