"""HealthCore MCP server end to end: OAuth with MCP Auth, discovery, ticket and inventory tools,
least privilege, error codes and audit logs. Real server (uvicorn) + test OAuth server + a
stand-in HealthCore API (tests/mcp_stack.py); nothing live is needed.

    python -m pytest tests/mcps/test_healthcore_mcp.py
"""

import asyncio
import json

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.auth import BearerAuth
from cryptography.hazmat.primitives.asymmetric import rsa

from tests.mcp_stack import AUDIENCE, running_stack

MCP_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
LIST_TOOLS = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}


@pytest.fixture
def stack(monkeypatch, tmp_path):
    with running_stack(monkeypatch, tmp_path) as running:
        yield running


def run(coro):
    return asyncio.run(coro)


async def call(stack, tool, args, client_id="ops"):
    async with Client(stack.mcp_url, auth=BearerAuth(stack.oauth.token(client_id))) as client:
        result = await client.call_tool(tool, args, raise_on_error=False)
    if result.is_error:
        return "error", json.loads(result.content[0].text)
    return "ok", result.structured_content


# ---------------------------------------------------------------- authentication (MCP Auth)


def test_protected_resource_metadata_points_to_the_authorization_server(stack):
    url = stack.mcp_url.replace("/mcp", "/.well-known/oauth-protected-resource/mcp")
    metadata = httpx.get(url).json()
    assert metadata["resource"] == stack.mcp_url
    assert metadata["authorization_servers"] == [stack.oauth.url]
    assert metadata["scopes_supported"] == ["incidents:read", "incidents:write", "inventory:read"]


@pytest.mark.parametrize("make_token, expected_error", [
    (lambda o: None, "missing_auth_header"),
    (lambda o: "not-a-jwt", "invalid_token"),
    (lambda o: o.token(key=rsa.generate_private_key(public_exponent=65537, key_size=2048)), "invalid_token"),  # forged
    (lambda o: o.token(expires_in=-600), "invalid_token"),  # expired
    (lambda o: o.token(audience="another-api"), "invalid_audience"),
])
def test_without_a_valid_token_nothing_can_be_listed_or_called(stack, make_token, expected_error):
    token = make_token(stack.oauth)
    headers = {**MCP_HEADERS, **({"Authorization": f"Bearer {token}"} if token else {})}
    for body in (LIST_TOOLS, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                              "params": {"name": "get_incident", "arguments": {"incident_id": 2}}}):
        response = httpx.post(stack.mcp_url, headers=headers, json=body)
        assert response.status_code == 401
        assert response.json()["error"] == expected_error
        assert 'resource_metadata="' in response.headers["www-authenticate"]
    assert stack.api.requests == []  # the HealthCore API was never reached


def test_token_from_another_issuer_is_rejected(stack):
    other = stack.oauth.token(issuer="https://evil.example/realms/healthcore")
    response = httpx.post(stack.mcp_url, headers={**MCP_HEADERS, "Authorization": f"Bearer {other}"}, json=LIST_TOOLS)
    assert response.status_code == 401


# ---------------------------------------------------------------- discovery


def test_discovery_describes_every_tool_its_scope_and_schemas(stack):
    async def discover():
        async with Client(stack.mcp_url, auth=BearerAuth(stack.oauth.token("ops"))) as client:
            return client.initialize_result, await client.list_tools(), await client.list_resources()

    init, tools, resources = run(discover())
    by_name = {tool.name: tool for tool in tools}
    assert set(by_name) == {"get_incident", "list_incidents", "create_incident", "update_incident_status", "query_inventory"}
    scopes = {"get_incident": "incidents:read", "list_incidents": "incidents:read", "create_incident": "incidents:write",
              "update_incident_status": "incidents:write", "query_inventory": "inventory:read"}
    for name, tool in by_name.items():
        assert scopes[name] in tool.description and tool.inputSchema["properties"] and tool.outputSchema
    assert by_name["query_inventory"].annotations.readOnlyHint is True
    assert "INVENTORY_READ_ONLY" in by_name["query_inventory"].description
    assert by_name["create_incident"].annotations.readOnlyHint is False
    assert "PATCH /api/incidents/{id}/status" in by_name["update_incident_status"].description
    assert set(by_name["create_incident"].inputSchema["properties"]["category"]["enum"]) >= {"billing_error", "other"}
    assert "INSUFFICIENT_SCOPE" in init.instructions and "READ ONLY" in init.instructions
    assert [str(r.uri) for r in resources] == ["healthcore://incidents/catalog"]


# ---------------------------------------------------------------- tickets


def test_ticket_lifecycle_create_change_status_and_read(stack):
    status, created = run(call(stack, "create_incident", {
        "title": "Pulse oximeter display flickers", "description": "Display flickers during triage.",
        "category": "clinical_equipment", "origin": "branch", "branch": "london_west"}))
    assert status == "ok" and created["status"] == "open"
    ticket = created["id"]
    assert run(call(stack, "update_incident_status", {"incident_id": ticket, "status": "in_progress"}))[1]["status"] == "in_progress"
    assert run(call(stack, "get_incident", {"incident_id": ticket}))[1]["status"] == "in_progress"
    listed = run(call(stack, "list_incidents", {"status": "open", "branch": "london_west", "limit": 1}))[1]
    assert listed["total"] == 2 and listed["returned"] == 1
    # status changes go through the lifecycle endpoint, never a generic PATCH on the incident
    assert ("PATCH", f"/api/incidents/{ticket}/status") in stack.api.requests
    assert not [r for r in stack.api.requests if r[0] == "PATCH" and not r[1].endswith("/status")]


