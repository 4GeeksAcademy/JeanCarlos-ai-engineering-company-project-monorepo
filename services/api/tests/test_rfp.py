"""RFP endpoints on the existing API: upload answers 202 fast, the intake runs in the background
and chains the generation + evaluation (Part 2) and the approval flow (Part 3); POST /generate and
POST /approval take a queued ticket; each department decides through its own endpoint."""

import pytest

from services.api.main import app
from services.api.routes import rfp
from tests.pipelines.rfp_fixtures import FORMAL, INVALID, engine, model  # noqa: F401


@pytest.fixture
def headers(user_factory, auth_headers):
    return auth_headers(user_factory(username="tom"))


@pytest.fixture(autouse=True)
def rfp_engine(engine):  # noqa: F811
    app.dependency_overrides[rfp.get_rfp_engine] = lambda: engine
    yield engine
    app.dependency_overrides.pop(rfp.get_rfp_engine, None)


def upload(client, headers, path):
    return client.post("/api/rfp/tickets", headers=headers,
                       files={"file": (path.name, path.read_bytes(), "application/pdf")})


def test_upload_is_accepted_and_processed_in_the_background(client, headers, model):  # noqa: F811
    response = upload(client, headers, FORMAL)
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "analizando" and body["rfp_id"].startswith("RFP-")
    # TestClient runs background tasks before returning: intake + Part 2 + opening Part 3 are done
    ticket = client.get(f"/api/rfp/tickets/{body['ticket_id']}", headers=headers).json()
    assert ticket["status"] == "esperando_aprobación" and ticket["stage"] == "awaiting_approval"
    assert len(ticket["sections"]) == 3
    assert ticket["synthesis"]["workstreams"] and ticket["response_handoff"]["contract"] == "rfp_response.handoff/v1"
    assert all(s["generation_status"] == "passed" and s["evaluation_results"]["overall_pass"] for s in ticket["sections"])
    assert "document_markdown" not in ticket and "raw_pdf_path" not in ticket
    listed = client.get("/api/rfp/tickets", headers=headers).json()
    assert listed[0]["ticket_id"] == body["ticket_id"] and listed[0]["client_country"] == "US"


def test_a_rejected_document_does_not_stop_other_tickets(client, headers, model):  # noqa: F811
    discarded = upload(client, headers, INVALID).json()
    accepted = upload(client, headers, FORMAL).json()
    statuses = {t["ticket_id"]: t["status"] for t in client.get("/api/rfp/tickets", headers=headers).json()}
    assert statuses == {discarded["ticket_id"]: "descartado", accepted["ticket_id"]: "esperando_aprobación"}


def test_generate_takes_a_ticket_from_the_part1_queue(client, headers, model, monkeypatch):  # noqa: F811
    monkeypatch.setenv("RFP_AUTO_GENERATE", "0")
    monkeypatch.setenv("RFP_AUTO_APPROVAL", "0")
    ticket_id = upload(client, headers, FORMAL).json()["ticket_id"]
    assert client.get(f"/api/rfp/tickets/{ticket_id}", headers=headers).json()["status"] == "analisis_completo"
    response = client.post(f"/api/rfp/tickets/{ticket_id}/generate", headers=headers)
    assert response.status_code == 202 and response.json()["queued"] is True
    ticket = client.get(f"/api/rfp/tickets/{ticket_id}", headers=headers).json()
    assert ticket["status"] == "en_evaluación" and ticket["ready_for_approval"] is True
    # already taken from the queue / a discarded document: 409
    assert client.post(f"/api/rfp/tickets/{ticket_id}/generate", headers=headers).status_code == 409
    discarded = upload(client, headers, INVALID).json()["ticket_id"]
    assert client.post(f"/api/rfp/tickets/{discarded}/generate", headers=headers).status_code == 409
    assert client.post(f"/api/rfp/tickets/{ticket_id}/generate").status_code == 401


