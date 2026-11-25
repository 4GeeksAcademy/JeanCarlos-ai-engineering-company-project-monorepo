"""RFP response generation (Hito 9 Parte 2) with a scripted model: one generator per department,
three evaluators in parallel (readability, relevance, compliance against the BRIEFING §5 rules),
the generator ↔ evaluator loop with its iteration limit, needs_human_review, ticket progress and
the handoff to Part 3. Part 2 starts from tickets that Part 1 left in its queue.

    python -m pytest tests/pipelines/test_rfp_response.py
"""

import json

import pytest
from sqlmodel import Session, select

from data.pipelines.rfp_intake import evaluators, guidelines, response_graph, response_runner, runner, store
from data.pipelines.rfp_intake.evaluators import evaluate_compliance, evaluate_readability, evaluate_relevance
from data.pipelines.rfp_intake.generators import GENERATORS, ClinicalGenerator, ComplianceGenerator, RevenueGenerator
from data.pipelines.rfp_intake.handoff import RESPONSE_CONTRACT
from data.pipelines.rfp_intake.models import RfpDepartmentSection, RfpTicket
from tests.pipelines.rfp_fixtures import FORMAL, INFORMAL, INVALID, WITH_PHI, draft, engine, model  # noqa: F401

US_ALLOWED = evaluators.allowed_figures("Meridian 800 employees August 23, 2026 12 months")


def analysed(engine, path):  # noqa: F811
    ticket = store.receive(engine, path.read_bytes(), created_by="test")
    runner.run_intake(engine, ticket.ticket_id)
    return ticket.ticket_id


def sections_of(engine, ticket_id):  # noqa: F811
    with Session(engine) as session:
        return {s.department_id: s for s in session.exec(select(RfpDepartmentSection)
                                                         .where(RfpDepartmentSection.ticket_id == ticket_id))}


def generator_calls(model, department_id):  # noqa: F811
    return [c for c in model.calls if c[0] == f"generator:{department_id}"]


# ================================================================ generators


def context(country="US"):
    metadata = {"client_name": "Meridian Manufacturing, Inc.", "client_country": country, "covered_population": 800,
                "population_unit": "employees", "program_types": ["occupational_health"], "budget_range": None}
    return {"ticket_id": "t-1", "department_id": "revenue", "metadata": metadata,
            "currency": {"US": "USD", "UK": "GBP"}[country], "required_clauses": ["BAA"],
            "summary": "Meridian pide una propuesta de salud ocupacional para 800 empleados.",
            "workstream": {"department_id": "revenue", "key_aspects": [{"aspect": "Moneda y términos", "detail": "USD"}],
                           "requests": ["Modelo de precios"], "open_questions": ["Presupuesto no indicado"]},
            "conflicts": [], "excerpts": [{"id": 3, "text": "Pricing should be per employee per month."}],
            "max_iterations": 3, "allowed_figures": []}


def test_each_department_has_its_own_generator():
    assert {d: type(g) for d, g in GENERATORS.items()} == {"revenue": RevenueGenerator, "clinical": ClinicalGenerator,
                                                          "compliance": ComplianceGenerator}
    prompts = {d: g.system_prompt(context()) for d, g in GENERATORS.items()}
    assert "## Commercial Terms — Revenue Cycle" in prompts["revenue"] and "Tom Callahan" in prompts["revenue"]
    assert "### Staffing and capacity" in prompts["clinical"] and "Dr. Marcus Reid" in prompts["clinical"]
    assert "### Business Associate Agreement (BAA)" in prompts["compliance"]
    assert "Data Processing Agreement (DPA) under UK GDPR" in GENERATORS["compliance"].system_prompt(context("UK"))
    assert "HC-BAA-US" in prompts["revenue"] and "HC-DPA-UK" not in prompts["revenue"]


def test_revenue_generator_drafts_from_the_part1_handoff(model):  # noqa: F811
    result = GENERATORS["revenue"].generate(context())
    assert result["content"].startswith("## Commercial Terms — Revenue Cycle") and "USD" in result["content"]
    agent, system, user = model.calls[-1]
    assert agent == "generator:revenue"
    assert "Moneda y términos" in user and "800" in user and "Meridian pide una propuesta" in user  # handoff + summary
    assert "<documento_rfp>" in user and "[P3]" in user  # its excerpts, as data
    assert "Quote currency for this client: USD" in user


