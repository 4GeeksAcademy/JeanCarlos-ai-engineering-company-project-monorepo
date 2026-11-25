"""End-to-end (Hito 9 Partes 1 → 2 → 3) with simulated approvals: the same plans that
scripts/rfp_e2e.py runs against the real model, here with the scripted model and SQLite. Checks
that the ticket goes through the BRIEFING statuses in order, with no jump, and that the data stays
consistent from the upload to the final document.

    python -m pytest tests/pipelines/test_rfp_e2e.py
"""

import json

from data.pipelines.rfp_intake import phi, simulation, store
from tests.pipelines.rfp_fixtures import FORMAL, INFORMAL, engine, model  # noqa: F401

ORDER = ["analizando", "analisis_completo", "generando_borrador", "en_evaluación", "needs_human_review",
         "esperando_aprobación", "terminado"]


def statuses(report):
    seen = []
    for row in report["timeline"]:
        if not seen or seen[-1] != row["status"]:
            seen.append(row["status"])
    return seen


def test_standard_plan_from_upload_to_final_document(engine, model):  # noqa: F811
    report = simulation.run(engine, FORMAL, "standard")
    assert statuses(report) == ["analizando", "analisis_completo", "en_evaluación", "esperando_aprobación", "terminado"]
    assert all(ORDER.index(a) <= ORDER.index(b) for a, b in zip(statuses(report), statuses(report)[1:]))  # never back
    # B approved while A is still interrupted (first step: clinical approves, revenue/compliance paused)
    assert report["steps"][0]["threads"] == {"ticket": "await_departments", "revenue": "await_approval",
                                             "clinical": "await_closure", "compliance": "await_approval"}
    assert report["steps"][1]["threads"]["revenue"] == "await_approval"  # request_changes → revised → asked again
    assert report["steps"][-1]["threads"] == {k: "ended" for k in ("ticket", "revenue", "clinical", "compliance")}
    view = store.ticket_view(engine, report["ticket_id"])
    handoff, document = view["response_handoff"], report["final_document"]
    # the same facts from Part 1 to the final document
    assert view["metadata"]["currency"] == handoff["currency"] == document["currency"] == "USD"
    assert [s["department_id"] for s in handoff["sections"]] == [s["department_id"] for s in document["sections"]]
    assert {s["department_id"]: s["content"] for s in document["sections"]} == \
        {s["department_id"]: s["draft_content"] for s in view["sections"]}  # exactly what was approved
    assert document["sections"][0]["revision_rounds"] == 1
    assert view["ready_for_generation"] is False and view["ready_for_approval"] is False  # both queues consumed
    trace = report["trace"]
    assert [t["thread_id"].rsplit(":", 1)[-1] for t in trace[:1]] == ["intake"]
    assert trace[-1]["node"] == "close" and not phi.find_phi(json.dumps(store.trace(engine, report["ticket_id"])))


def test_capacity_plan_goes_through_the_fixed_arbiter(engine, model):  # noqa: F811
    report = simulation.run(engine, INFORMAL, "capacity")
    assert report["status"] == "terminado" and statuses(report)[-2:] == ["esperando_aprobación", "terminado"]
    assert [(a["trigger"], a["arbiter"], a["resolution"], a["agreed_population"]) for a in report["arbitration"]] == [
        ("capacity-vs-population", "Tom Callahan", "reduce_population", 2000)]
    document = report["final_document"]
    assert document["currency"] == "GBP" and "UK GDPR — Data Processing Agreement (DPA)" in document["markdown"]
    assert "2000 students" in document["markdown"]
    arbiter = [t for t in report["trace"] if t["node"] == "arbiter_decision"]
    assert [t["status"] for t in arbiter] == ["interrupted", "ok"] and arbiter[-1]["agent"] == "human:Tom Callahan"
