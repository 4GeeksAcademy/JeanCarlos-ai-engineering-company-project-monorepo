"""Part 2 — evaluators of a generated section, and the EvaluationResult contract.

Three evaluators run in parallel on each section (graph: generate → readability | relevance |
compliance → judge). Each one receives ONLY the section under review plus what it needs to judge
it — never the whole RFP nor another department's section:

    readability  draft                                     → py-readability-metrics (deterministic)
    relevance    draft + its department's key_aspects       → model judge, every "covered" claim must
                                                              quote the draft verbatim (verified)
    compliance   draft + client_country/currency + allowed  → BRIEFING §5 rules by id (deterministic)
                 figures from the Part 1 handoff

The judge merges the three results into one EvaluationResult with a concrete
`feedback_for_generator` that goes back to the department's generator when the section fails.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from . import agents, guidelines, phi
from .document import _ensure_punkt
from .models import utcnow

# ================================================================ EvaluationResult


class _Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class ReadabilityEval(_Model):
    passed: bool = Field(alias="pass")
    score: float | None  # Flesch-Kincaid grade
    details: dict[str, Any]


class RelevanceEval(_Model):
    passed: bool = Field(alias="pass")
    score: float  # share of key aspects covered
    missing_aspects: list[str]
    covered_aspects: list[str]
    evaluator: str  # model / fallback
    details: dict[str, Any] = Field(default_factory=dict)


class Violation(BaseModel):
    rule_id: str
    message: str


class ComplianceEval(_Model):
    passed: bool = Field(alias="pass")
    rule_ids: list[str]  # rules checked
    violations: list[Violation]
    contains_phi: bool


class EvaluationResult(_Model):
    section_id: str
    department_id: str
    iteration: int
    readability: ReadabilityEval
    relevance: RelevanceEval
    compliance: ComplianceEval
    overall_pass: bool
    contains_phi: bool  # BRIEFING §2.3: the compliance evaluator can mark it
    feedback_for_generator: str
    evaluated_at: str

    def as_dict(self) -> dict:
        return self.model_dump(by_alias=True)


# ================================================================ helpers


def _prose(markdown: str) -> str:
    """Markdown → plain sentences: headings dropped, every bullet/line ends as a sentence."""
    lines = []
    for line in markdown.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or set(line) <= set("-|: "):
            continue
        line = re.sub(r"^(?:[-*+]|\d+[.)])\s+", "", line)
        line = re.sub(r"[*_`>|]+|\[|\]", "", line).strip()
        if line and line[-1] not in ".!?:;":
            line += "."
        lines.append(line)
    return " ".join(lines)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower())


def _contains(haystack: str, needle: str) -> bool:
    h, n = " ".join(_words(haystack)), " ".join(_words(needle))
    return bool(n) and n in h


# ================================================================ 1. readability


def evaluate_readability(draft: str) -> ReadabilityEval:
    prose = _prose(draft)
    words = len(re.findall(r"\b\w+\b", prose))
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", prose) if s.strip()]
    long = sorted((s for s in sentences if len(s.split()) > guidelines.LONG_SENTENCE_WORDS), key=lambda s: -len(s.split()))
    details: dict[str, Any] = {"words": words, "sentences": len(sentences), "max_grade": guidelines.MAX_GRADE,
                               "min_reading_ease": guidelines.MIN_READING_EASE,
                               "words_range": [guidelines.MIN_WORDS, guidelines.MAX_WORDS],
                               "long_sentences": [" ".join(s.split()[:12]) + "…" for s in long[:3]], "issues": []}
    if words < guidelines.MIN_WORDS:
        details["issues"].append(f"too short: {words} words (minimum {guidelines.MIN_WORDS})")
        return ReadabilityEval(passed=False, score=None, details=details)
    _ensure_punkt()
    from readability import Readability

    r = Readability(prose)
    grade = round(r.flesch_kincaid().score, 2)
    ease = round(r.flesch().score, 2)
    details.update(flesch_kincaid_grade=grade, flesch_reading_ease=ease, gunning_fog=round(r.gunning_fog().score, 2))
    if words > guidelines.MAX_WORDS:
        details["issues"].append(f"too long: {words} words (maximum {guidelines.MAX_WORDS})")
    if grade > guidelines.MAX_GRADE:
        details["issues"].append(f"Flesch-Kincaid grade {grade} > {guidelines.MAX_GRADE}")
    if ease < guidelines.MIN_READING_EASE:
        details["issues"].append(f"Flesch reading ease {ease} < {guidelines.MIN_READING_EASE}")
    return ReadabilityEval(passed=not details["issues"], score=grade, details=details)


# ================================================================ 2. relevance

RELEVANCE_PROMPT = """Eres el evaluador de pertinencia de las secciones de propuestas de HealthCore. Recibes UNA sección \
(de {department}) y la lista numerada de aspectos clave que la Parte 1 asignó a ese departamento. Para cada aspecto, \
decide si la sección lo responde de forma explícita (no basta con mencionarlo de pasada). Si está cubierto, copia en \
"evidence" una frase LITERAL de la sección que lo responde (se verificará que exista tal cual).

