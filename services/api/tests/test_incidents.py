import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

from packages.shared.incidents import BRANCHES, CATEGORIES, ORIGINS, STATUSES
from services.api.database import get_db
from services.api.main import app
from services.api.models.incident import Incident
from services.api.routers import incidents as incidents_router

VALID = {
    "title": "EHR login failing for front desk",
    "description": "Front desk staff cannot sign in to the EHR since 9am.",
    "category": "it_system",
    "origin": "branch",
    "branch": "tampa_bay",
}


@pytest.fixture
def engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    def get_test_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = get_test_db
    incidents_router._ready_engines.clear()
    yield engine
    app.dependency_overrides.pop(get_db, None)
    incidents_router._ready_engines.clear()


@pytest.fixture
def headers(engine, user_factory, auth_headers):
    return auth_headers(user_factory())


def create(client, headers, **changes):
    return client.post("/api/incidents", headers=headers, json={**VALID, **changes})


def field_errors(response):
    assert response.status_code == 400, response.text
    body = response.json()
    assert "Traceback" not in response.text
    return {item["field"]: item["message"] for item in body["errors"]}


# --- Authentication ---------------------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("GET", "/api/incidents"), ("POST", "/api/incidents"), ("GET", "/api/incidents/summary"),
    ("GET", "/api/incidents/1"), ("PATCH", "/api/incidents/1/status"),
])
def test_incident_routes_require_a_token(client, engine, method, path):
    assert client.request(method, path, json={}).status_code == 401


# --- POST -------------------------------------------------------------------

def test_create_incident_sets_defaults_and_timestamps(client, headers, engine):
    response = create(client, headers)
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "open"
    assert body["id"] > 0
    assert body["created_at"] == body["updated_at"]
    assert "legacy_ref" not in body and "user_uuid" not in body
    with Session(engine) as session:
        stored = session.exec(select(Incident)).one()
        assert stored.user_uuid is not None


def test_create_trims_text_and_accepts_initial_status(client, headers):
    response = create(client, headers, title="  Leaking roof  ", status="in_progress", origin="internal", branch="central")
    assert response.status_code == 201
    assert response.json()["title"] == "Leaking roof"
    assert response.json()["status"] == "in_progress"


def test_missing_fields_return_400_naming_each_field(client, headers):
    errors = field_errors(client.post("/api/incidents", headers=headers, json={}))
    assert set(errors) == {"title", "description", "category", "origin", "branch"}
    assert errors["title"] == "Title is required."
    assert errors["branch"] == "Branch is required."


@pytest.mark.parametrize("field,value,expected", [
    ("category", "hardware", "Category must be one of"),
    ("origin", "patient", "Origin must be one of"),
    ("branch", "dallas", "Branch must be one of"),
    ("status", "closed", "Status must be one of"),
    ("title", "x" * 121, "at most 120"),
    ("title", "   ", "Title is required."),
    ("description", "abc", "at least 5"),
    ("description", 42, "Description is required."),
])
def test_invalid_values_return_400_for_that_field(client, headers, field, value, expected):
    errors = field_errors(create(client, headers, **{field: value}))
    assert list(errors) == [field]
    assert expected in errors[field]


def test_read_only_or_unknown_fields_are_rejected(client, headers):
    errors = field_errors(create(client, headers, id=7, created_at="2024-01-01", patient_name="x"))
    assert set(errors) == {"id", "created_at", "patient_name"}


@pytest.mark.parametrize("body", [b"{", b"[]", b'"text"'])
def test_malformed_bodies_return_400(client, headers, body):
    response = client.post(
        "/api/incidents", headers={**headers, "content-type": "application/json"}, content=body
    )
    assert response.status_code == 400
    assert response.json()["errors"]


def test_every_briefing_branch_category_and_origin_is_accepted(client, headers):
    for branch in BRANCHES:
        assert create(client, headers, branch=branch).status_code == 201
    for category in CATEGORIES:
        assert create(client, headers, category=category).status_code == 201
    for origin in ORIGINS:
        assert create(client, headers, origin=origin).status_code == 201


# --- GET list / detail --------------------------------------------------------

def test_reads_work_on_an_empty_database(client, headers):
    assert client.get("/api/incidents", headers=headers).json() == []
    summary = client.get("/api/incidents/summary", headers=headers)
    assert summary.status_code == 200
    body = summary.json()
    assert body["total"] == 0
    assert body["by_status"] == dict.fromkeys(STATUSES, 0)
    assert body["by_category"] == dict.fromkeys(CATEGORIES, 0)
    assert body["by_origin"] == dict.fromkeys(ORIGINS, 0)
    assert body["by_branch"] == dict.fromkeys(BRANCHES, 0)


