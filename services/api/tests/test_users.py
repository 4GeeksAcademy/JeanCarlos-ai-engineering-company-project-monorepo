import pytest
from tinydb import Query

from services.api.core.security import verify_password
from services.api.tests.conftest import PASSWORD


def test_list_users_returns_managed_users(client, admin, user_factory, auth_headers):
    user = user_factory()
    response = client.get("/users", headers=auth_headers(admin))
    assert response.status_code == 200
    assert {item["id"] for item in response.json()} == {admin["id"], user["id"]}


def test_list_users_with_only_the_initial_admin(client, admin, auth_headers):
    response = client.get("/users", headers=auth_headers(admin))
    assert [item["id"] for item in response.json()] == [admin["id"]]


@pytest.mark.parametrize("role", ["user", "manager"])
def test_list_users_rejects_non_admins(client, user_factory, auth_headers, role):
    user = user_factory(role=role)
    assert client.get("/users", headers=auth_headers(user)).status_code == 403


def test_create_user_persists_a_hash_and_role(client, database, admin, auth_headers):
    response = client.post("/users", headers=auth_headers(admin), json={
        "username": "newuser", "password": PASSWORD, "role": "manager", "email": "new@example.org",
    })
    assert response.status_code == 201
    record = database.table("users").get(Query().id == response.json()["id"])
    assert record["role"] == "manager"
    assert record["hashed_password"] != PASSWORD
    assert verify_password(PASSWORD, record["hashed_password"])


def test_create_user_accepts_the_bcrypt_limit_and_no_email(client, database, admin, auth_headers):
    password = "é" * 36
    response = client.post("/users", headers=auth_headers(admin), json={
        "username": "boundary", "password": password, "role": "user",
    })
    assert response.status_code == 201
    record = database.table("users").get(Query().id == response.json()["id"])
    assert record["email"] is None
    assert verify_password(password, record["hashed_password"])


def test_create_user_rejects_duplicates_and_non_admins(client, admin, user_factory, auth_headers):
    user = user_factory()
    payload = {"username": "ADMIN", "password": PASSWORD, "role": "user"}
    assert client.post("/users", headers=auth_headers(admin), json=payload).status_code == 409
    payload.update(username="unique", email="ADMIN@example.org")
    assert client.post("/users", headers=auth_headers(admin), json=payload).status_code == 409
    assert client.post("/users", headers=auth_headers(user), json=payload).status_code == 403


def test_get_user_returns_the_requested_user(client, admin, user_factory, auth_headers):
    user = user_factory()
    response = client.get(f"/users/{user['id']}", headers=auth_headers(admin))
    assert response.status_code == 200
    assert response.json()["id"] == user["id"]


def test_get_user_reports_missing_user(client, admin, auth_headers):
    assert client.get("/users/missing", headers=auth_headers(admin)).status_code == 404


def test_get_user_rejects_non_admin(client, admin, user_factory, auth_headers):
    user = user_factory()
    assert client.get(f"/users/{admin['id']}", headers=auth_headers(user)).status_code == 403


def test_update_user_changes_password_and_revokes_existing_token(client, database, admin, user_factory, auth_headers):
    user = user_factory()
    old_headers = auth_headers(user)
    response = client.patch(f"/users/{user['id']}", headers=auth_headers(admin), json={
        "username": "updated", "password": "ReplacementPassword123!",
    })
    assert response.status_code == 200
    record = database.table("users").get(Query().id == user["id"])
    assert record["username"] == "updated"
    assert verify_password("ReplacementPassword123!", record["hashed_password"])
    assert client.get("/me", headers=old_headers).status_code == 401
    assert client.post("/login", data={"username": "updated", "password": "ReplacementPassword123!"}).status_code == 200


def test_update_user_preserves_token_for_unchanged_email_and_protects_last_admin(client, admin, auth_headers):
    headers = auth_headers(admin)
    response = client.patch(f"/users/{admin['id']}", headers=headers, json={"email": "admin@example.org"})
    assert response.status_code == 200
    assert client.get("/me", headers=headers).status_code == 200
    assert client.patch(f"/users/{admin['id']}", headers=headers, json={"role": "user"}).status_code == 409


def test_update_user_rejects_unauthorized_and_duplicate_changes(client, admin, user_factory, auth_headers):
    user = user_factory()
    assert client.patch(f"/users/{admin['id']}", headers=auth_headers(user), json={"role": "admin"}).status_code == 403
    assert client.patch(f"/users/{user['id']}", headers=auth_headers(admin), json={"username": "ADMIN"}).status_code == 409
    assert client.patch("/users/missing", headers=auth_headers(admin), json={"role": "user"}).status_code == 404


def test_delete_user_removes_profile_and_revokes_access(client, database, admin, user_factory, auth_headers):
    user = user_factory()
    user_headers = auth_headers(user)
    database.table("profiles").insert({"id": "profile-id", "user_id": user["id"]})
    response = client.delete(f"/users/{user['id']}", headers=auth_headers(admin))
    assert response.status_code == 204
    assert database.table("users").get(Query().id == user["id"]) is None
    assert database.table("profiles").get(Query().user_id == user["id"]) is None
    assert client.get("/me", headers=user_headers).status_code == 401


def test_delete_user_cannot_remove_the_last_admin(client, admin, auth_headers):
    assert client.delete(f"/users/{admin['id']}", headers=auth_headers(admin)).status_code == 409


def test_delete_user_rejects_missing_and_unauthorized_requests(client, admin, user_factory, auth_headers):
    user = user_factory()
    assert client.delete("/users/missing", headers=auth_headers(admin)).status_code == 404
    assert client.delete(f"/users/{admin['id']}", headers=auth_headers(user)).status_code == 403