JSON: {{"aspects": [{{"index": 0, "covered": true|false, "evidence": "frase literal de la sección" | null, \
"gap": "qué le falta, si no está cubierto" | null}}]}}"""


class AspectVerdict(BaseModel):
    index: int
    covered: bool
    evidence: str | None = None
    gap: str | None = None


class RelevanceVerdict(BaseModel):
    aspects: list[AspectVerdict]


def _aspect_title(aspect: dict) -> str:
    return aspect.get("aspect") or str(aspect)


def _keyword_coverage(draft: str, aspect: dict) -> bool:
    stop = {"the", "and", "for", "with", "that", "this", "from", "una", "los", "las", "del", "por", "para", "con", "que"}
    keys = {w for w in _words(f"{aspect.get('aspect', '')} {aspect.get('detail', '')}") if len(w) > 3 and w not in stop}
    found = set(_words(draft))
    return bool(keys) and len(keys & found) / len(keys) >= 0.5


def evaluate_relevance(department_name: str, draft: str, key_aspects: list[dict]) -> RelevanceEval:
    if not key_aspects:
        return RelevanceEval(passed=True, score=1.0, missing_aspects=[], covered_aspects=[], evaluator="rules")
    numbered = "\n".join(f"{i}. {_aspect_title(a)}: {a.get('detail', '')}" for i, a in enumerate(key_aspects))
    gaps: dict[str, str] = {}
    try:
        verdict = agents.json_call("evaluator:relevance", RELEVANCE_PROMPT.format(department=department_name),
                                   f"Aspectos clave:\n{numbered}\n\n<seccion>\n{draft}\n</seccion>", RelevanceVerdict)
        by_index = {v.index: v for v in verdict.aspects}
        covered, unverified = [], []
        for i, aspect in enumerate(key_aspects):
            v = by_index.get(i)
            if v and v.covered and v.evidence and _contains(draft, v.evidence):
                covered.append(_aspect_title(aspect))
            else:
                if v and v.covered:
                    unverified.append(_aspect_title(aspect))  # claimed covered, but the quote is not in the draft
                if v and v.gap:
                    gaps[_aspect_title(aspect)] = v.gap
        evaluator, details = "model", {"unverified_evidence": unverified}
    except agents.AgentError as error:  # the judge is down: deterministic keyword coverage
        covered = [_aspect_title(a) for a in key_aspects if _keyword_coverage(draft, a)]
        evaluator, details = "fallback", {"reason": str(error)[:120]}
    missing = [_aspect_title(a) for a in key_aspects if _aspect_title(a) not in covered]
    details["gaps"] = gaps
    return RelevanceEval(passed=not missing, score=round(len(covered) / len(key_aspects), 2), missing_aspects=missing,
                         covered_aspects=covered, evaluator=evaluator, details=details)


# ================================================================ 3. compliance (BRIEFING §5)

_BAA = re.compile(r"\bBAA\b|business\s+associate", re.I)
_HIPAA = re.compile(r"\bHIPAA\b", re.I)
_DPA = re.compile(r"\bDPA\b|data\s+processing\s+agreement", re.I)
_UK_GDPR = re.compile(r"\bUK[\s-]+GDPR\b", re.I)
_CURRENCY_MARKS = {"USD": re.compile(r"\$|\bUSD\b|\bUS\s?dollars?\b", re.I), "GBP": re.compile(r"£|\bGBP\b|\bpounds?\s+sterling\b", re.I),
                   "EUR": re.compile(r"€|\bEUR\b|\beuros?\b", re.I)}
_WAIVE_COMPLIANCE = re.compile(
    r"\b(?:without|no\s+need\s+for|not\s+requir\w*|waiv\w*|skip\w*|bypass\w*|optional)\b[^.\n]{0,40}"
    r"\b(?:compliance|regulatory|data\s+protection)\s+(?:review|approval|sign[\s-]?off)\b|"
    r"\b(?:compliance|regulatory)\s+(?:review|approval|sign[\s-]?off)\b[^.\n]{0,30}"
    r"\b(?:is\s+)?(?:optional|not\s+(?:required|needed|necessary)|waived|can\s+be\s+skipped)\b", re.I)
_SMALL_NUMBERS = {str(n) for n in range(0, 11)}


def allowed_figures(*texts: Any) -> set[str]:
    """Every figure present in the Part 1 handoff / excerpts / company context the generator received."""
    found: set[str] = set()
    for text in texts:
        found |= agents.numbers_in(text if isinstance(text, str) else repr(text))
    return found | _SMALL_NUMBERS


def evaluate_compliance(department_id: str, draft: str, *, country: str | None, allowed: set[str],
                        phi_categories: list[str] | None = None) -> ComplianceEval:
    violations: list[Violation] = []
    checked = ["HC-PHI-01", "HC-CUR-01", "HC-CMP-01", "HC-FIG-01", "HC-FMT-01"]

    # HC-PHI-01 — categories only, never the matched text
    categories = sorted(set(phi_categories or []) | set(phi.find_phi(draft)))
    if categories:
        violations.append(Violation(rule_id="HC-PHI-01", message="The section contained patient information "
                                    f"({', '.join(categories)}); it was redacted. Remove every patient name, diagnosis "
                                    "or identifier — not even illustrative examples are allowed."))

    # HC-BAA-US / HC-DPA-UK — the right instrument for the client's country, never the other one
    if country == "US":
        checked.insert(1, "HC-BAA-US")
        if department_id == "compliance" and not (_BAA.search(draft) and _HIPAA.search(draft)):
            violations.append(Violation(rule_id="HC-BAA-US", message="US client: include a Business Associate "
                                        "Agreement (BAA) clause under HIPAA."))
        if _DPA.search(draft) or _UK_GDPR.search(draft):
            violations.append(Violation(rule_id="HC-BAA-US", message="US client: remove the DPA / UK GDPR instrument; "
                                        "the instrument for the US is a BAA under HIPAA."))
    elif country == "UK":
        checked.insert(1, "HC-DPA-UK")
        if department_id == "compliance" and not (_DPA.search(draft) and _UK_GDPR.search(draft)):
            violations.append(Violation(rule_id="HC-DPA-UK", message="UK client: include a Data Processing Agreement "
                                        "(DPA) clause that references UK GDPR."))
        if _BAA.search(draft) or _HIPAA.search(draft):
            violations.append(Violation(rule_id="HC-DPA-UK", message="UK client: remove the BAA / HIPAA instrument; "
                                        "the instrument for the UK is a DPA under UK GDPR."))
    elif department_id == "compliance":
        violations.append(Violation(rule_id="HC-FMT-01", message="client_country is unknown: state that the BAA (US) "
                                    "or DPA (UK GDPR) depends on the country and ask it as an open question."))

    # HC-CUR-01 — USD for US, GBP for UK
    expected = {"US": "USD", "UK": "GBP"}.get(country or "")
    if expected:
        wrong = [code for code, mark in _CURRENCY_MARKS.items() if code != expected and mark.search(draft)]
        if wrong:
            violations.append(Violation(rule_id="HC-CUR-01", message=f"Quote only in {expected} for a {country} client; "
                                        f"remove {', '.join(wrong)}."))
        if department_id == "revenue" and not re.search(rf"\b{expected}\b", draft):
            violations.append(Violation(rule_id="HC-CUR-01", message=f"State the quote currency explicitly as {expected}."))

    # HC-CMP-01 — Compliance approval is always mandatory
    if _WAIVE_COMPLIANCE.search(draft):
        violations.append(Violation(rule_id="HC-CMP-01", message="Compliance review/approval is mandatory for every "
                                    "proposal; do not describe it as optional, waived or not required."))
    if department_id == "compliance" and not re.search(r"Claire\s+Whitfield", draft):
        violations.append(Violation(rule_id="HC-CMP-01", message="State that Compliance approval by Claire Whitfield is "
                                    "required before signature."))

    # HC-FIG-01 — no invented figures
    invented = sorted(agents.numbers_in(draft) - allowed, key=lambda n: (len(n), n))
    if invented:
        violations.append(Violation(rule_id="HC-FIG-01", message="Figures not found in the RFP analysis or HealthCore "
                                    f"data: {', '.join(invented[:8])}. Remove them or replace them with "
                                    "'[to be confirmed]' and add an open question."))

    # HC-FMT-01 — the department's format
    fmt = guidelines.section_format(department_id, country)
    missing = [h for h in (fmt.title, *fmt.headings) if not re.search(r"(?m)^#{2,4}\s*" + re.escape(h), draft, re.I)]
    if missing:
        violations.append(Violation(rule_id="HC-FMT-01", message="Missing required headings: "
                                    + "; ".join(("## " if h == fmt.title else "### ") + h for h in missing)))

    return ComplianceEval(passed=not violations, rule_ids=checked, violations=violations, contains_phi=bool(categories))


# ================================================================ judge


def feedback(readability: ReadabilityEval, relevance: RelevanceEval, compliance: ComplianceEval) -> str:
    lines = []
    for issue in readability.details.get("issues", []):
        lines.append(f"[readability] {issue}.")
    if not readability.passed and readability.details.get("long_sentences"):
        lines.append("[readability] Split these long sentences (max ~20 words each): "
                     + " | ".join(readability.details["long_sentences"]))
    for aspect in relevance.missing_aspects:
        gap = relevance.details.get("gaps", {}).get(aspect)
        lines.append(f"[relevance] Address the key aspect «{aspect}» explicitly{': ' + gap if gap else ''}.")
    for v in compliance.violations:
        lines.append(f"[compliance {v.rule_id}] {v.message}")
    return "\n".join(lines)


def judge(ticket_id: str, department_id: str, iteration: int, readability: ReadabilityEval, relevance: RelevanceEval,
          compliance: ComplianceEval) -> EvaluationResult:
    overall = readability.passed and relevance.passed and compliance.passed
    return EvaluationResult(section_id=f"{ticket_id}:{department_id}", department_id=department_id, iteration=iteration,
                            readability=readability, relevance=relevance, compliance=compliance, overall_pass=overall,
                            contains_phi=compliance.contains_phi,
                            feedback_for_generator="" if overall else feedback(readability, relevance, compliance),
                            evaluated_at=utcnow().isoformat())