def test_ticket_error_codes(stack):
    assert run(call(stack, "get_incident", {"incident_id": 99999}))[1]["code"] == "NOT_FOUND"
    run(call(stack, "update_incident_status", {"incident_id": 2, "status": "in_progress"}))
    error = run(call(stack, "update_incident_status", {"incident_id": 2, "status": "open"}))[1]
    assert error["code"] == "INVALID_STATUS_TRANSITION" and error["details"]["errors"][0]["field"] == "status"
    invalid = run(call(stack, "create_incident", {"title": "x", "description": "valid text", "category": "nope",
                                                  "origin": "branch", "branch": "central"}))[1]
    assert invalid["code"] == "VALIDATION_ERROR" and invalid["details"]["errors"][0]["field"] == "category"
    assert run(call(stack, "delete_incident", {"incident_id": 2}))[1]["code"] == "UNKNOWN_TOOL"


# ---------------------------------------------------------------- inventory (read only)


def test_inventory_reads(stack):
    status, listed = run(call(stack, "query_inventory", {"operation": "list"}, client_id="inventory-reader"))
    assert status == "ok" and [p["sku"] for p in listed["products"]] == ["HCR-PPE-001", "HCR-MED-002"]
    found = run(call(stack, "query_inventory", {"operation": "get", "product": "vacunas antigripales"}))[1]
    assert [p["current_stock"] for p in found["products"]] == [25]


@pytest.mark.parametrize("operation", ["update", "adjust", "delete", "create", "set_stock", "consume"])
def test_inventory_write_attempts_are_rejected_explicitly(stack, operation):
    status, error = run(call(stack, "query_inventory", {"operation": operation, "sku": "HCR-PPE-001"}))
    assert status == "error" and error["code"] == "INVENTORY_READ_ONLY"
    assert error["details"]["allowed_operations"] == ["list", "get"]
    assert stack.api.requests == []  # rejected before touching the inventory


def test_there_is_no_inventory_write_scope_and_no_write_tool(stack):
    assert "inventory:write" not in httpx.get(stack.mcp_url.replace("/mcp", "/.well-known/oauth-protected-resource/mcp")).json()["scopes_supported"]
    assert run(call(stack, "update_inventory", {"sku": "HCR-PPE-001"}))[1]["code"] == "UNKNOWN_TOOL"


# ---------------------------------------------------------------- least privilege


@pytest.mark.parametrize("client_id, tool, args, scope", [
    ("healthcore-agent", "create_incident", {"title": "t", "description": "agent text", "category": "other",
                                             "origin": "internal", "branch": "central"}, "incidents:write"),
    ("healthcore-agent", "update_incident_status", {"incident_id": 2, "status": "discarded"}, "incidents:write"),
    ("inventory-reader", "get_incident", {"incident_id": 2}, "incidents:read"),
    ("inventory-reader", "list_incidents", {"status": "open"}, "incidents:read"),
])
def test_each_tool_requires_its_own_scope(stack, client_id, tool, args, scope):
    status, error = run(call(stack, tool, args, client_id=client_id))
    assert status == "error" and error["code"] == "INSUFFICIENT_SCOPE" and error["details"]["required_scope"] == scope
    assert stack.api.requests == []


def test_error_codes_are_distinct_for_authentication_authorization_and_validation(stack):
    unauthenticated = httpx.post(stack.mcp_url, headers=MCP_HEADERS, json=LIST_TOOLS)
    forbidden = run(call(stack, "create_incident", {"title": "t", "description": "agent text", "category": "other",
                                                    "origin": "internal", "branch": "central"}, client_id="healthcore-agent"))[1]
    invalid = run(call(stack, "get_incident", {"incident_id": -1}))[1]
    assert unauthenticated.status_code == 401
    assert {forbidden["code"], invalid["code"]} == {"INSUFFICIENT_SCOPE", "VALIDATION_ERROR"}


# ---------------------------------------------------------------- upstream failures


def test_upstream_timeout_and_outage(stack, monkeypatch):
    monkeypatch.setenv("MCP_API_TIMEOUT_SECONDS", "0.3")
    stack.api.delay = 1.0
    assert run(call(stack, "get_incident", {"incident_id": 2}))[1]["code"] == "UPSTREAM_TIMEOUT"
    stack.api.delay = 0
    monkeypatch.setenv("HEALTHCORE_API_URL", "http://127.0.0.1:9")
    assert run(call(stack, "get_incident", {"incident_id": 2}))[1]["code"] == "UPSTREAM_UNAVAILABLE"


# ---------------------------------------------------------------- audit log


def test_every_invocation_is_logged_with_client_tool_and_result(stack):
    run(call(stack, "get_incident", {"incident_id": 2}, client_id="healthcore-agent"))
    run(call(stack, "query_inventory", {"operation": "update", "sku": "HCR-PPE-001"}, client_id="inventory-reader"))
    run(call(stack, "create_incident", {"title": "Secret patient name", "description": "free text with details",
                                        "category": "other", "origin": "internal", "branch": "central"},
             client_id="healthcore-agent"))
    lines = [json.loads(line) for line in stack.audit_log.read_text().splitlines()]
    assert [(l["tool"], l["client_id"], l["result"], l["error_code"]) for l in lines] == [
        ("get_incident", "healthcore-agent", "ok", None),
        ("query_inventory", "inventory-reader", "error", "INVENTORY_READ_ONLY"),
        ("create_incident", "healthcore-agent", "error", "INSUFFICIENT_SCOPE"),
    ]
    assert "Secret patient name" not in stack.audit_log.read_text()  # free text is never logged
    assert all(l["duration_ms"] >= 0 and l["ts"] for l in lines)
