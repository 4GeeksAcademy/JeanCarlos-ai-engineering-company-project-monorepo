"""The two model calls of the memory loop, each with a structured (JSON) output validated here.

- self_evaluate(): after answering, ONE call decides whether the user's message contains a fact
  worth remembering (explicit criterion below) and drafts the proposal. It never writes memory.
- classify_decision(): when a proposal is pending, ONE call labels the user's next message
  against that proposal: approve / reject / edit / unrelated / ambiguous, with a confidence and
  the literal evidence. Anything below the confidence threshold, or whose evidence is not in
  the message, is treated as ambiguous: the proposal is discarded, never approved by default.

Deterministic checks wrap both calls (the model is not trusted on its own): PHI guard,
allowed kinds, grounding of the proposal in the user's own words, evidence present in the
message.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from data.process.rag import llm_client, settings

from .phi_guard import find_phi

APPROVAL_CONFIDENCE = 0.8
GROUNDING_RATIO = 0.6  # share of the proposal's content words that must come from the user's message


class SelfEvaluation(BaseModel):
    memorable: bool
    kind: Literal["clinic_correction", "incident_pattern", "staff_preference"] | None = None
    content: str | None = Field(default=None, max_length=400)
    scope_clinic: str | None = None
    scope_country: Literal["US", "UK"] | None = None
    scope_staff: str | None = None
    reason: str = ""
    patient_data_requested: bool = False


class Decision(BaseModel):
    label: Literal["approve", "reject", "edit", "unrelated", "ambiguous"]
    confidence: float = Field(ge=0, le=1)
    evidence: str = ""
    edited_content: str | None = Field(default=None, max_length=400)
    remaining_question: str | None = Field(default=None, max_length=1000)


SELF_EVALUATION_PROMPT = """Eres el paso de auto-evaluación del agente de soporte de HealthCore (12 clínicas en EE. UU. y \
Reino Unido). Ya respondiste al usuario; ahora decides SI su mensaje contiene algo que valga la pena \
recordar para conversaciones futuras. No escribes memoria: solo propones.

Propón (memorable=true) SOLO si el MENSAJE DEL USUARIO afirma un hecho NUEVO o CORREGIDO de uno de estos tipos:
- "clinic_correction": corrección operativa de una clínica o de un país: cambio de horario, protocolo \
local de recepción, excepción administrativa US vs UK, cambio de un proceso interno en una clínica.
- "incident_pattern": patrón de incidentes conocido y su causa, sin datos de paciente (p. ej. "el \
sistema de referidos falla los lunes por el batch nocturno", "la alerta de no-show en Austin fue por un \
cierre de carretera").
- "staff_preference": cómo un miembro del staff quiere que se le presente información operativa (no clínica).

NO propongas (memorable=false) cuando:
- es una pregunta o consulta puntual (el dato vive en un dashboard, en el Incidents Manager o en el inventario);
- el mensaje solo pide estados de tickets, stock u otros datos en vivo;
- es un saludo, un agradecimiento o un cierre de conversación;
- el hecho ya está en las MEMORIAS EXISTENTES o en la base de conocimiento;
- pretende cambiar una política oficial documentada (tarifas, coberturas, plazos, reglas de no-show, \
seguros): eso lo cambia el dueño del documento, no la memoria del agente;
- el hecho no lo afirma el usuario (no inventes ni completes datos).

PHI (HIPAA + UK GDPR, sin excepciones): si el mensaje intenta que recuerdes algo de un paciente concreto \
(nombre, historia clínica, fecha de nacimiento, diagnóstico, seguro, resultados, notas clínicas, citas de \
una persona) marca patient_data_requested=true y memorable=false.

"content" debe ser una sola frase autocontenida, en español, con las palabras del usuario, sin datos de \
pacientes. "scope_clinic": ciudad o sede si aplica; "scope_country": "US" o "UK" si aplica; \
"scope_staff": nombre del miembro del staff para preferencias.

Devuelve SOLO JSON: {"memorable": bool, "kind": ..., "content": ..., "scope_clinic": ..., \
"scope_country": ..., "scope_staff": ..., "reason": "...", "patient_data_requested": bool}"""

DECISION_PROMPT = """Clasifica la respuesta del usuario a una PROPUESTA DE MEMORIA pendiente del agente de HealthCore.

Etiquetas:
- "approve": acepta explícitamente que se recuerde la propuesta tal cual ("sí", "recuérdalo", "guárdalo", "adelante").
- "reject": rechaza explícitamente ("no", "no lo guardes", "olvídalo", "eso no es correcto").
- "edit": acepta pero corrige el contenido; pon en "edited_content" la frase final completa a recordar.
- "unrelated": no contesta a la propuesta y cambia de tema (por ejemplo, hace otra pregunta sin aprobar ni rechazar).
- "ambiguous": no se puede saber con seguridad (p. ej. "ok", "gracias", "puede ser", "luego vemos").

Nunca interpretes silencio, cortesía o ambigüedad como aprobación.
"evidence": copia LITERAL el fragmento del mensaje que justifica la etiqueta.
"remaining_question": si además de decidir el usuario pregunta o pide otra cosa en el mismo mensaje, \
cópiala aquí (si no, null). Si la etiqueta es "unrelated", pon todo el mensaje.
"confidence": de 0 a 1.