def test_list_filters_by_status_origin_branch_and_category(client, headers):
    first = create(client, headers).json()
    create(client, headers, origin="customer", branch="central", category="billing_error")
    create(client, headers, origin="internal", branch="central", category="compliance_breach")
    client.patch(f"/api/incidents/{first['id']}/status", headers=headers, json={"status": "in_progress"})

    def ids(**params):
        response = client.get("/api/incidents", headers=headers, params=params)
        assert response.status_code == 200
        return len(response.json())

    assert ids() == 3
    assert ids(status="in_progress") == 1
    assert ids(origin="customer") == 1
    assert ids(branch="central") == 2
    assert ids(category="compliance_breach") == 1
    assert ids(branch="central", origin="internal") == 1
    assert ids(branch="tampa_bay", status="open") == 0


def test_invalid_filters_return_400(client, headers):
    errors = field_errors(client.get("/api/incidents", headers=headers, params={"status": "closed", "branch": "x"}))
    assert set(errors) == {"status", "branch"}


def test_detail_returns_incident_or_404(client, headers):
    created = create(client, headers).json()
    assert client.get(f"/api/incidents/{created['id']}", headers=headers).json() == created
    response = client.get("/api/incidents/999", headers=headers)
    assert response.status_code == 404
    assert response.json() == {"detail": "Incident not found"}
    assert "incident_id" in field_errors(client.get("/api/incidents/abc", headers=headers))


# --- PATCH status ---------------------------------------------------------------

@pytest.mark.parametrize("path", [
    ["in_progress", "resolved"], ["in_progress", "discarded"], ["discarded"],
])
def test_valid_lifecycle_transitions(client, headers, path):
    incident = create(client, headers).json()
    for new_status in path:
        response = client.patch(f"/api/incidents/{incident['id']}/status", headers=headers, json={"status": new_status})
        assert response.status_code == 200
        assert response.json()["status"] == new_status
    assert response.json()["updated_at"] >= incident["updated_at"]


@pytest.mark.parametrize("path,invalid", [
    ([], "resolved"), ([], "open"),
    (["in_progress"], "open"), (["in_progress"], "in_progress"),
    (["in_progress", "resolved"], "discarded"), (["in_progress", "resolved"], "open"),
    (["discarded"], "in_progress"), ([], "closed"), ([], None),
])
def test_invalid_transitions_return_400(client, headers, path, invalid):
    incident = create(client, headers).json()
    for new_status in path:
        client.patch(f"/api/incidents/{incident['id']}/status", headers=headers, json={"status": new_status})
    response = client.patch(f"/api/incidents/{incident['id']}/status", headers=headers, json={"status": invalid})
    assert "status" in field_errors(response)


def test_patch_updates_only_status_and_handles_missing_incidents(client, headers):
    incident = create(client, headers).json()
    errors = field_errors(client.patch(
        f"/api/incidents/{incident['id']}/status", headers=headers,
        json={"status": "in_progress", "title": "changed"},
    ))
    assert "status" in errors
    assert client.get(f"/api/incidents/{incident['id']}", headers=headers).json()["title"] == incident["title"]
    assert client.patch("/api/incidents/999/status", headers=headers, json={"status": "in_progress"}).status_code == 404


# --- Summary and errors -----------------------------------------------------------

def test_summary_counts_by_status_category_origin_and_branch(client, headers):
    create(client, headers)
    create(client, headers, origin="customer", branch="central", category="billing_error")
    body = client.get("/api/incidents/summary", headers=headers).json()
    assert body["total"] == 2
    assert body["by_status"]["open"] == 2
    assert body["by_category"]["it_system"] == 1 and body["by_category"]["billing_error"] == 1
    assert body["by_origin"]["branch"] == 1 and body["by_origin"]["customer"] == 1
    assert body["by_branch"]["tampa_bay"] == 1 and body["by_branch"]["central"] == 1


def test_unexpected_errors_return_a_generic_500(client, headers, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(incidents_router, "validate_incident", explode)
    from fastapi.testclient import TestClient

    with TestClient(app, raise_server_exceptions=False) as raw_client:
        response = raw_client.post("/api/incidents", headers=headers, json=VALID)
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert "secret" not in response.text and "Traceback" not in response.text


def test_database_constraints_reject_values_outside_the_catalog(engine):
    Incident.__table__.create(engine, checkfirst=True)
    from sqlalchemy.exc import IntegrityError

    for bad in ({"status": "closed"}, {"origin": "x"}, {"category": "x"}, {"branch": "x"}, {"title": "  "}):
        with Session(engine) as session:
            session.add(Incident(**{**VALID, "status": "open", **bad}))
            with pytest.raises(IntegrityError):
                session.commit()
