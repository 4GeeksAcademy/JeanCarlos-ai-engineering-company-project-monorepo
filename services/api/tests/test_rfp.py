"""RFP endpoints on the existing API: upload answers 202 fast, the intake runs in the background
and (Part 2) chains the generation + evaluation; POST /generate takes a queued ticket."""

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
    # TestClient runs background tasks before returning: intake + (automatic) Part 2 are done
    ticket = client.get(f"/api/rfp/tickets/{body['ticket_id']}", headers=headers).json()
    assert ticket["status"] == "en_evaluación" and ticket["stage"] == "generated" and len(ticket["sections"]) == 3
    assert ticket["synthesis"]["workstreams"] and ticket["response_handoff"]["contract"] == "rfp_response.handoff/v1"
    assert all(s["generation_status"] == "passed" and s["evaluation_results"]["overall_pass"] for s in ticket["sections"])
    assert "document_markdown" not in ticket and "raw_pdf_path" not in ticket
    listed = client.get("/api/rfp/tickets", headers=headers).json()
    assert listed[0]["ticket_id"] == body["ticket_id"] and listed[0]["client_country"] == "US"


def test_a_rejected_document_does_not_stop_other_tickets(client, headers, model):  # noqa: F811
    discarded = upload(client, headers, INVALID).json()
    accepted = upload(client, headers, FORMAL).json()
    statuses = {t["ticket_id"]: t["status"] for t in client.get("/api/rfp/tickets", headers=headers).json()}
    assert statuses == {discarded["ticket_id"]: "descartado", accepted["ticket_id"]: "en_evaluación"}


def test_generate_takes_a_ticket_from_the_part1_queue(client, headers, model, monkeypatch):  # noqa: F811
    monkeypatch.setenv("RFP_AUTO_GENERATE", "0")
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


def test_non_pdf_and_missing_session_are_rejected(client, headers):
    assert client.post("/api/rfp/tickets", headers=headers,
                       files={"file": ("x.pdf", b"not a pdf", "application/pdf")}).status_code == 400
    assert client.get("/api/rfp/tickets").status_code == 401
    assert client.get("/api/rfp/tickets/00000000-0000-0000-0000-000000000000", headers=headers).status_code == 404
