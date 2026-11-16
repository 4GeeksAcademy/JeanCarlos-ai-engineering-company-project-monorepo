import pytest
from tinydb import Query

from services.api.core.security import verify_password
from services.api.tests.conftest import PASSWORD


# --- POST /users (public registration) ------------------------------------

def test_register_is_public_hashes_password_and_defaults_to_user_role(client, database):
    response = client.post("/users", json={"email": "new@example.org", "password": PASSWORD})

    assert response.status_code == 201
    body = response.json()
    assert body["role"] == "user"
    assert body["is_active"] is True
    assert "created_at" in body
    assert "hashed_password" not in body and "password" not in body
    record = database.table("users").get(Query().id == body["id"])
    assert record["hashed_password"] != PASSWORD
    assert record["hashed_password"].startswith("$2")
    assert verify_password(PASSWORD, record["hashed_password"])
    assert PASSWORD not in str(database.table("users").all())


def test_register_creates_the_linked_profile_and_keeps_contact_data_out_of_user(client, database):
    response = client.post("/users", json={
        "email": "profiled@example.org", "password": PASSWORD,
        "name": "Pat", "phone": "+34 600 000 000", "address": "Calle 1",
    })
    user_id = response.json()["id"]
    record = database.table("users").get(Query().id == user_id)
    assert not {"name", "phone", "address"} & set(record)
    profiles = database.table("profiles").search(Query().user_id == user_id)
    assert len(profiles) == 1
    assert profiles[0]["name"] == "Pat"
    assert profiles[0]["address"] == "Calle 1"


def test_register_without_profile_fields_still_creates_an_empty_profile(client, database):
    user_id = client.post("/users", json={"email": "bare@example.org", "password": PASSWORD}).json()["id"]
    profile = database.table("profiles").get(Query().user_id == user_id)
    assert profile is not None and profile["name"] is None


@pytest.mark.parametrize("role", ["superuser", "root", ""])
def test_register_rejects_unknown_roles(client, role):
    response = client.post("/users", json={"email": "r@example.org", "password": PASSWORD, "role": role})
    assert response.status_code == 422


@pytest.mark.parametrize("role", ["admin", "manager"])
def test_only_admins_can_register_elevated_roles(client, admin, user_factory, auth_headers, role):
    payload = {"email": f"{role}2@example.org", "password": PASSWORD, "role": role}
    assert client.post("/users", json=payload).status_code == 403
    assert client.post("/users", json=payload, headers=auth_headers(user_factory())).status_code == 403
    response = client.post("/users", json=payload, headers=auth_headers(admin))
    assert response.status_code == 201
    assert response.json()["role"] == role


def test_register_rejects_duplicate_emails_and_invalid_payloads(client, admin):
    assert client.post("/users", json={"email": "ADMIN@example.org", "password": PASSWORD}).status_code == 409
    assert client.post("/users", json={"email": "x@example.org", "password": "short"}).status_code == 422
    assert client.post("/users", json={"email": "x@example.org", "password": "é" * 37}).status_code == 422
    assert client.post("/users", json={"email": "x@example.org", "password": PASSWORD, "username": "x"}).status_code == 422


# --- GET /users, GET /users/{id} ------------------------------------------

def test_list_users_requires_a_token_and_a_staff_role(client, admin, user_factory, auth_headers):
    user = user_factory()
    manager = user_factory(email="manager@example.org", role="manager")
    assert client.get("/users").status_code == 401
    assert client.get("/users", headers=auth_headers(user)).status_code == 403
    for caller in (admin, manager):
        response = client.get("/users", headers=auth_headers(caller))
        assert response.status_code == 200
        assert {item["id"] for item in response.json()} == {admin["id"], user["id"], manager["id"]}


def test_get_user_allows_self_and_staff_but_forbids_other_users(client, admin, user_factory, auth_headers):
    user = user_factory()
    other = user_factory(email="other@example.org")
    assert client.get(f"/users/{user['id']}").status_code == 401
    assert client.get(f"/users/{user['id']}", headers=auth_headers(user)).status_code == 200
    assert client.get(f"/users/{user['id']}", headers=auth_headers(admin)).status_code == 200
    assert client.get(f"/users/{user['id']}", headers=auth_headers(other)).status_code == 403
    assert client.get("/users/missing", headers=auth_headers(admin)).status_code == 404


