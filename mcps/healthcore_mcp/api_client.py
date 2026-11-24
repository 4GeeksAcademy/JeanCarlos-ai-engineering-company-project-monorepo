"""Client of the existing HealthCore API: the MCP server builds on these services, it does
not replace them or touch their databases.

- Incidents Manager: GET /api/incidents, GET /api/incidents/{id}, POST /api/incidents and
  PATCH /api/incidents/{id}/status (the lifecycle endpoint; there is no generic PATCH).
- Inventory: GET /inventory/products only. This client has no method that writes inventory.

Authentication: a dedicated HealthCore API user with the plain `user` role (MCP_API_USERNAME /
MCP_API_PASSWORD), logged in through POST /login; the token is cached and renewed on 401.
Every call has an explicit timeout (MCP_API_TIMEOUT_SECONDS, default 4 s).
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from .errors import ErrorCode, McpToolError

API_TIMEOUT_SECONDS = 4.0


def _settings() -> tuple[str, str, str, float]:
    load_dotenv(Path(__file__).resolve().parent / ".env")
    username, password = os.environ.get("MCP_API_USERNAME"), os.environ.get("MCP_API_PASSWORD")
    if not username or not password:
        raise McpToolError(ErrorCode.UPSTREAM_AUTH_FAILED, "The MCP server has no HealthCore API service account configured.")
    return (os.environ.get("HEALTHCORE_API_URL", "http://127.0.0.1:8000").rstrip("/"), username, password,
            float(os.environ.get("MCP_API_TIMEOUT_SECONDS", API_TIMEOUT_SECONDS)))


class HealthCoreApi:
    def __init__(self) -> None:
        self._token: str | None = None
        self._lock = threading.Lock()

    def _login(self, client: httpx.Client, username: str, password: str) -> str:
        response = client.post("/login", data={"username": username, "password": password})
        if response.status_code != 200:
            raise McpToolError(ErrorCode.UPSTREAM_AUTH_FAILED, "The HealthCore API rejected the MCP service account.",
                               {"http_status": response.status_code})
        return response.json()["access_token"]

    def request(self, method: str, path: str, *, params: dict[str, Any] | None = None,
                json: dict[str, Any] | None = None) -> httpx.Response:
        base_url, username, password, timeout = _settings()
        try:
            with httpx.Client(base_url=base_url, timeout=httpx.Timeout(timeout)) as client:
                for attempt in (1, 2):
                    with self._lock:
                        if self._token is None:
                            self._token = self._login(client, username, password)
                        token = self._token
                    response = client.request(method, path, params=params, json=json,
                                              headers={"Authorization": f"Bearer {token}"})
                    if response.status_code == 401 and attempt == 1:  # expired: log in again once
                        with self._lock:
                            self._token = None
                        continue
                    break
        except httpx.TimeoutException as error:
            raise McpToolError(ErrorCode.UPSTREAM_TIMEOUT, f"The HealthCore API did not answer within {timeout:g} s.",
                               {"endpoint": f"{method} {path}"}) from error
        except httpx.HTTPError as error:
            raise McpToolError(ErrorCode.UPSTREAM_UNAVAILABLE, "The HealthCore API is not reachable.",
                               {"endpoint": f"{method} {path}", "reason": type(error).__name__}) from error
        if response.status_code == 401:
            raise McpToolError(ErrorCode.UPSTREAM_AUTH_FAILED, "The HealthCore API rejected the MCP service account.")
        if response.status_code >= 500:
            raise McpToolError(ErrorCode.UPSTREAM_UNAVAILABLE, "The HealthCore API returned a server error.",
                               {"endpoint": f"{method} {path}", "http_status": response.status_code})
        return response

    # ------------------------------------------------------------ Incidents Manager

    @staticmethod
    def _incident_errors(response: httpx.Response, incident_id: int | None = None) -> None:
        if response.status_code == 404:
            raise McpToolError(ErrorCode.NOT_FOUND, f"Ticket {incident_id} does not exist.", {"incident_id": incident_id})
        if response.status_code in (400, 422):
            body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            detail = body.get("detail") if isinstance(body, dict) else None
            errors = body.get("errors", []) if isinstance(body, dict) else []
            code = (ErrorCode.INVALID_STATUS_TRANSITION if detail == "This status change is not allowed."
                    else ErrorCode.VALIDATION_ERROR)
            raise McpToolError(code, detail if isinstance(detail, str) else "The Incidents Manager rejected the input.",
                               {"errors": errors})

    def get_incident(self, incident_id: int) -> dict[str, Any]:
        response = self.request("GET", f"/api/incidents/{incident_id}")
        self._incident_errors(response, incident_id)
        return response.json()

    def list_incidents(self, filters: dict[str, str]) -> list[dict[str, Any]]:
        response = self.request("GET", "/api/incidents", params=filters)
        self._incident_errors(response)
        return response.json()

    def create_incident(self, payload: dict[str, str]) -> dict[str, Any]:
        response = self.request("POST", "/api/incidents", json=payload)
        self._incident_errors(response)
        return response.json()

    def change_incident_status(self, incident_id: int, status: str) -> dict[str, Any]:
        response = self.request("PATCH", f"/api/incidents/{incident_id}/status", json={"status": status})
        self._incident_errors(response, incident_id)
        return response.json()

    # ------------------------------------------------------------ inventory (read only)

    def list_products(self) -> list[dict[str, Any]]:
        response = self.request("GET", "/inventory/products")
        if response.status_code != 200:
            raise McpToolError(ErrorCode.UPSTREAM_UNAVAILABLE, "The inventory did not return the products.",
                               {"http_status": response.status_code})
        return response.json()


API = HealthCoreApi()
