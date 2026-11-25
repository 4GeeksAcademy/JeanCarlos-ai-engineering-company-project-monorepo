"""Part 3 rules (BRIEFING Hito 9 §2.1, §5, §6, §7): who approves each department, what the
approver sees, which human answers are valid, the iteration limits, and the arbitration triggers
with their fixed arbiter. Everything here is deterministic — no model decides an approval or an
arbitration.

Approvers: the named owner of each department (§2.1) — Tom Callahan (revenue), Dr. Marcus Reid
(clinical), Claire Whitfield (compliance). No extra hierarchy; compliance always approves.

Limits (enforced by the graph and by `validate_decision`, not only documented):
- MAX_REVISION_ROUNDS: `request_changes` per department. When used up, the approver can only
  approve or reject.
- MAX_ARBITRATION_ROUNDS: cycles in which the arbitration node sends sections back to their
  departments. When used up, the named arbiter of the trigger gets a final ruling without
  another revision cycle.

Triggers (§7), detected on structured state — never on free text from an agent:
- phi-detected: `evaluation_results.contains_phi` of a section, or the PHI rules matching its
  stored draft. Arbiter: Claire Whitfield. Rule: hard stop — the section goes back to its
  generator (request_changes), approval of that section is blocked and the synthesizer cannot
  run; when the rounds are used up, Claire discards the ticket or asks for a new run.
- baa-dpa-mismatch: the compliance section lacks the instrument of the client's country (BAA +
  HIPAA for US, DPA + UK GDPR for UK) or any section embeds the other country's instrument (same
  regexes as the HC-BAA-US / HC-DPA-UK evaluator rules). Arbiter: Claire Whitfield. Rule: forced
  request_changes on compliance and on every section that embeds the wrong clause.
- capacity-vs-population: the capacity Dr. Marcus Reid confirms when he approves the clinical
  section (`confirmed_capacity`, a number) is below the population of the contract
  (`rfp_metadata.covered_population`, or the population agreed in an earlier arbitration).
  Evaluated only after Compliance approved and no phi-detected is open. Arbiter: Tom Callahan,
  who chooses: reduce the covered population (Revenue revises its terms) or add sites (Clinical
  revises and re-confirms capacity). Once he reduced it, a section that still quotes the original
  population (a number in its draft) is sent back by rule — the decision of the arbiter is applied,
  not re-discussed.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import agents, evaluators, phi
from .departments import ALWAYS_REQUIRED, DEPARTMENTS

MAX_REVISION_ROUNDS = 2
MAX_ARBITRATION_ROUNDS = 2
ACTIONS = ("approve", "request_changes", "reject")


def _limit(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


def max_revision_rounds() -> int:
    return _limit("RFP_MAX_REVISION_ROUNDS", MAX_REVISION_ROUNDS)


def max_arbitration_rounds() -> int:
    return _limit("RFP_MAX_ARBITRATION_ROUNDS", MAX_ARBITRATION_ROUNDS)


def approver_of(department_id: str) -> str:
    return DEPARTMENTS[department_id].owner


def required_departments(handoff_departments: list[str]) -> list[str]:
    """The active departments of the ticket + compliance, always (§2.1, §5)."""
    ordered = [d for d in DEPARTMENTS if d in set(handoff_departments) | set(ALWAYS_REQUIRED)]
    return ordered


# ================================================================ human answers


class InvalidDecision(ValueError):
    """The human answer cannot enter the graph (wrong approver, action not allowed, PHI...)."""


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "request_changes", "reject"]
    approver: str = Field(min_length=3, max_length=80)
    comment: str | None = Field(default=None, max_length=2000)
    confirmed_capacity: int | None = Field(default=None, gt=0, le=1_000_000)
    submitted_by: str | None = Field(default=None, max_length=64)  # the authenticated account (set by the API)


def _check_comment(comment: str | None, required: bool) -> None:
    if required and len((comment or "").strip()) < 10:
        raise InvalidDecision("Explain the decision in the comment (at least 10 characters).")
    if comment and phi.find_phi(comment):
        raise InvalidDecision("The comment contains patient information; remove it (BRIEFING: no PHI anywhere).")


def validate_decision(packet: dict, raw: Any) -> ApprovalDecision:
    """Validated BEFORE the answer enters the graph (API) and again inside the graph."""
    try:
        decision = ApprovalDecision.model_validate(raw)
    except ValidationError as error:
        raise InvalidDecision(f"Invalid decision: {error.errors()[0]['msg']}") from error
    if decision.approver != packet["approver"]:
        raise InvalidDecision(f"Only {packet['approver']} can approve the {packet['department']} section.")
    if decision.action not in packet["allowed_actions"]:
        raise InvalidDecision(packet["blocked_reasons"].get(decision.action) or f"{decision.action} is not allowed now.")
    _check_comment(decision.comment, decision.action in ("request_changes", "reject"))
    if decision.action == "approve" and packet.get("requires_capacity") and decision.confirmed_capacity is None:
        raise InvalidDecision("Clinical Operations must confirm the staff capacity (people covered) to approve.")
    if decision.action != "approve" and decision.confirmed_capacity is not None:
        raise InvalidDecision("confirmed_capacity is only recorded with an approval.")
    return decision


class ArbiterDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger: Literal["phi-detected", "baa-dpa-mismatch", "capacity-vs-population"]
    arbiter: str = Field(min_length=3, max_length=80)
    resolution: Literal["reduce_population", "add_sites", "discard", "request_new_run"]
    covered_population: int | None = Field(default=None, gt=0, le=1_000_000)
    comment: str | None = Field(default=None, max_length=2000)
    submitted_by: str | None = Field(default=None, max_length=64)  # the authenticated account (set by the API)


def validate_arbiter_decision(pending: dict | None, raw: Any) -> ArbiterDecision:
    if not pending:
        raise InvalidDecision("No arbitration is waiting for a decision.")
    try:
        decision = ArbiterDecision.model_validate(raw)
    except ValidationError as error:
        raise InvalidDecision(f"Invalid arbitration: {error.errors()[0]['msg']}") from error
    if decision.trigger != pending["trigger"]:
        raise InvalidDecision(f"The open arbitration is {pending['trigger']}.")
    if decision.arbiter != pending["arbiter"]:
        raise InvalidDecision(f"The fixed arbiter of {pending['trigger']} is {pending['arbiter']} (BRIEFING §7).")
    if decision.resolution not in pending["options"]:
        raise InvalidDecision(f"Allowed resolutions: {', '.join(pending['options'])}.")
    if decision.resolution == "reduce_population":
        capacity = (pending.get("evidence") or {}).get("confirmed_capacity")
        if decision.covered_population is None or (capacity and decision.covered_population > capacity):
            raise InvalidDecision(f"Give the reduced covered population (at most the confirmed capacity, {capacity}).")
    _check_comment(decision.comment, decision.resolution in ("discard", "request_new_run"))
    return decision


# ================================================================ triggers (§7)


@dataclass(frozen=True)
class Trigger:
    id: str
    arbiter_department: str
    rule: str
    blocking: bool  # blocks the approval of the affected section and the synthesizer


TRIGGERS: dict[str, Trigger] = {t.id: t for t in (
    Trigger("phi-detected", "compliance", "Hard stop: send the section back (request_changes), block its approval and "
            "the synthesizer; when the rounds are used up, Compliance discards the ticket or asks for a new run.", True),
    Trigger("baa-dpa-mismatch", "compliance", "Force request_changes on compliance and on every section embedding the "
            "wrong clause until the instrument matches the client's country.", True),
    Trigger("capacity-vs-population", "revenue", "After Compliance confirmed no PHI: reduce the covered population or "
            "add sites; force the revision of revenue and/or clinical.", False),
)}


def arbiter_of(trigger: str) -> str:
    return DEPARTMENTS[TRIGGERS[trigger].arbiter_department].owner


def instrument_problems(department_id: str, draft: str | None, country: str | None) -> list[str]:
    """The structured BAA/DPA check (same patterns as the HC-BAA-US / HC-DPA-UK evaluator rules)."""
    draft = draft or ""
    problems = []
    if country == "US":
        if department_id == "compliance" and not (evaluators._BAA.search(draft) and evaluators._HIPAA.search(draft)):
            problems.append("missing BAA clause under HIPAA")
        if evaluators._DPA.search(draft) or evaluators._UK_GDPR.search(draft):
            problems.append("embeds a DPA / UK GDPR instrument for a US client")
    elif country == "UK":
        if department_id == "compliance" and not (evaluators._DPA.search(draft) and evaluators._UK_GDPR.search(draft)):
            problems.append("missing DPA clause referencing UK GDPR")
        if evaluators._BAA.search(draft) or evaluators._HIPAA.search(draft):
            problems.append("embeds a BAA / HIPAA instrument for a UK client")
    return problems


def section_phi(section) -> list[str]:
    result = section.evaluation_results or {}
    categories = set(phi.find_phi(section.draft_content))
    if result.get("contains_phi") and not categories:
        categories.add("flagged_by_evaluator")
    return sorted(categories)


def draft_open_questions(draft: str | None) -> list[str]:
    """The bullets under "### Open questions" of the section."""
    match = re.search(r"(?ims)^#{2,4}\s*open questions\s*$(.*?)(?=^#{1,4}\s|\Z)", draft or "")
    return [line.strip()[2:].strip() for line in (match.group(1) if match else "").splitlines()
            if line.strip()[:2] in ("- ", "* ")]


def agreed_population(ticket, metadata) -> int | None:
    agreed = [e["agreed_population"] for e in (ticket.arbitration or []) if e.get("agreed_population")]
    return agreed[-1] if agreed else (metadata.covered_population if metadata else None)


def stale_population(ticket, metadata, sections: list) -> dict[str, str]:
    """After a reduce_population ruling: the sections whose draft still quotes the original population."""
    agreed = [e["agreed_population"] for e in (ticket.arbitration or []) if e.get("agreed_population")]
    original = str(metadata.covered_population) if metadata and metadata.covered_population else None
    if not agreed or not original or str(agreed[-1]) == original:
        return {}
    return {s.department_id: original for s in sections if original in agents.numbers_in(s.draft_content or "")}


def detect_triggers(ticket, metadata, sections: list) -> list[dict]:
    """Every BRIEFING §7 trigger present in the structured state of the ticket right now."""
    country = metadata.client_country if metadata else None
    found: list[dict] = []
    phi_departments = {s.department_id: section_phi(s) for s in sections if section_phi(s)}
    if phi_departments:
        found.append({"trigger": "phi-detected", "departments": sorted(phi_departments),
                      "evidence": {"categories": phi_departments}})
    mismatch = {s.department_id: instrument_problems(s.department_id, s.draft_content, country) for s in sections}
    mismatch = {d: p for d, p in mismatch.items() if p}
    if mismatch:
        found.append({"trigger": "baa-dpa-mismatch", "departments": sorted(mismatch, key=list(DEPARTMENTS).index),
                      "evidence": {"country": country, "problems": mismatch}})
    rows = {s.department_id: s for s in sections}
    clinical, compliance = rows.get("clinical"), rows.get("compliance")
    population = agreed_population(ticket, metadata)
    if (clinical and clinical.approval_status == "approved" and clinical.confirmed_capacity and population
            and clinical.confirmed_capacity < population and compliance and compliance.approval_status == "approved"
            and not phi_departments):
        found.append({"trigger": "capacity-vs-population", "departments": ["revenue", "clinical"],
                      "evidence": {"confirmed_capacity": clinical.confirmed_capacity, "covered_population": population,
                                   "population_unit": metadata.population_unit if metadata else None}})
    stale = stale_population(ticket, metadata, sections)
    if stale:  # the arbiter already decided: applied by rule, not asked again
        found.append({"trigger": "capacity-vs-population", "departments": sorted(stale, key=list(DEPARTMENTS).index),
                      "by_rule": True, "evidence": {"stale_population": next(iter(stale.values())),
                                                    "agreed_population": population,
                                                    "population_unit": metadata.population_unit}})
    for item in found:
        item.setdefault("by_rule", TRIGGERS[item["trigger"]].blocking)
        item["arbiter"] = arbiter_of(item["trigger"])
        item["arbiter_department"] = TRIGGERS[item["trigger"]].arbiter_department
        item["rule"] = TRIGGERS[item["trigger"]].rule
    return found


def feedback_for(trigger: dict, department_id: str) -> str:
    """What the arbitration rule tells the generator of a section it sends back."""
    if trigger["trigger"] == "phi-detected":
        return ("[arbitration phi-detected — Claire Whitfield] The section contained patient information. Remove every "
                "patient name, diagnosis or identifier; describe services in aggregate terms only.")
    if trigger["trigger"] == "baa-dpa-mismatch":
        problems = "; ".join(trigger["evidence"]["problems"].get(department_id, []))
        instrument = {"US": "a Business Associate Agreement (BAA) under HIPAA",
                      "UK": "a Data Processing Agreement (DPA) under UK GDPR"}.get(trigger["evidence"]["country"], "")
        return (f"[arbitration baa-dpa-mismatch — Claire Whitfield] {problems}. The only instrument for this client is "
                f"{instrument}; do not mention the other country's instrument.")
    if trigger["trigger"] == "capacity-vs-population" and trigger.get("by_rule"):
        evidence = trigger["evidence"]
        unit = evidence.get("population_unit") or "people"
        return (f"[arbitration capacity-vs-population — Tom Callahan] The arbiter set the covered population to "
                f"{evidence['agreed_population']} {unit}. Replace every mention of {evidence['stale_population']} "
                f"{unit} with {evidence['agreed_population']} {unit}; do not mention the original population.")
    raise ValueError(trigger["trigger"])


# ================================================================ what the approver sees


def _evaluation_summary(result: dict | None) -> dict:
    result = result or {}
    readability = result.get("readability") or {}
    relevance = result.get("relevance") or {}
    compliance = result.get("compliance") or {}
    return {
        "overall_pass": result.get("overall_pass"),
        "iteration": result.get("iteration"),
        "readability": {"pass": readability.get("pass"), "score": readability.get("score"),
                        "grade": (readability.get("details") or {}).get("flesch_kincaid_grade")},
        "relevance": {"pass": relevance.get("pass"), "score": relevance.get("score"),
                      "covered": relevance.get("covered_aspects", []), "missing": relevance.get("missing_aspects", [])},
        "compliance": {"pass": compliance.get("pass"),
                       "violations": [v.get("rule_id") for v in compliance.get("violations", [])]},
        "contains_phi": bool(result.get("contains_phi")),
    }


def build_packet(ticket, metadata, section, triggers: list[dict], revision_rounds: int) -> dict:
    """Design question 2 — the minimum the approver needs to decide without re-reading the RFP:
    their section, the verdict of the three evaluators (and what is missing), whether it hit the
    Part 2 iteration limit, the open questions, the triggers that touch it, the client facts that
    the section must match (country, currency, instrument, population) and what they may do."""
    department_id = section.department_id
    country = metadata.client_country if metadata else None
    mine = [t for t in triggers if department_id in t["departments"]]
    blocking = [t for t in mine if t.get("by_rule")]
    limit = max_revision_rounds()
    allowed = list(ACTIONS)
    blocked: dict[str, str] = {}
    if blocking:
        allowed.remove("approve")
        blocked["approve"] = ("Approval blocked by " + ", ".join(t["trigger"] for t in blocking)
                              + f" (arbiter {blocking[0]['arbiter']}): request changes or reject.")
    if revision_rounds >= limit:
        allowed.remove("request_changes")
        blocked["request_changes"] = f"The revision limit ({limit}) is used up: approve or reject."
    population = agreed_population(ticket, metadata)
    return {
        "ticket_id": section.ticket_id,
        "department_id": department_id,
        "department": DEPARTMENTS[department_id].name,
        "approver": approver_of(department_id),
        "approver_role": DEPARTMENTS[department_id].owner_role,
        "client": {"name": metadata.client_name if metadata else None, "country": country,
                   "currency": metadata.currency if metadata else None,
                   "instrument": {"US": "BAA (HIPAA)", "UK": "DPA (UK GDPR)"}.get(country or ""),
                   "covered_population": population, "population_unit": metadata.population_unit if metadata else None},
        "draft": section.draft_content,
        "words": len(re.findall(r"\b\w+\b", section.draft_content or "")),
        "generation_status": section.generation_status,
        "needs_human_review": section.generation_status == "needs_human_review",
        "iterations": section.iterations,
        "evaluation": _evaluation_summary(section.evaluation_results),
        "open_questions": draft_open_questions(section.draft_content) or section.open_questions or [],
        "triggers": [{k: t[k] for k in ("trigger", "arbiter", "rule", "evidence")} for t in mine],
        "revision_rounds": revision_rounds,
        "max_revision_rounds": limit,
        "allowed_actions": allowed,
        "blocked_reasons": blocked,
        "requires_capacity": department_id == "clinical" and bool(population),
    }