# --- PUT /users/{id} ------------------------------------------------------

def test_user_can_update_own_credentials_and_old_token_is_revoked(client, database, user_factory, auth_headers):
    user = user_factory()
    old_headers = auth_headers(user)
    response = client.put(f"/users/{user['id']}", headers=old_headers, json={
        "email": "renamed@example.org", "password": "ReplacementPassword123!",
    })
    assert response.status_code == 200
    assert response.json()["email"] == "renamed@example.org"
    record = database.table("users").get(Query().id == user["id"])
    assert verify_password("ReplacementPassword123!", record["hashed_password"])
    assert client.get("/auth/me", headers=old_headers).status_code == 401
    assert client.post("/auth/login", json={
        "email": "renamed@example.org", "password": "ReplacementPassword123!",
    }).status_code == 200


def test_user_cannot_update_another_users_credentials(client, admin, user_factory, auth_headers):
    user = user_factory()
    other = user_factory(email="other@example.org")
    for target in (other, admin):
        response = client.put(f"/users/{target['id']}", headers=auth_headers(user), json={"email": "x@example.org"})
        assert response.status_code == 403
    assert client.put(f"/users/{user['id']}", json={"email": "x@example.org"}).status_code == 401


def test_only_admins_change_role_or_status(client, database, admin, user_factory, auth_headers):
    user = user_factory()
    assert client.put(f"/users/{user['id']}", headers=auth_headers(user), json={"role": "admin"}).status_code == 403
    assert client.put(f"/users/{user['id']}", headers=auth_headers(user), json={"is_active": False}).status_code == 403
    assert client.put(f"/users/{user['id']}", headers=auth_headers(admin), json={"role": "superuser"}).status_code == 422

    response = client.put(f"/users/{user['id']}", headers=auth_headers(admin), json={"role": "manager"})
    assert response.status_code == 200 and response.json()["role"] == "manager"
    assert client.put(f"/users/{user['id']}", headers=auth_headers(admin), json={"is_active": False}).status_code == 200
    assert database.table("users").get(Query().id == user["id"])["is_active"] is False


def test_update_keeps_token_for_unchanged_email_and_protects_last_admin(client, admin, user_factory, auth_headers):
    headers = auth_headers(admin)
    assert client.put(f"/users/{admin['id']}", headers=headers, json={"email": "admin@example.org"}).status_code == 200
    assert client.get("/auth/me", headers=headers).status_code == 200
    assert client.put(f"/users/{admin['id']}", headers=headers, json={"role": "user"}).status_code == 409
    assert client.put(f"/users/{admin['id']}", headers=headers, json={"is_active": False}).status_code == 409
    other = user_factory()
    assert client.put(f"/users/{other['id']}", headers=headers, json={"email": "ADMIN@example.org"}).status_code == 409
    assert client.put("/users/missing", headers=headers, json={"role": "user"}).status_code == 404


# --- DELETE /users/{id} ---------------------------------------------------

def test_delete_user_removes_profile_and_revokes_access(client, database, admin, user_factory, auth_headers):
    user = user_factory()
    user_headers = auth_headers(user)
    response = client.delete(f"/users/{user['id']}", headers=auth_headers(admin))
    assert response.status_code == 204
    assert database.table("users").get(Query().id == user["id"]) is None
    assert database.table("profiles").get(Query().user_id == user["id"]) is None
    assert client.get("/auth/me", headers=user_headers).status_code == 401


def test_user_can_delete_own_account_but_not_others(client, database, admin, user_factory, auth_headers):
    user = user_factory()
    other = user_factory(email="other@example.org")
    assert client.delete(f"/users/{other['id']}").status_code == 401
    assert client.delete(f"/users/{other['id']}", headers=auth_headers(user)).status_code == 403
    assert client.delete(f"/users/{admin['id']}", headers=auth_headers(user)).status_code == 403
    assert client.delete(f"/users/{user['id']}", headers=auth_headers(user)).status_code == 204
    assert database.table("users").get(Query().id == other["id"]) is not None


def test_delete_user_cannot_remove_the_last_admin(client, admin, auth_headers):
    assert client.delete(f"/users/{admin['id']}", headers=auth_headers(admin)).status_code == 409
    assert client.delete("/users/missing", headers=auth_headers(admin)).status_code == 404
