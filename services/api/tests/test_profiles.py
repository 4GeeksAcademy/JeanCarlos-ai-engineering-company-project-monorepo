from tinydb import Query


def profile_of(database, user):
    return database.table("profiles").get(Query().user_id == user["id"])


def test_profile_me_requires_a_token(client):
    assert client.get("/profiles/me").status_code == 401
    assert client.put("/profiles/me", json={"name": "x"}).status_code == 401


def test_get_profile_me_returns_the_authenticated_users_profile(client, user_factory, auth_headers):
    user = user_factory(name="Alice", phone="555", address="1 Main St")
    user_factory(email="other@example.org", name="Other")
    response = client.get("/profiles/me", headers=auth_headers(user))
    assert response.status_code == 200
    assert response.json()["user_id"] == user["id"]
    assert response.json()["name"] == "Alice"


def test_put_profile_me_updates_contact_fields_only(client, database, user_factory, auth_headers):
    user = user_factory(name="Alice", phone="555")
    response = client.put("/profiles/me", headers=auth_headers(user), json={
        "name": "Alice B", "address": "2 Side St",
    })
    assert response.status_code == 200
    stored = profile_of(database, user)
    assert stored["name"] == "Alice B"
    assert stored["address"] == "2 Side St"
    assert stored["phone"] == "555"
    assert client.put("/profiles/me", headers=auth_headers(user), json={"user_id": "x"}).status_code == 422
    assert client.put("/profiles/me", headers=auth_headers(user), json={"email": "x@example.org"}).status_code == 422


def test_put_profile_me_creates_a_profile_for_legacy_users(client, database, user_factory, auth_headers):
    user = user_factory()
    database.table("profiles").remove(Query().user_id == user["id"])
    assert client.get("/profiles/me", headers=auth_headers(user)).status_code == 404
    assert client.put("/profiles/me", headers=auth_headers(user), json={"name": "New"}).status_code == 200
    assert profile_of(database, user)["name"] == "New"


def test_users_cannot_read_or_modify_another_users_profile(client, database, admin, user_factory, auth_headers):
    user = user_factory(name="Alice")
    other = user_factory(email="other@example.org", name="Other")
    other_profile = profile_of(database, other)

    headers = auth_headers(user)
    assert client.get(f"/profiles/{other_profile['id']}", headers=headers).status_code == 403
    assert client.put(f"/profiles/{other_profile['id']}", headers=headers, json={"name": "Hacked"}).status_code == 403
    assert client.put(f"/profiles/{other_profile['id']}", headers=auth_headers(admin), json={"name": "Hacked"}).status_code == 403
    assert profile_of(database, other)["name"] == "Other"
    assert client.get("/profiles", headers=headers).status_code == 403


def test_owner_and_staff_access_by_profile_id(client, database, admin, user_factory, auth_headers):
    user = user_factory(name="Alice")
    profile_id = profile_of(database, user)["id"]
    assert client.get(f"/profiles/{profile_id}", headers=auth_headers(user)).status_code == 200
    assert client.get(f"/profiles/{profile_id}", headers=auth_headers(admin)).status_code == 200
    assert client.put(f"/profiles/{profile_id}", headers=auth_headers(user), json={"phone": "1"}).status_code == 200
    assert client.get("/profiles/missing", headers=auth_headers(user)).status_code == 404
    assert len(client.get("/profiles", headers=auth_headers(admin)).json()) == 2