def test_generator_receives_the_feedback_on_a_revision(model):  # noqa: F811
    GENERATORS["revenue"].generate(context(), feedback="[compliance HC-CUR-01] State the currency as USD.",
                                   previous_draft="## Commercial Terms — Revenue Cycle\nold draft")
    user = model.calls[-1][2]
    assert "NO pasó la evaluación" in user and "HC-CUR-01" in user and "old draft" in user


def test_clinical_generator_only_offers_clinics_in_the_client_country():
    us = ClinicalGenerator().company_context(context("US"))[0]
    uk = ClinicalGenerator().company_context(context("UK"))[0]
    assert "us-tx-001" in us and "uk-lon-001" not in us
    assert "uk-man-001" in uk and "us-tx-001" not in uk and "3 clinics" in uk


# ================================================================ evaluators


def test_readability_passes_a_clear_section_and_fails_a_dense_one():
    good = evaluate_readability(draft("revenue", "US"))
    assert good.passed and good.score <= guidelines.MAX_GRADE
    dense = "## Commercial Terms — Revenue Cycle\n" + " ".join(
        ["Notwithstanding the aforementioned considerations regarding institutional procurement methodologies, the "
         "comprehensive remuneration framework contemplates differentiated capitation arrangements, supplementary "
         "utilization-contingent reimbursement mechanisms and multidimensional performance-adjusted reconciliation "
         "procedures applicable throughout the contractual relationship between the parties."] * 4)
    bad = evaluate_readability(dense)
    assert not bad.passed and bad.score > guidelines.MAX_GRADE
    assert bad.details["long_sentences"] and any("Flesch-Kincaid" in i for i in bad.details["issues"])
    short = evaluate_readability("## Commercial Terms\nToo short.")
    assert not short.passed and "too short" in short.details["issues"][0]


def test_relevance_requires_a_verbatim_quote_for_each_covered_aspect(model):  # noqa: F811
    aspects = [{"aspect": "Moneda y términos", "detail": "USD"}, {"aspect": "Plazo", "detail": "12 meses"}]
    model.overrides["evaluator:relevance"] = {"aspects": [
        {"index": 0, "covered": True, "evidence": "All prices in this proposal are quoted in USD"},
        {"index": 1, "covered": True, "evidence": "We guarantee a 12 month fixed price"}]}  # not in the draft
    result = evaluate_relevance("Revenue Cycle", draft("revenue", "US"), aspects)
    assert not result.passed and result.missing_aspects == ["Plazo"] and result.covered_aspects == ["Moneda y términos"]
    assert result.details["unverified_evidence"] == ["Plazo"] and result.score == 0.5


def test_relevance_falls_back_to_keyword_coverage_when_the_judge_is_down(model):  # noqa: F811
    model.overrides["evaluator:relevance"] = RuntimeError("gateway down")
    result = evaluate_relevance("Revenue Cycle", draft("revenue", "US"),
                                [{"aspect": "Payment structure", "detail": "monthly invoice"}])
    assert result.evaluator == "fallback" and result.passed


def test_compliance_passes_a_section_that_follows_the_briefing():
    for department in ("revenue", "clinical", "compliance"):
        for country in ("US", "UK"):
            result = evaluate_compliance(department, draft(department, country), country=country, allowed=US_ALLOWED)
            assert result.passed, (department, country, result.violations)


def test_compliance_fails_a_us_proposal_quoted_in_gbp():
    """BRIEFING §5: USD for US clients — a fixture, one failing assertion."""
    bad = draft("revenue", "US").replace("quoted in USD", "quoted in GBP (£)")
    result = evaluate_compliance("revenue", bad, country="US", allowed=US_ALLOWED)
    assert result.passed is False
    assert [v.rule_id for v in result.violations] == ["HC-CUR-01", "HC-CUR-01"]


