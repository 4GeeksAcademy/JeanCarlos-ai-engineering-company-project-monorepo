"""Deterministic guards of the compliance assistant (HealthCore BRIEFING, Hito 8 Parte 2).

Fast, testable, no model involved. They run on every message before any model call and they
can only block, never allow something another layer blocked. Each returns the rules that fired
(never the matched text, so nothing sensitive reaches a log).

- detect_instruction_override: jailbreaks / attempts to change the system instructions, in
  Spanish and English, including reformulations (role-play, hypotheticals, "new instructions",
  prompt extraction, fake role markers) and obfuscation (s p a c e d letters, l33t, base64).
- detect_personal_task: using the assistant as a personal chatbot (essays, poems, personal
  emails, homework, code for other projects, therapy, recipes…).
- detect_breach_probe: details of a specific / active security breach, including gradual
  extraction across turns (BRIEFING §4 case 4).
- detect_confidential_contract: commercial terms of a vendor-specific BAA/DPA (BRIEFING §3).
- detect_patient_identifiers: a patient case with identifiers or quasi-identifiers (name, age +
  diagnosis + location…) — BRIEFING §2 forbids discussing it; the question must be reformulated.
"""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata

from services.agent.memory.phi_guard import find_phi

_F = re.IGNORECASE


_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})