Devuelve SOLO JSON: {"label": ..., "confidence": ..., "evidence": "...", "edited_content": ..., "remaining_question": ...}"""


def _json_call(system: str, user: str) -> dict[str, Any]:
    response = llm_client().chat.completions.create(
        model=settings().generation_model, temperature=0,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    content = response.choices[0].message.content or ""
    match = re.search(r"\{.*\}", content, re.S)
    if not match:
        raise ValueError("no JSON in the model output")
    return json.loads(match.group(0))


def _words(text: str) -> set[str]:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return {w for w in re.findall(r"[a-z0-9]+", text) if len(w) > 3}


def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
    """Models write "" or "N/A" for an empty scope; treat it as no scope."""
    clean = dict(raw)
    for key in ("kind", "content", "scope_clinic", "scope_country", "scope_staff"):
        value = clean.get(key)
        if isinstance(value, str) and value.strip().lower() in ("", "null", "none", "n/a", "na", "-"):
            clean[key] = None
    if clean.get("scope_country") not in (None, "US", "UK"):
        clean["scope_country"] = {"EE. UU.": "US", "USA": "US", "EEUU": "US", "Reino Unido": "UK", "GB": "UK"}.get(
            str(clean["scope_country"]).strip())
    return clean


# Documented policy (knowledge base: insurers, coverage, fees, charges, quotas) is changed by its
# owner, never by the agent memory. A proposal touching it is dropped even if the model proposed it.
_DOCUMENTED_POLICY = re.compile(
    r"\b(?:seguros?|aseguradoras?|cobertura|insurance|coverage|medicare|medicaid|bupa|axa|aetna|cigna|"
    r"unitedhealthcare|blue\s+cross|humana|tarifas?|copagos?|self-pay|descuentos?|precios?|usd|gbp|"
    r"cargos?\s+(?:de|por)|cupos?|ilimitad[oa])\b", re.IGNORECASE)


def touches_documented_policy(content: str) -> bool:
    plain = unicodedata.normalize("NFKD", content)
    return bool(_DOCUMENTED_POLICY.search("".join(ch for ch in plain if not unicodedata.combining(ch))))


def grounded(content: str, message: str) -> bool:
    """Anti-poisoning/hallucination: the proposal must be made of the user's own words."""
    words = _words(content)
    return not words or len(words & _words(message)) / len(words) >= GROUNDING_RATIO


def self_evaluate(message: str, answer: str, memories: list[dict]) -> dict[str, Any]:
    """Returns {"outcome": "none" | "propose" | "blocked_phi", ...}. Never raises: if the model
    fails, there is simply nothing to propose."""
    phi_in_message = find_phi(message)
    try:
        raw = _json_call(SELF_EVALUATION_PROMPT, json.dumps({
            "MENSAJE DEL USUARIO": message, "RESPUESTA DEL AGENTE": answer[:1500],
            "MEMORIAS EXISTENTES": [m["content"] for m in memories]}, ensure_ascii=False))
        evaluation = SelfEvaluation.model_validate(_normalize(raw))
    except (ValueError, ValidationError) as error:
        return {"outcome": "none", "reason": f"self-evaluation unavailable: {type(error).__name__}",
                "phi": [f.category for f in phi_in_message]}
    except Exception as error:  # gateway down: never block the answer
        return {"outcome": "none", "reason": f"self-evaluation unavailable: {type(error).__name__}",
                "phi": [f.category for f in phi_in_message]}

    content_phi = find_phi(evaluation.content or "") + find_phi(evaluation.scope_staff or "")
    if evaluation.patient_data_requested or (evaluation.memorable and (content_phi or phi_in_message)):
        categories = sorted({f.category for f in [*phi_in_message, *content_phi]} or {"patient_information"})
        return {"outcome": "blocked_phi", "categories": categories, "reason": "patient information"}
    if not evaluation.memorable or not evaluation.kind or not evaluation.content:
        return {"outcome": "none", "reason": evaluation.reason[:300]}
    if not grounded(evaluation.content, message):
        return {"outcome": "none", "reason": "proposal not grounded in the user's words (rejected by the agent)"}
    if touches_documented_policy(evaluation.content):
        return {"outcome": "none", "reason": "documented policy (insurers, coverage, fees, quotas): changed by its owner, "
                                             "not by the agent memory"}
    return {"outcome": "propose", "kind": evaluation.kind, "content": evaluation.content.strip(),
            "scope_clinic": evaluation.scope_clinic, "scope_country": evaluation.scope_country,
            "scope_staff": evaluation.scope_staff, "reason": evaluation.reason[:300]}


def classify_decision(proposal: dict, message: str) -> dict[str, Any]:
    """Explicit intent classification of `message` against the pending `proposal`."""
    try:
        raw = _json_call(DECISION_PROMPT, json.dumps({
            "PROPUESTA": proposal["content"], "MENSAJE DEL USUARIO": message}, ensure_ascii=False))
        decision = Decision.model_validate(raw)
        classifier = "model"
    except Exception as error:  # fail safe: no classifier, no approval
        return {"label": "ambiguous", "confidence": 0.0, "classifier": f"unavailable:{type(error).__name__}",
                "edited_content": None, "remaining_question": None}
    label = decision.label
    evidence_ok = bool(decision.evidence) and decision.evidence.strip().lower() in message.lower()
    if label in ("approve", "reject", "edit") and (decision.confidence < APPROVAL_CONFIDENCE or not evidence_ok):
        label = "ambiguous"  # below threshold or evidence not in the message: discard, never approve
    if label == "edit" and not (decision.edited_content or "").strip():
        label = "ambiguous"
    remaining = (decision.remaining_question or "").strip() or None
    if label == "unrelated":
        remaining = message
    return {"label": label, "model_label": decision.label, "confidence": decision.confidence, "classifier": classifier,
            "evidence": decision.evidence[:200], "edited_content": decision.edited_content if label == "edit" else None,
            "remaining_question": remaining}