def test_compliance_fails_a_uk_proposal_that_offers_a_baa_instead_of_a_dpa():
    """BRIEFING §5 + trigger baa-dpa-mismatch: a UK client gets a DPA under UK GDPR."""
    bad = draft("compliance", "US").replace("HIPAA", "HIPAA")  # the US clause sent to a UK client
    result = evaluate_compliance("compliance", bad, country="UK", allowed=US_ALLOWED)
    assert result.passed is False and {v.rule_id for v in result.violations} >= {"HC-DPA-UK"}


def test_compliance_rejects_patient_identifiers_and_marks_contains_phi():
    bad = draft("clinical", "US") + "\nFor example, Patient: Jane Placeholder, DOB: 03/04/1980, was treated last year.\n"
    result = evaluate_compliance("clinical", bad, country="US", allowed=US_ALLOWED)
    assert result.passed is False and result.contains_phi is True
    assert result.violations[0].rule_id == "HC-PHI-01" and "Jane" not in result.violations[0].message


def test_compliance_rejects_a_promise_to_skip_compliance_approval_and_invented_figures():
    bad = (draft("revenue", "US") + "\nCompliance review is optional for this contract. We will serve 1,250 employees "
           "at USD 45 per employee.\n")
    rules = [v.rule_id for v in evaluate_compliance("revenue", bad, country="US", allowed=US_ALLOWED).violations]
    assert "HC-CMP-01" in rules and "HC-FIG-01" in rules


def test_compliance_requires_the_section_format():
    bad = draft("compliance", "US").replace("### Compliance approval", "### Approval")
    result = evaluate_compliance("compliance", bad, country="US", allowed=US_ALLOWED)
    assert [v.rule_id for v in result.violations] == ["HC-FMT-01"] and "Compliance approval" in result.violations[0].message


def test_judge_builds_the_evaluation_result_with_actionable_feedback(model):  # noqa: F811
    bad = draft("revenue", "US").replace("quoted in USD", "quoted in GBP")
    result = evaluators.judge("t-1", "revenue", 1, evaluate_readability(bad),
                              evaluate_relevance("Revenue Cycle", bad, [{"aspect": "Moneda", "detail": "USD"}]),
                              evaluate_compliance("revenue", bad, country="US", allowed=US_ALLOWED)).as_dict()
    assert set(result) >= {"section_id", "department_id", "readability", "relevance", "compliance", "overall_pass",
                           "feedback_for_generator", "contains_phi"}
    assert result["readability"]["pass"] is True and result["compliance"]["pass"] is False
    assert result["compliance"]["rule_ids"][:2] == ["HC-PHI-01", "HC-BAA-US"]
    assert result["overall_pass"] is False and "[compliance HC-CUR-01]" in result["feedback_for_generator"]


# ================================================================ the cycle, end to end


def test_part2_starts_from_the_part1_handoff_and_all_sections_pass(engine, model):  # noqa: F811
    ticket_id = analysed(engine, FORMAL)
    calls_before = len(model.calls)
    assert response_runner.run_generation(engine, ticket_id) == "en_evaluación"
    agents_called = [c[0] for c in model.calls[calls_before:]]
    assert "classifier" not in agents_called and "extractor" not in agents_called  # the PDF is not re-read
    assert sorted(a for a in agents_called if a.startswith("generator:")) == [
        "generator:clinical", "generator:compliance", "generator:revenue"]
    assert agents_called.count("evaluator:relevance") == 3
    clinical_prompt = generator_calls(model, "clinical")[0][2]
    assert "Capacidad clínica" in clinical_prompt and "Moneda y términos" not in clinical_prompt  # only its workstream
    sections = sections_of(engine, ticket_id)
    assert all(s.generation_status == "passed" and s.iterations == 1 for s in sections.values())
    assert all(s.evaluation_results["overall_pass"] and s.draft_content for s in sections.values())
    with Session(engine) as session:
        ticket = session.get(RfpTicket, ticket_id)
    assert ticket.status == "en_evaluación" and ticket.stage == "generated" and ticket.ready_for_approval
    assert not ticket.ready_for_generation  # taken from the Part 1 queue
    handoff = ticket.response_handoff
    assert handoff["contract"] == RESPONSE_CONTRACT and handoff["needs_human_review"] == [] and handoff["currency"] == "USD"
    assert {s["department_id"]: (s["approver"], bool(s["draft_content"]), s["evaluation_result"]["overall_pass"])
            for s in handoff["sections"]} == {"revenue": ("Tom Callahan", True, True),
                                              "clinical": ("Dr. Marcus Reid", True, True),
                                              "compliance": ("Claire Whitfield", True, True)}
    assert handoff["compliance_approval_required"] is True and handoff["signals"] == []


