"""The agent's tools as an MCP client (langchain-mcp-adapters) of the HealthCore MCP server:
OAuth client credentials, contracts, timeout and failure results. Runs against the real MCP
server with a test OAuth server and a stand-in HealthCore API (tests/mcp_stack.py).

    python -m pytest tests/pipelines/test_agent_tools.py
"""

import ast
import socket
import threading
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from services.agent import tools
from tests.mcp_stack import running_stack

AGENT_DIR = Path(__file__).resolve().parents[2] / "services" / "agent"


@pytest.fixture
def stack(monkeypatch, tmp_path):
    with running_stack(monkeypatch, tmp_path) as running:
        monkeypatch.setenv("MCP_SERVER_URL", running.mcp_url)
        monkeypatch.setenv("MCP_OAUTH_TOKEN_URL", f"{running.oauth.url}/token")
        monkeypatch.setenv("AGENT_MCP_CLIENT_ID", "healthcore-agent")
        monkeypatch.setenv("AGENT_MCP_CLIENT_SECRET", "agent-secret")
        monkeypatch.setattr(tools, "TOKENS", tools._TokenCache())
        yield running


def test_ticket_by_id_goes_through_the_mcp_server(stack):
    result = tools.lookup_tickets(tools.TicketQuery(ticket_id=2))

    assert result.status == "ok" and result.endpoint == "MCP get_incident"
    assert result.tickets[0].status == "open" and result.tickets[0].branch == "london_west"
    assert "description" not in result.tickets[0].model_dump()
    assert stack.api.requests[-1] == ("GET", "/api/incidents/2")  # reached through the MCP server
    audit = stack.audit_log.read_text()
    assert '"client_id": "healthcore-agent"' in audit and '"tool": "get_incident"' in audit


def test_ticket_filters_use_list_incidents(stack):
    result = tools.lookup_tickets(tools.TicketQuery(status="open", branch="london_west"))
    assert result.status == "ok" and result.endpoint == "MCP list_incidents" and result.total == 2
    assert [t.id for t in result.tickets] == [26, 2]


def test_unknown_ticket_is_not_found(stack):
    result = tools.lookup_tickets(tools.TicketQuery(ticket_id=482))
    assert result.status == "not_found" and result.error.startswith("NOT_FOUND")


def test_stock_goes_through_query_inventory(stack):
    result = tools.lookup_stock(tools.StockQuery(product="guantes de nitrilo"))
    assert result.status == "ok" and result.endpoint == "MCP query_inventory"
    assert [(p.sku, p.current_stock) for p in result.products] == [("HCR-PPE-001", 102)]
    assert tools.lookup_stock(tools.StockQuery(product="desfibriladores")).status == "not_found"


def test_the_agent_token_only_carries_read_scopes(stack):
    """Even if the agent tried, its OAuth client cannot write tickets."""
    with pytest.raises(tools.McpCallFailed) as denied:
        tools.call_mcp_tool("update_incident_status", {"incident_id": 2, "status": "discarded"})
    assert denied.value.code == "INSUFFICIENT_SCOPE"
    assert not [r for r in stack.api.requests if r[0] != "GET" and r[1] != "/login"]


def test_wrong_client_secret_is_unavailable_not_a_crash(stack, monkeypatch):
    monkeypatch.setenv("AGENT_MCP_CLIENT_SECRET", "wrong")
    result = tools.lookup_tickets(tools.TicketQuery(ticket_id=2))
    assert result.status == "unavailable" and result.error.startswith("OAUTH_TOKEN_REJECTED")


def test_upstream_failure_reported_by_the_mcp_server_is_unavailable(stack):
    stack.api.server.shutdown()
    stack.api.server.server_close()
    result = tools.lookup_tickets(tools.TicketQuery(ticket_id=2))
    assert result.status == "unavailable" and result.error.startswith("UPSTREAM_UNAVAILABLE")


def test_mcp_server_down_is_unavailable(stack, monkeypatch):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setenv("MCP_SERVER_URL", f"http://127.0.0.1:{port}/mcp")
    result = tools.lookup_tickets(tools.TicketQuery(ticket_id=2))
    assert result.status == "unavailable" and result.error.startswith("MCP_UNREACHABLE")


def test_timeout_is_explicit_and_never_hangs(stack, monkeypatch):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    held = []
    threading.Thread(target=lambda: held.append(listener.accept()[0]), daemon=True).start()
    monkeypatch.setenv("MCP_SERVER_URL", f"http://127.0.0.1:{listener.getsockname()[1]}/mcp")
    monkeypatch.setenv("AGENT_TOOL_TIMEOUT_SECONDS", "0.5")
    start = time.perf_counter()
    result = tools.lookup_tickets(tools.TicketQuery(ticket_id=2))
    assert result.status == "unavailable" and result.error == "TIMEOUT: timeout after 0.5 s"
    assert time.perf_counter() - start < 2.5
    listener.close()


def test_default_timeout_is_four_seconds(monkeypatch):
    monkeypatch.delenv("AGENT_TOOL_TIMEOUT_SECONDS", raising=False)
    assert tools.TOOL_TIMEOUT_SECONDS == 4.0 and tools.tool_timeout() == 4.0


def test_contracts_reject_invalid_input():
    with pytest.raises(ValidationError):
        tools.TicketQuery()
    with pytest.raises(ValidationError):
        tools.TicketQuery(status="closed")
    with pytest.raises(ValidationError):
        tools.StockQuery(product="x")


def test_the_agent_has_no_direct_path_to_the_incidents_manager():
    """Migration check: no agent module calls the HealthCore API; tickets only flow through MCP."""
    for path in AGENT_DIR.glob("*.py"):
        source = path.read_text()
        assert "/api/incidents" not in source.replace("GET /api/incidents", ""), path.name
        assert "AGENT_TOOLS_API_URL" not in source and "tool_credentials" not in source, path.name
    imported = {node.names[0].name for node in ast.walk(ast.parse((AGENT_DIR / "tools.py").read_text()))
                if isinstance(node, ast.Import)}
    assert "httpx" in imported  # only for the OAuth token request
    assert '"/api/' not in (AGENT_DIR / "tools.py").read_text()
