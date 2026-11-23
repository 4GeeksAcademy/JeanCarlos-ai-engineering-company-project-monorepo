"""Read-only tools of the agent against a local HTTP server that plays the incident and
inventory APIs: contract, credentials, GET only, timeout and failure results.

    python -m pytest tests/pipelines/test_agent_tools.py
"""

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from pydantic import ValidationError

from services.agent import tools

TICKET = {"id": 2, "title": "Patient not informed of appointment reschedule by clinic",
          "description": "free text that may carry patient details", "category": "patient_experience",
          "status": "open", "origin": "customer", "branch": "london_west",
          "created_at": "2025-03-12T09:00:00Z", "updated_at": "2025-03-14T10:30:00Z"}
PRODUCTS = [
    {"id": 1, "name": "Guantes de nitrilo (caja de 100)", "sku": "HCR-PPE-001", "category": "ppe", "unit": "box",
     "country": "US", "product_category": None, "expiry_date": None, "current_stock": 102},
    {"id": 7, "name": "Vacuna antigripal (dosis)", "sku": "HCR-MED-002", "category": "medications", "unit": "vial",
     "country": "UK", "product_category": None, "expiry_date": None, "current_stock": 25},
]


class FakeApi(BaseHTTPRequestHandler):
    requests: list = []
    delay = 0.0
    fail = None

    def log_message(self, *args):
        pass

    def _send(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        FakeApi.requests.append(("GET", self.path, self.headers.get("Authorization")))
        time.sleep(FakeApi.delay)
        if FakeApi.fail:
            return self._send(FakeApi.fail, {"detail": "boom"})
        if self.path == "/api/incidents/2":
            return self._send(200, TICKET)
        if self.path.startswith("/api/incidents/"):
            return self._send(404, {"detail": "Incident not found"})
        if self.path.startswith("/api/incidents?"):
            return self._send(200, [TICKET] if "status=open" in self.path else [])
        if self.path == "/inventory/products":
            return self._send(200, PRODUCTS)
        return self._send(404, {})

    def _write_attempt(self):
        FakeApi.requests.append((self.command, self.path, None))
        self._send(405, {})

    do_POST = do_PATCH = do_PUT = do_DELETE = _write_attempt


@pytest.fixture
def api(monkeypatch):
    FakeApi.requests, FakeApi.delay, FakeApi.fail = [], 0.0, None
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("AGENT_TOOLS_API_URL", f"http://127.0.0.1:{server.server_port}")
    with tools.tool_credentials("coordinator-jwt"):
        yield FakeApi
    server.shutdown()


def test_ticket_by_id_reads_the_incident_api_with_the_callers_token(api):
    result = tools.lookup_tickets(tools.TicketQuery(ticket_id=2))

    assert result.status == "ok" and result.endpoint == "GET /api/incidents/2"
    assert result.tickets[0].status == "open" and result.tickets[0].branch == "london_west"
    assert "description" not in result.tickets[0].model_dump()  # free text never leaves the tool
    assert api.requests == [("GET", "/api/incidents/2", "Bearer coordinator-jwt")]


def test_ticket_filters_are_sent_as_query_parameters(api):
    result = tools.lookup_tickets(tools.TicketQuery(status="open", branch="london_west"))
    assert result.status == "ok" and result.total == 1
    assert api.requests[0][1] == "/api/incidents?status=open&branch=london_west"


def test_unknown_ticket_is_not_found(api):
    result = tools.lookup_tickets(tools.TicketQuery(ticket_id=482))
    assert result.status == "not_found" and result.tickets == []


def test_timeout_is_explicit_and_returns_unavailable(api, monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_TIMEOUT_SECONDS", "0.3")
    api.delay = 2.0
    start = time.perf_counter()
    result = tools.lookup_tickets(tools.TicketQuery(ticket_id=2))
    assert result.status == "unavailable" and result.error == "timeout after 0.3 s"
    assert time.perf_counter() - start < 1.5


def test_default_timeout_is_four_seconds():
    assert tools.TOOL_TIMEOUT_SECONDS == 4.0 and tools.tool_timeout() == 4.0


@pytest.mark.parametrize("status", [500, 503, 401])
def test_server_errors_and_rejected_credentials_are_unavailable(api, status):
    api.fail = status
    assert tools.lookup_tickets(tools.TicketQuery(ticket_id=2)).status == "unavailable"
    assert tools.lookup_stock(tools.StockQuery(product="guantes")).status == "unavailable"


def test_service_down_is_unavailable(monkeypatch):
    with socket.socket() as probe:  # a port with nothing listening
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setenv("AGENT_TOOLS_API_URL", f"http://127.0.0.1:{port}")
    with tools.tool_credentials("jwt"):
        result = tools.lookup_tickets(tools.TicketQuery(ticket_id=2))
    assert result.status == "unavailable" and result.error == "ConnectError"


def test_without_credentials_the_tool_does_not_call_the_api(api):
    with tools.tool_credentials(None):
        result = tools.lookup_tickets(tools.TicketQuery(ticket_id=2))
    assert result.status == "unavailable" and api.requests == []


@pytest.mark.parametrize("product, expected", [
    ("guantes de nitrilo", ["HCR-PPE-001"]), ("Guantes", ["HCR-PPE-001"]), ("vacunas antigripales", ["HCR-MED-002"]),
    ("hcr-med-002", ["HCR-MED-002"]), ("desfibriladores", []),
])
def test_stock_matches_products_by_name_or_sku(api, product, expected):
    result = tools.lookup_stock(tools.StockQuery(product=product))
    assert [p.sku for p in result.products] == expected
    assert result.status == ("ok" if expected else "not_found")


def test_tools_only_issue_get_requests(api):
    tools.lookup_tickets(tools.TicketQuery(ticket_id=2))
    tools.lookup_tickets(tools.TicketQuery(status="open"))
    tools.lookup_stock(tools.StockQuery(product="guantes"))
    assert {method for method, *_ in api.requests} == {"GET"}


def test_contracts_reject_invalid_input():
    with pytest.raises(ValidationError):
        tools.TicketQuery()  # neither id nor filter
    with pytest.raises(ValidationError):
        tools.TicketQuery(status="closed")  # not in the incident catalog
    with pytest.raises(ValidationError):
        tools.TicketQuery(ticket_id=0)
    with pytest.raises(ValidationError):
        tools.StockQuery(product="x")
