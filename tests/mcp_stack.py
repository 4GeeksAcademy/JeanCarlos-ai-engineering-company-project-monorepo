"""A complete MCP stack for tests, all on localhost, no Docker:

- a test OAuth 2.1 / OIDC authorization server (discovery, JWKS, /token with client credentials
  and per-client scopes), signing RS256 JWTs with a throwaway key;
- a stand-in of the HealthCore API (POST /login, /api/incidents, PATCH …/status,
  GET /inventory/products) that records every request it receives;
- the real HealthCore MCP server (mcps.healthcore_mcp.server.create_app) under uvicorn, validating
  tokens with MCP Auth in "jwt" mode against that authorization server.
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from packages.shared.incidents import TRANSITIONS

AUDIENCE = "healthcore-mcp"
CLIENTS = {
    "ops": ("ops-secret", "incidents:read incidents:write inventory:read"),
    "healthcore-agent": ("agent-secret", "incidents:read inventory:read"),
    "inventory-reader": ("inventory-secret", "inventory:read"),
}
SERVICE_USER = ("mcp-service", "service-password")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _serve(handler: type[BaseHTTPRequestHandler]) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


class _Json(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, status: int, body: Any) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))


@dataclass
class OAuthServer:
    key: Any = field(default_factory=lambda: rsa.generate_private_key(public_exponent=65537, key_size=2048))
    url: str = ""
    server: Any = None

    def token(self, client_id: str = "ops", scope: str | None = None, *, audience: str = AUDIENCE,
              issuer: str | None = None, expires_in: int = 300, key: Any = None) -> str:
        now = int(time.time())
        claims = {"iss": issuer or self.url, "sub": f"service-account-{client_id}", "azp": client_id, "aud": audience,
                  "scope": CLIENTS[client_id][1] if scope is None else scope, "iat": now, "exp": now + expires_in}
        return jwt.encode(claims, key or self.key, algorithm="RS256", headers={"kid": "test-key"})

    def start(self) -> "OAuthServer":
        outer = self
        public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))

        class Handler(_Json):
            def do_GET(self):
                if self.path == "/.well-known/openid-configuration":
                    return self.send_json(200, {
                        "issuer": outer.url, "authorization_endpoint": f"{outer.url}/auth",
                        "token_endpoint": f"{outer.url}/token", "jwks_uri": f"{outer.url}/jwks",
                        "response_types_supported": ["code"], "grant_types_supported": ["client_credentials"],
                        "code_challenge_methods_supported": ["S256"],
                        "scopes_supported": ["incidents:read", "incidents:write", "inventory:read"]})
                if self.path == "/jwks":
                    return self.send_json(200, {"keys": [{**public_jwk, "kid": "test-key", "use": "sig", "alg": "RS256"}]})
                self.send_json(404, {})

            def do_POST(self):
                form = {k: v[0] for k, v in parse_qs(self.body().decode()).items()}
                client = CLIENTS.get(form.get("client_id", ""))
                if self.path != "/token" or not client or form.get("client_secret") != client[0]:
                    return self.send_json(401, {"error": "unauthorized_client"})
                self.send_json(200, {"access_token": outer.token(form["client_id"]), "token_type": "Bearer",
                                     "expires_in": 300})

        self.server, self.url = _serve(Handler)
        return self


INCIDENTS = [
    {"id": 2, "title": "Patient not informed of appointment reschedule by clinic",
     "description": "Patient not informed of appointment reschedule by clinic", "category": "patient_experience",
     "status": "open", "origin": "customer", "branch": "london_west",
     "created_at": "2024-01-17T00:00:00Z", "updated_at": "2024-01-17T00:00:00Z"},
    {"id": 26, "title": "Duplicate charge for the same consultation detected",
     "description": "Duplicate charge for the same consultation detected", "category": "billing_error",
     "status": "open", "origin": "customer", "branch": "london_west",
     "created_at": "2024-01-31T00:00:00Z", "updated_at": "2024-01-31T00:00:00Z"},
]
PRODUCTS = [
    {"id": 1, "name": "Guantes de nitrilo (caja de 100)", "sku": "HCR-PPE-001", "category": "ppe", "unit": "box",
     "country": "US", "product_category": "ppe", "expiry_date": None, "current_stock": 102},
    {"id": 7, "name": "Vacuna antigripal (dosis)", "sku": "HCR-MED-002", "category": "medications", "unit": "vial",
     "country": "UK", "product_category": "medication", "expiry_date": "2027-03-31", "current_stock": 25},
]


@dataclass
class FakeHealthCoreApi:
    url: str = ""
    server: Any = None
    requests: list = field(default_factory=list)
    incidents: dict = field(default_factory=lambda: {i["id"]: dict(i) for i in INCIDENTS})
    delay: float = 0.0

    def start(self) -> "FakeHealthCoreApi":
        api = self

        class Handler(_Json):
            def _authorized(self) -> bool:
                return self.headers.get("Authorization") == "Bearer service-token"

            def _record(self):
                api.requests.append((self.command, self.path))
                time.sleep(api.delay)

            def do_GET(self):
                self._record()
                if not self._authorized():
                    return self.send_json(401, {"detail": "Invalid or expired credentials"})
                path = urlparse(self.path)
                if path.path == "/inventory/products":
                    return self.send_json(200, PRODUCTS)
                if path.path == "/api/incidents":
                    query = {k: v[0] for k, v in parse_qs(path.query).items()}
                    rows = [i for i in api.incidents.values() if all(i[k] == v for k, v in query.items())]
                    return self.send_json(200, sorted(rows, key=lambda i: i["created_at"], reverse=True))
                if path.path.startswith("/api/incidents/"):
                    incident = api.incidents.get(int(path.path.rsplit("/", 1)[1]))
                    return self.send_json(200, incident) if incident else self.send_json(404, {"detail": "Incident not found"})
                self.send_json(404, {})

            def do_POST(self):
                self._record()
                body = self.body()
                if self.path == "/login":
                    form = {k: v[0] for k, v in parse_qs(body.decode()).items()}
                    ok = (form.get("username"), form.get("password")) == SERVICE_USER
                    return self.send_json(200, {"access_token": "service-token"}) if ok else self.send_json(401, {})
                if not self._authorized():
                    return self.send_json(401, {})
                if self.path == "/api/incidents":
                    data = json.loads(body)
                    now = datetime.now(timezone.utc).isoformat()
                    new = {**data, "id": max(api.incidents) + 1, "status": "open", "created_at": now, "updated_at": now}
                    api.incidents[new["id"]] = new
                    return self.send_json(201, new)
                self.send_json(405, {})

            def do_PATCH(self):
                self._record()
                body = json.loads(self.body())
                parts = self.path.strip("/").split("/")
                if not self._authorized():
                    return self.send_json(401, {})
                if len(parts) != 4 or parts[3] != "status":  # only the lifecycle endpoint exists
                    return self.send_json(405, {"detail": "Method Not Allowed"})
                incident = api.incidents.get(int(parts[2]))
                if not incident:
                    return self.send_json(404, {"detail": "Incident not found"})
                if body.get("status") not in TRANSITIONS[incident["status"]]:
                    return self.send_json(400, {"detail": "This status change is not allowed.", "errors": [
                        {"field": "status", "message": f"An incident that is {incident['status']} can only move to: "
                                                        f"{', '.join(TRANSITIONS[incident['status']]) or 'nothing'}."}]})
                incident["status"] = body["status"]
                return self.send_json(200, incident)

            do_PUT = do_DELETE = lambda self: (self._record(), self.send_json(405, {}))

        self.server, self.url = _serve(Handler)
        return self


@dataclass
class McpStack:
    oauth: OAuthServer
    api: FakeHealthCoreApi
    mcp_url: str
    audit_log: Any


@contextlib.contextmanager
def running_stack(monkeypatch, tmp_path) -> Iterator[McpStack]:
    import uvicorn
    from mcpauth.config import AuthServerType
    from mcpauth.utils import fetch_server_config

    oauth, api = OAuthServer().start(), FakeHealthCoreApi().start()
    port = free_port()
    mcp_url = f"http://127.0.0.1:{port}/mcp"
    for name, value in {"MCP_OAUTH_ISSUER": oauth.url, "MCP_RESOURCE_URL": mcp_url, "MCP_AUDIENCE": AUDIENCE,
                        "HEALTHCORE_API_URL": api.url, "MCP_API_USERNAME": SERVICE_USER[0],
                        "MCP_API_PASSWORD": SERVICE_USER[1], "MCP_AUDIT_LOG": str(tmp_path / "audit.jsonl")}.items():
        monkeypatch.setenv(name, value)

    from mcps.healthcore_mcp import api_client
    from mcps.healthcore_mcp.server import create_app

    monkeypatch.setattr(api_client.API, "_token", None)
    app = create_app(fetch_server_config(oauth.url, AuthServerType.OIDC))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        yield McpStack(oauth, api, mcp_url, tmp_path / "audit.jsonl")
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        oauth.server.shutdown()
        api.server.shutdown()
