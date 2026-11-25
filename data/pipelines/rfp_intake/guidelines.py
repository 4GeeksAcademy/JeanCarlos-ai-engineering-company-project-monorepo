"""HealthCore guidelines for the proposal sections (BRIEFING Hito 9 §2.1 and §5), as data.

The compliance evaluator checks every generated section against these rules — each violation
carries the rule id, so the result is verifiable and never a subjective opinion of an agent.
The generators receive the same rules and the expected format of their section, so a section
that follows its instructions passes compliance on the first try.

Sections are drafted in English: they are the client-facing economic proposal for US / UK
institutions, and the readability formulas (py-readability-metrics) are calibrated for English.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_ITERATIONS = 3  # generator → evaluators cycles per section (RFP_MAX_ITERATIONS overrides it)

# Readability of a client-facing proposal section (py-readability-metrics needs >= 100 words).
MIN_WORDS, MAX_WORDS = 120, 550
MAX_GRADE = 14.0  # Flesch-Kincaid grade: an institutional buyer, not an academic paper
MIN_READING_EASE = 30.0  # Flesch reading ease: below 30 is "very difficult"
LONG_SENTENCE_WORDS = 28


@dataclass(frozen=True)
class Rule:
    id: str
    text: str  # what the BRIEFING says
    source: str  # where it comes from


RULES: dict[str, Rule] = {r.id: r for r in (
    Rule("HC-PHI-01", "No section may contain patient names, diagnoses or any patient identifier, real or illustrative.",
         "BRIEFING §5 (1) y restricción no negociable HIPAA / UK GDPR"),
    Rule("HC-BAA-US", "Every proposal for a US client includes a Business Associate Agreement (BAA) clause under HIPAA; "
         "no section offers a DPA / UK GDPR instrument to a US client.", "BRIEFING §5 (2) y trigger baa-dpa-mismatch"),
    Rule("HC-DPA-UK", "Every proposal for a UK client includes a Data Processing Agreement (DPA) clause referencing UK GDPR; "
         "no section offers a BAA / HIPAA instrument to a UK client.", "BRIEFING §5 (3) y trigger baa-dpa-mismatch"),
    Rule("HC-CUR-01", "Prices are quoted in USD for US clients and in GBP for UK clients (from client_country); "
         "no other currency appears.", "BRIEFING §5 (4)"),
    Rule("HC-CMP-01", "Compliance approval is always mandatory before closing: no section may say it is optional, "
         "waived or not required, and the compliance section states that Claire Whitfield must approve.",
         "BRIEFING §5 (5) y §6"),
    Rule("HC-FIG-01", "Never invent headcount, volumes, prices or dates: every figure comes from the RFP analysis "
         "(Part 1 handoff) or HealthCore's own data; anything missing is written as an open question.",
         "BRIEFING §2.3"),
    Rule("HC-FMT-01", "The section follows the format of its department (required headings, open questions listed).",
         "BRIEFING §2.1 (qué aporta cada departamento)"),
)}


@dataclass(frozen=True)
class SectionFormat:
    title: str
    headings: tuple[str, ...]  # required "###" headings, in order
    guidance: str  # what goes in the section (BRIEFING §2.1)


OPEN_QUESTIONS_HEADING = "Open questions"


def section_format(department_id: str, country: str | None) -> SectionFormat:
    """The expected format of each department's section of the economic proposal."""
    if department_id == "revenue":
        return SectionFormat(
            title="Commercial Terms — Revenue Cycle",
            headings=("Pricing and currency", "Payment structure", "Contract term", OPEN_QUESTIONS_HEADING),
            guidance="Financial terms, currency and payment structure (Tom Callahan, owner of the ticket). Quote in the "
                     "client's currency; when the RFP gives no budget or volume, write the pricing model and leave the "
                     "amount as '[amount to be confirmed by Revenue Cycle]'.")
    if department_id == "clinical":
        return SectionFormat(
            title="Clinical Delivery — Clinical Operations",
            headings=("Sites and coverage", "Staffing and capacity", "Clinical services", OPEN_QUESTIONS_HEADING),
            guidance="Clinical feasibility (Dr. Marcus Reid): which HealthCore clinics and what staff capacity can cover "
                     "the contract. Never assume available capacity: what must be confirmed goes to open questions.")
    clause = {"US": "Business Associate Agreement (BAA)", "UK": "Data Processing Agreement (DPA) under UK GDPR"}.get(
        country or "", "Data protection agreement (BAA or DPA, to be confirmed)")
    return SectionFormat(
        title="Data Protection and Compliance — Compliance and Data Governance",
        headings=("Regulatory framework", clause, "Compliance approval", OPEN_QUESTIONS_HEADING),
        guidance="Regulatory review (Claire Whitfield): HIPAA for US clients with a BAA clause, UK GDPR for UK clients "
                 "with a DPA clause; state that Compliance approval is required before signature.")


def rules_for_prompt(country: str | None) -> str:
    applicable = ["HC-PHI-01", "HC-CUR-01", "HC-CMP-01", "HC-FIG-01", "HC-FMT-01"]
    applicable.insert(1, {"US": "HC-BAA-US", "UK": "HC-DPA-UK"}.get(country or "", "HC-BAA-US"))
    if not country:
        applicable.insert(2, "HC-DPA-UK")
    return "\n".join(f"- {RULES[r].id}: {RULES[r].text}" for r in applicable)