def test_a_failed_section_goes_back_to_its_generator_with_the_feedback(engine, model):  # noqa: F811
    ticket_id = analysed(engine, INFORMAL)  # UK client
    wrong = draft("revenue", "UK").replace("quoted in GBP", "quoted in USD")
    model.overrides["generator:revenue"] = [{"content": wrong}, {"content": draft("revenue", "UK")}]
    assert response_runner.run_generation(engine, ticket_id) == "en_evaluación"
    calls = generator_calls(model, "revenue")
    assert len(calls) == 2 and len(generator_calls(model, "clinical")) == 1
    assert "[compliance HC-CUR-01]" in calls[1][2] and "quoted in USD" in calls[1][2]  # feedback + previous draft
    revenue = sections_of(engine, ticket_id)["revenue"]
    assert revenue.generation_status == "passed" and revenue.iterations == 2
    assert [h["evaluation"]["overall_pass"] for h in revenue.evaluation_history] == [False, True]
    assert revenue.evaluation_history[0]["evaluation"]["compliance"]["violations"][0]["rule_id"] == "HC-CUR-01"


def test_iteration_limit_marks_the_section_for_human_review_and_keeps_it(engine, model):  # noqa: F811
    ticket_id = analysed(engine, FORMAL)
    never_passes = draft("compliance", "US").replace("Claire Whitfield must approve", "The team may approve")
    model.overrides["generator:compliance"] = {"content": never_passes}
    assert response_runner.run_generation(engine, ticket_id) == "needs_human_review"
    assert len(generator_calls(model, "compliance")) == guidelines.MAX_ITERATIONS
    sections = sections_of(engine, ticket_id)
    compliance = sections["compliance"]
    assert compliance.generation_status == "needs_human_review" and compliance.iterations == guidelines.MAX_ITERATIONS
    assert compliance.draft_content == never_passes.strip()  # last draft kept
    assert compliance.evaluation_results["overall_pass"] is False and len(compliance.evaluation_history) == 3
    assert sections["revenue"].generation_status == "passed"  # the other departments are not affected
    with Session(engine) as session:
        ticket = session.get(RfpTicket, ticket_id)
    assert ticket.status == "needs_human_review" and ticket.ready_for_approval  # never discarded
    handoff = ticket.response_handoff
    assert handoff["needs_human_review"] == ["compliance"]
    section = next(s for s in handoff["sections"] if s["department_id"] == "compliance")
    assert section["draft_content"] == never_passes.strip() and section["evaluation_result"]["compliance"]["pass"] is False


def test_the_limit_is_configurable(engine, model, monkeypatch):  # noqa: F811
    monkeypatch.setenv("RFP_MAX_ITERATIONS", "1")
    ticket_id = analysed(engine, FORMAL)
    model.overrides["generator:revenue"] = {"content": draft("revenue", "US").replace("USD", "GBP")}
    assert response_runner.run_generation(engine, ticket_id) == "needs_human_review"
    assert len(generator_calls(model, "revenue")) == 1