def plain(text: str, *, deobfuscate: bool = False) -> str:
    """Lowercase, accents removed. With deobfuscate (jailbreak detection only): s p a c e d
    letters joined and l33t digits mapped — never for detectors that need the real numbers."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    if not deobfuscate:
        return text
    text = re.sub(r"\b(?:[a-z0-9]\s){3,}[a-z0-9]\b", lambda m: m.group(0).replace(" ", ""), text)
    return text.translate(_LEET)


def _decoded_base64(text: str) -> str:
    """Text hidden in base64 tokens (a classic way to smuggle instructions)."""
    found = []
    for token in re.findall(r"[A-Za-z0-9+/]{24,}={0,2}", text):
        try:
            decoded = base64.b64decode(token + "=" * (-len(token) % 4), validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        if decoded.isprintable():
            found.append(decoded)
    return " ".join(found)


# ---------------------------------------------------------------- instruction override / jailbreak

_OVERRIDE_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("ignore_instructions", re.compile(
        r"\b(?:ignora|ignoren|ignores|ignorar|ignoralas|ignoralo|olvida|olviden|olvides|olvidar|olvidate|omite|omitir|"
        r"descarta|descartar|saltate|saltar|salta|desactiva|desactivar|anula|anular|borra|borrar|elimina|"
        r"dej\w*\s+de\s+(?:seguir|lado)|no\s+(?:sigas|hagas\s+caso\s+a|obedezcas)|ignore|disregard|forget|override|"
        r"bypass|skip|drop)\b"
        r"[^.?!]{0,60}\b(?:instruccion\w*|regla\w*|indicacion\w*|directriz|directrices|restriccion\w*|politicas?|"
        r"prompt|configuracion|limit\w*|lo\s+anterior|todo\s+lo\s+(?:que\s+te\s+dij\w*|anterior)|instructions?|rules?|"
        r"guidelines?|restrictions?|system|above|previous)\b", _F)),
    ("role_change", re.compile(
        r"\b(?:actua\w*|comportate|finge|fingir|haz\s+de\s+cuenta|imagina\s+que\s+eres|ahora\s+eres|ya\s+no\s+eres|"
        r"a\s+partir\s+de\s+ahora\s+(?:eres|seras|responde\w*|actua\w*)|seras|conviertete|juguemos\s+a\s+que|"
        r"en\s+un\s+mundo\s+(?:hipotetico|donde)|hipoteticamente|pretend|act\s+as|you\s+are\s+now|from\s+now\s+on|"
        r"roleplay|role-play|simulate|let'?s\s+play)\b[^.?!]{0,80}\b(?:sin\s+(?:reglas|restricciones|filtros|limites|"
        r"normas|censura|compliance)|libre\s+de|sin\s+ninguna|no\s+tuvieras|no\s+tienes\s+reglas|otro\s+asistente|"
        r"cualquier\s+cosa|asistente\s+general|no\s+restrictions|unfiltered|without\s+(?:rules|restrictions|limits)|"
        r"no\s+rules|anything|dan\b|jailbr\w*|chatgpt)", _F)),
    ("forget_identity", re.compile(
        r"\b(?:olvida\s+que\s+(?:trabajas|eres)|ya\s+no\s+trabajas|no\s+trabajas\s+para|deja\s+de\s+ser\s+(?:el\s+)?asistente|"
        r"forget\s+(?:that\s+)?you\s+(?:work|are)|you\s+(?:no\s+longer|don'?t)\s+work\s+for)\b", _F)),
    ("mode_switch", re.compile(
        r"\b(?:modo\s+(?:desarrollador|developer|dios|sin\s+censura|libre|dan|jailbreak|admin\w*|depuracion|debug)|"
        r"developer\s+mode|god\s+mode|dan\s+mode|jailbreak\w*|do\s+anything\s+now)\b", _F)),
    ("prompt_extraction", re.compile(
        r"\b(?:muestra\w*|revela\w*|repite\w*|imprime|dime|ensena\w*|copia\w*|escribe|cuales\s+son|reveal|print|show|"
        r"repeat|tell\s+me|what\s+(?:is|are))\b[^.?!]{0,50}\b(?:system\s+prompt|prompt\s+(?:del\s+sistema|inicial|original)|"
        r"tus\s+(?:instrucciones|reglas\s+internas|directrices)|instrucciones\s+(?:del\s+sistema|iniciales|originales|ocultas)|"
        r"tu\s+(?:configuracion|prompt)|your\s+(?:instructions|system\s+prompt|prompt|rules)|hidden\s+instructions)", _F)),
    ("fake_role_marker", re.compile(
        r"(?:^|\n)\s*(?:system|sistema|assistant|asistente|developer)\s*:|<\|?(?:im_start|system|im_end)\|?>|\[/?inst\]|"
        r"#{2,}\s*(?:system|instrucciones|nuevas\s+instrucciones)|\bnuevas\s+instrucciones\s*:|\bnew\s+instructions\s*:", _F)),
    ("authority_claim", re.compile(
        r"\b(?:soy\s+(?:tu|el)\s+(?:desarrollador|creador|administrador|programador)|i\s+am\s+your\s+(?:developer|creator|admin)|"
        r"tengo\s+autorizacion\s+para\s+(?:cambiar|desactivar)|claire\s+(?:whitfield\s+)?(?:me\s+)?autoriz\w*\s+a\s+(?:ignorar|saltar|desactivar))\b", _F)),
]


def detect_instruction_override(text: str) -> list[str]:
    candidates = [plain(text), plain(text, deobfuscate=True), plain(_decoded_base64(text), deobfuscate=True)]
    return sorted({name for name, rule in _OVERRIDE_RULES for candidate in candidates if candidate and rule.search(candidate)})


# ---------------------------------------------------------------- personal chatbot use

_PERSONAL_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("creative_writing", re.compile(
        r"\b(?:escrib\w*|redact\w*|hazme|haz\s+un|crea\w*|compon\w*|inventa\w*|genera\w*|write|compose|draft|make\s+me)\b"
        r"[^.?!]{0,40}\b(?:poema|poesia|cancion|cuento|historia\s+de|novela|chiste|ensayo|redaccion|carta\s+de\s+amor|"
        r"discurso\s+de\s+boda|rap|haiku|poem|song|story|essay|joke|love\s+letter|novel)\b", _F)),
    ("personal_correspondence", re.compile(
        r"\b(?:correo|email|e-mail|mensaje|carta|whatsapp)\b[^.?!]{0,40}\b(?:personal|a\s+mi\s+(?:jefe|novi[oa]|pareja|madre|"
        r"padre|amig[oa]|casero)|para\s+mi\s+(?:jefe|novi[oa]|pareja|madre|padre|amig[oa]|casero)|aumento\s+de\s+sueldo|"
        r"subida\s+de\s+sueldo|renuncia|dimision|vacaciones\s+personales|raise)\b", _F)),
    ("homework", re.compile(
        r"\b(?:mi\s+tarea|mis\s+deberes|tarea\s+de\s+(?:la\s+|mi\s+)?(?:universidad|clase|escuela|colegio|facultad|master)|"
        r"trabajo\s+de\s+(?:la\s+|mi\s+)?universidad|deberes\s+de|"
        r"mi\s+tesis|mi\s+examen|my\s+homework|my\s+assignment|my\s+thesis)\b", _F)),
    ("code_for_other_project", re.compile(
        r"\b(?:escrib\w*|hazme|genera\w*|programa\w*|write|code|debug\w*|corrige|arregla)\b[^.?!]{0,50}\b(?:codigo|script|programa|"
        r"funcion|clase|query\s+sql|consulta\s+sql|regex|code|function|python|javascript|java|c\+\+|react|html|css)\b", _F)),
    ("therapy_or_personal_advice", re.compile(
        r"\b(?:se\s+mi\s+(?:terapeuta|psicolog\w*|amig[oa]|novi[oa])|necesito\s+(?:terapia|desahogarme)|mi\s+(?:novi[oa]|pareja|ex)\s+"
        r"(?:me\s+dejo|me\s+engana)|consejos?\s+(?:amorosos?|de\s+pareja|sentimentales?)|be\s+my\s+(?:therapist|friend))\b", _F)),
    ("personal_errand", re.compile(
        r"\b(?:receta\s+de|plan\s+de\s+(?:dieta|entrenamiento|viaje)|itinerario|reserva\w*\s+(?:un\s+)?(?:hotel|vuelo|restaurante)|"
        r"que\s+pelicula|recomiend\w*\s+(?:una\s+)?(?:pelicula|serie|libro|restaurante)|horoscopo|apuestas?|"
        r"recipe\s+for|book\s+a\s+(?:flight|hotel))\b", _F)),
]


def detect_personal_task(text: str) -> list[str]:
    candidate = plain(text)
    return sorted({name for name, rule in _PERSONAL_RULES if rule.search(candidate)})


# ---------------------------------------------------------------- active breach probing

_BREACH = re.compile(r"\b(?:brecha\w*|breach\w*|filtracion\w*|fuga\s+de\s+datos|incidente\s+de\s+seguridad|data\s+leak|"
                     r"ciberataque|ransomware|hackeo|acceso\s+no\s+autorizado|violacion\s+de\s+(?:datos|seguridad))\b", _F)
_SPECIFIC = re.compile(r"\b(?:esta|esa|la\s+(?:actual|reciente|ultima|de\s+(?:ayer|la\s+semana|este\s+mes))|actual|activa|en\s+curso|"
                       r"abierta|reciente|que\s+(?:tenemos|hubo|paso|estamos\s+investigando)|en\s+investigacion|investigando|"
                       r"nuestra|ongoing|current|active|under\s+investigation|recent|last\s+week)\b", _F)
_DETAIL = re.compile(r"\b(?:cuando\s+se\s+(?:descubri\w*|detect\w*|produj\w*)|cuando\s+(?:fue|ocurrio|paso)|cuant[oa]s\s+(?:registros|"
                     r"pacientes|afectad\w*|datos|personas|historias)|que\s+clinica|cual\s+clinica|en\s+que\s+clinica|que\s+sede|"
                     r"quien\s+(?:fue|lo\s+hizo|la\s+causo)|que\s+datos\s+se|alcance\s+de|que\s+sistemas|afectad\w*|detalles\s+de|"
                     r"how\s+many\s+records|which\s+clinic|when\s+was\s+it\s+discovered|who\s+did)\b", _F)
_GENERIC = re.compile(r"\b(?:plazo|plazos|como\s+(?:se\s+)?notific\w*|procedimiento|protocolo|que\s+(?:hay\s+que|debo|debemos)\s+hacer|"
                      r"requisito\w*|obligacion\w*|en\s+general|deadline|how\s+to\s+(?:report|notify)|procedure)\b", _F)


def mentions_breach(text: str) -> bool:
    return bool(_BREACH.search(plain(text)))


def detect_breach_probe(text: str, *, breach_topic_in_conversation: bool = False) -> list[str]:
    """Details of a specific or active breach are never disclosed; generic notification
    procedure questions are in domain. In a conversation that already touched a breach, a
    bare follow-up like "¿cuántos registros?" is the gradual extraction the BRIEFING describes."""
    candidate = plain(text)
    if _GENERIC.search(candidate) and not _DETAIL.search(candidate):
        return []
    fired = []
    if _BREACH.search(candidate) and (_DETAIL.search(candidate) or _SPECIFIC.search(candidate)):
        fired.append("breach_details")
    elif breach_topic_in_conversation and _DETAIL.search(candidate):
        fired.append("breach_gradual_extraction")
    return fired


# ---------------------------------------------------------------- confidential vendor contracts

_AGREEMENT = re.compile(r"\b(?:baa|dpa|business\s+associate|data\s+processing\s+agreement|acuerdo\s+de\s+(?:asociado|"
                        r"procesamiento|tratamiento)|contrato\s+con)\b", _F)
_COMMERCIAL = re.compile(r"\b(?:terminos|condiciones|clausula\w*|tarifa\w*|precio\w*|importe|cuanto\s+(?:paga\w*|cuesta|cobra\w*)|"
                         r"comercial\w*|penalizacion\w*|descuento\w*|rate|pricing|terms|fees?|cuota\s+mensual|monthly)\b", _F)


def detect_confidential_contract(text: str) -> list[str]:
    candidate = plain(text)
    return ["vendor_agreement_terms"] if _AGREEMENT.search(candidate) and _COMMERCIAL.search(candidate) else []


# ---------------------------------------------------------------- patient identifiers / quasi-identifiers

_CONCEPT_KEYWORDS = {"medical_record_number", "date_of_birth", "insurance_member_id"}
DIRECT_IDENTIFIERS = {"patient_name", "patient_reference", "medical_record_number", "ssn", "nhs_number",
                      "national_insurance_number", "insurance_member_id", "long_identifier", "phone_number", "email",
                      "uk_postcode", "date_of_birth", "personal_date"}
_AGE = re.compile(r"\b\d{1,3}\s*(?:anos|ano|a\.|years?\s+old|y/?o|yo)\b|\b(?:de|tiene)\s+\d{1,3}\s+anos\b", _F)
_CONDITION = re.compile(r"\b(?:diagnostic\w*|diagnosis|diagnosed|padece|sufre\s+de|enfermedad|condicion|cancer|diabetes|vih|hiv|"
                        r"hepatitis|embarazada|pregnant|tratamiento\s+(?:de|para)|resultado\w*\s+de)\b", _F)
_CITIES = (r"(?:austin|dallas|houston|san\s+antonio|el\s+paso|fort\s+worth|miami|orlando|tampa|jacksonville|atlanta|savannah|"
           r"augusta|londres|london|manchester|camden|islington)")
_LOCATION = re.compile(rf"\b(?:(?:clinica|sede|centro|consulta)\s+(?:de|en)\s+{_CITIES}|en\s+{_CITIES}|in\s+{_CITIES})\b", _F)
_NOT_A_NAME = r"(?!(?:Nuev|New|Experience|Adult|Menor|Pediatr|Care|Portal|Intake|Services?\b|Relations|Safety|Access)[a-z]*\b)"
_FIRST_NAME_IN_CASE = re.compile(r"\b(?i:paciente|patient|caso\s+de|case\s+of)s?\b[\s,:-]+(?:(?i:llamad[oa]|named|que\s+se\s+llama)\s+)?"
                                 r"(?:(?i:sr|sra|srta|mr|mrs|ms|dr|dra)\.?\s+)?" + _NOT_A_NAME + r"[A-Z][a-z]{2,}\b", re.UNICODE)
_TITLED_NAME = re.compile(r"\b(?:Sr|Sra|Srta|Mr|Mrs|Ms|Miss)\.?\s+[A-Z][a-z'-]{2,}\b")


def detect_patient_identifiers(text: str) -> list[str]:
    """Direct identifiers (HIPAA Safe Harbor / UK GDPR), or a combination of quasi-identifiers
    that could re-identify a person (BRIEFING §2: age + diagnosis + location…). A generic
    question ("¿puedo compartir un diagnóstico con la aseguradora?") is not blocked."""
    candidate = plain(text)
    direct = sorted({f.category for f in find_phi(text) if f.category in DIRECT_IDENTIFIERS})
    if not _KEYWORD_WITH_VALUE.search(candidate):
        # "historia clínica", "fecha de nacimiento", "póliza"… mentioned without a value are concepts
        # a compliance question is allowed to talk about; with a value they are identifiers.
        direct = [c for c in direct if c not in _CONCEPT_KEYWORDS]
    quasi = [name for name, hit in (("age", _AGE.search(candidate)), ("condition", _CONDITION.search(candidate)),
                                    ("location", _LOCATION.search(candidate)),
                                    ("first_name", _FIRST_NAME_IN_CASE.search(text))) if hit]
    if "first_name" in quasi and "patient_name" not in direct:
        direct.append("patient_name")
    if len(quasi) >= 2:
        direct.append("quasi_identifiers:" + "+".join(quasi))
    return sorted(set(direct))


# ---------------------------------------------------------------- identifiers in the model OUTPUT

# The input guard is conservative (a mention like "historia clínica" is enough to stop a memory);
# an answer may legitimately *talk about* those concepts, so the output guard looks for values:
# a name next to "paciente", identifier numbers, contact data, dates tied to a person, lab values,
# or a quasi-identifier combination that includes an age.
_VALUE_CATEGORIES = {"patient_reference", "ssn", "nhs_number", "national_insurance_number",
                     "phone_number", "email", "uk_postcode", "personal_date", "icd_code"}
_KEYWORD_WITH_VALUE = re.compile(
    r"\b(?:mrn|nhc|historia\s+clinica|medical\s+record|dob|fecha\s+de\s+nacimiento|date\s+of\s+birth|nacid[oa]\s+el|"
    r"poliza|policy|member\s+id|numero\s+de\s+seguro|nhs\s+number|insurance\s+(?:id|number))\b\W{0,6}"
    r"(?:n[uo]\.?\s*)?[:#]?\s*[A-Z0-9/.-]*\d{3,}", _F)
_LAB_VALUE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:mg/dl|mmol/l|g/dl|ui/l|u/l)\b", _F)


def detect_identifiers_in_output(text: str) -> list[str]:
    found = {f.category for f in find_phi(text) if f.category in _VALUE_CATEGORIES}
    candidate = plain(text)
    if _KEYWORD_WITH_VALUE.search(candidate):
        found.add("identifier_value")
    if _LAB_VALUE.search(candidate):
        found.add("lab_value")
    quasi = [name for name, hit in (("age", _AGE.search(candidate)), ("condition", _CONDITION.search(candidate)),
                                    ("location", _LOCATION.search(candidate)),
                                    ("first_name", _FIRST_NAME_IN_CASE.search(text))) if hit]
    if "first_name" in quasi or _TITLED_NAME.search(text):
        found.add("patient_name")
    if "age" in quasi and len(quasi) >= 2:
        found.add("quasi_identifiers:" + "+".join(quasi))
    return sorted(found)