def test_approval_flow_through_the_api(client, headers, model, monkeypatch):  # noqa: F811
    monkeypatch.setenv("RFP_AUTO_APPROVAL", "0")
    monkeypatch.setenv("RFP_APPROVERS", '{"revenue": ["tom"], "clinical": ["tom"], "compliance": ["tom"]}')
    ticket_id = upload(client, headers, FORMAL).json()["ticket_id"]
    base = f"/api/rfp/tickets/{ticket_id}"
    assert client.post(f"{base}/sections/revenue/decision", headers=headers,
                       json={"action": "approve", "approver": "Tom Callahan"}).status_code == 409  # not open yet
    assert client.post(f"{base}/approval", headers=headers).status_code == 202
    assert client.post(f"{base}/approval", headers=headers).status_code == 409  # opened once
    state = client.get(f"{base}/approval", headers=headers).json()
    assert {k: v["paused_at"] for k, v in state["threads"].items()} == {
        "ticket": "await_departments", "revenue": "await_approval", "clinical": "await_approval",
        "compliance": "await_approval"}
    assert state["threads"]["clinical"]["thread_id"] == f"rfp-{ticket_id}:clinical"
    assert state["packets"]["clinical"]["requires_capacity"] is True
    invalid = client.post(f"{base}/sections/clinical/decision", headers=headers,
                          json={"action": "approve", "approver": "Tom Callahan", "confirmed_capacity": 900})
    assert invalid.status_code == 422 and "Dr. Marcus Reid" in invalid.json()["detail"]
    assert client.post(f"{base}/sections/legal/decision", headers=headers, json={}).status_code == 422
    assert client.get(f"{base}/document", headers=headers).status_code == 404
    for department_id, approver in (("clinical", "Dr. Marcus Reid"), ("revenue", "Tom Callahan"),
                                    ("compliance", "Claire Whitfield")):
        body = {"action": "approve", "approver": approver}
        if department_id == "clinical":
            body["confirmed_capacity"] = 900
        assert client.post(f"{base}/sections/{department_id}/decision", headers=headers, json=body).status_code == 202
    ticket = client.get(base, headers=headers).json()
    assert (ticket["status"], ticket["stage"]) == ("terminado", "completed")
    document = client.get(f"{base}/document", headers=headers).json()
    assert document["currency"] == "USD" and len(document["sections"]) == 3
    markdown = client.get(f"{base}/document?format=markdown", headers=headers)
    assert markdown.headers["content-type"].startswith("text/markdown") and markdown.text.startswith("# Economic Proposal")
    trace = client.get(f"{base}/trace", headers=headers).json()
    assert trace[0]["node"] == "convert_document" and trace[-1]["node"] == "close"
    assert client.post(f"{base}/arbitration", headers=headers, json={}).status_code == 409
    assert client.get(f"{base}/trace").status_code == 401


def test_non_pdf_and_missing_session_are_rejected(client, headers):
    assert client.post("/api/rfp/tickets", headers=headers,
                       files={"file": ("x.pdf", b"not a pdf", "application/pdf")}).status_code == 400
    assert client.get("/api/rfp/tickets").status_code == 401
    assert client.get("/api/rfp/tickets/00000000-0000-0000-0000-000000000000", headers=headers).status_code == 404


# ---------------------------------------------------------------- OWASP A01: who may sign off a department


def test_only_the_mapped_account_can_sign_off_a_department(client, headers, user_factory, auth_headers, model, monkeypatch):  # noqa: F811
    """The BRIEFING approver of compliance is Claire Whitfield: typing her name is not enough, the
    authenticated account must be one registered for that department (RFP_APPROVERS)."""
    monkeypatch.setenv("RFP_AUTO_APPROVAL", "0")
    monkeypatch.setenv("RFP_APPROVERS", '{"revenue": ["tom"], "clinical": ["marcus.reid"], "compliance": ["claire.whitfield"]}')
    intruder = auth_headers(user_factory(username="intruso"))
    claire = auth_headers(user_factory(username="claire.whitfield"))
    ticket_id = upload(client, headers, FORMAL).json()["ticket_id"]
    base = f"/api/rfp/tickets/{ticket_id}"
    assert client.post(f"{base}/approval", headers=headers).status_code == 202
    body = {"action": "approve", "approver": "Claire Whitfield", "comment": "ok"}
    for who in (intruder, headers):  # an unregistered staff member, and the revenue approver
        refused = client.post(f"{base}/sections/compliance/decision", headers=who, json=body)
        assert refused.status_code == 403 and "Compliance" in refused.json()["detail"]
    state = client.get(f"{base}/approval", headers=headers).json()
    assert state["departments"]["compliance"]["approval_status"] == "pending"  # nothing was signed
    forged = {**body, "submitted_by": "someone-else"}  # the client cannot choose who is recorded
    assert client.post(f"{base}/sections/compliance/decision", headers=claire, json=forged).status_code == 202
    ticket = client.get(base, headers=headers).json()
    compliance = next(s for s in ticket["sections"] if s["department_id"] == "compliance")
    assert compliance["approval_status"] == "approved"
    assert compliance["decision_history"][-1]["submitted_by"] == "claire.whitfield"


def test_without_a_configured_approver_nobody_can_sign_off(client, headers, model, monkeypatch):  # noqa: F811
    monkeypatch.setenv("RFP_AUTO_APPROVAL", "0")
    monkeypatch.delenv("RFP_APPROVERS", raising=False)
    ticket_id = upload(client, headers, FORMAL).json()["ticket_id"]
    base = f"/api/rfp/tickets/{ticket_id}"
    client.post(f"{base}/approval", headers=headers)
    response = client.post(f"{base}/sections/revenue/decision", headers=headers,
                           json={"action": "approve", "approver": "Tom Callahan"})
    assert response.status_code == 403  # fail closed