def test_phi_in_a_draft_is_redacted_before_it_is_stored(engine, model):  # noqa: F811
    ticket_id = analysed(engine, FORMAL)
    leaked = draft("clinical", "US") + "\nCase study — Patient: Jane Placeholder, DOB: 03/04/1980.\n"
    model.overrides["generator:clinical"] = {"content": leaked}
    assert response_runner.run_generation(engine, ticket_id) == "needs_human_review"
    clinical = sections_of(engine, ticket_id)["clinical"]
    stored = json.dumps([clinical.draft_content, clinical.evaluation_results, clinical.evaluation_history])
    assert "Jane" not in stored and "03/04/1980" not in stored and "REDACTADO" in clinical.draft_content
    assert clinical.evaluation_results["contains_phi"] is True
    assert clinical.evaluation_results["compliance"]["violations"][0]["rule_id"] == "HC-PHI-01"
    with Session(engine) as session:
        signals = session.get(RfpTicket, ticket_id).response_handoff["signals"]
    assert {"trigger": "phi-detected", "department_id": "clinical", "arbiter": "compliance"} in signals
    # the next generator call receives only the redacted draft
    assert all("Jane" not in c[2] for c in generator_calls(model, "clinical"))


def test_a_generator_that_is_down_only_affects_its_section(engine, model):  # noqa: F811
    ticket_id = analysed(engine, FORMAL)
    model.overrides["generator:clinical"] = RuntimeError("gateway down")
    assert response_runner.run_generation(engine, ticket_id) == "needs_human_review"
    sections = sections_of(engine, ticket_id)
    assert sections["clinical"].generation_status == "needs_human_review" and sections["clinical"].draft_content is None
    assert "generator_error" in sections["clinical"].evaluation_results
    assert sections["revenue"].generation_status == "passed" and sections["compliance"].generation_status == "passed"


def test_only_tickets_in_the_part1_queue_are_generated(engine, model):  # noqa: F811
    discarded = analysed(engine, INVALID)
    with_phi = analysed(engine, WITH_PHI)  # routed to compliance_review, ready_for_generation = false
    queued = analysed(engine, FORMAL)
    assert store.generation_queue(engine) == [queued]
    for ticket_id in (discarded, with_phi):
        with pytest.raises(response_graph.NotReadyForGeneration):
            response_runner.run_generation(engine, ticket_id)
    response_runner.run_generation(engine, queued)
    with pytest.raises(response_graph.NotReadyForGeneration):  # claimed once
        response_runner.run_generation(engine, queued)


def test_ticket_progress_is_written_while_the_cycle_runs(engine, model, monkeypatch):  # noqa: F811
    ticket_id = analysed(engine, FORMAL)
    seen = []
    original = store.update_section

    def spy(eng, tid, department_id, **values):
        with Session(eng) as session:
            seen.append((session.get(RfpTicket, tid).status, department_id, values.get("generation_status")))
        return original(eng, tid, department_id, **values)

    monkeypatch.setattr(store, "update_section", spy)
    response_runner.run_generation(engine, ticket_id)
    statuses = [s[0] for s in seen]
    assert statuses[0] == "generando_borrador" and "en_evaluación" in statuses
    assert {(d, g) for _, d, g in seen if g} >= {("revenue", "generating"), ("revenue", "evaluating"), ("revenue", "passed")}


def test_a_crash_is_recorded_on_the_ticket_and_can_be_retried(engine, model, monkeypatch):  # noqa: F811
    ticket_id = analysed(engine, FORMAL)
    original = response_graph.build_contexts
    monkeypatch.setattr(response_graph, "build_contexts", lambda *a: (_ for _ in ()).throw(ValueError("boom")))
    assert response_runner.run_generation(engine, ticket_id) == "failed"
    with Session(engine) as session:
        ticket = session.get(RfpTicket, ticket_id)
    assert ticket.stage == "generation_failed" and ticket.error.startswith("generating: ValueError")
    monkeypatch.setattr(response_graph, "build_contexts", original)  # (undo() would also restore the real model)
    assert response_runner.run_generation(engine, ticket_id, retry=True) == "en_evaluación"


def test_evaluators_run_in_parallel_branches_of_the_section_graph():
    graph = response_graph.build_section_graph().get_graph()
    edges = {(e.source, e.target) for e in graph.edges}
    for evaluator in ("evaluate_readability", "evaluate_relevance", "evaluate_compliance"):
        assert ("generate", evaluator) in edges and (evaluator, "judge") in edges
    assert ("judge", "generate") in edges and ("judge", "finish") in edges
