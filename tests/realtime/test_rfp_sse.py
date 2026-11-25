"""SSE of the RFP flow (Sistemas en Tiempo Real, Parte 1), tested on the wire: a real uvicorn server
in a thread and an HTTP client that reads the raw `text/event-stream` — headers, `event:` name,
`id:`, JSON `data:` with the BRIEFING fields, keep-alive comments, the JWT, the Last-Event-ID
replay after a disconnection (no duplicates) and the resync when the cursor is unknown.

    python -m pytest tests/realtime/test_rfp_sse.py
"""

import json
import queue
import socket
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import uvicorn

from data.pipelines.rfp_intake import events, phi, runner, store
from services.api import realtime
from services.api.main import app
from services.api.routes import rfp
from services.api.tests.conftest import (  # noqa: F401
    auth_headers,
    client,
    database,
    outbox,
    password_hash,
    user_factory,
)
from tests.pipelines.rfp_fixtures import FORMAL, INFORMAL, INVALID, WITH_PHI, engine, model  # noqa: F401

FIELDS = ["ticket_id", "rfp_id", "client_name", "client_country", "program_type", "status", "created_at"]


@pytest.fixture
def server(client, engine, monkeypatch):  # noqa: F811
    monkeypatch.setenv("RFP_SSE_HEARTBEAT_SECONDS", "0.3")
    monkeypatch.setenv("RFP_SSE_POLL_SECONDS", "0.2")
    monkeypatch.setenv("RFP_AUTO_GENERATE", "0")  # Part 2 is not part of this test
    app.dependency_overrides[rfp.get_rfp_engine] = lambda: engine
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    # lifespan="off": the TestClient of the `client` fixture already opened the app state (users DB)
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="warning",
                                        timeout_graceful_shutdown=1))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(5)
    app.dependency_overrides.pop(rfp.get_rfp_engine, None)


@pytest.fixture
def headers(user_factory, auth_headers):  # noqa: F811
    return auth_headers(user_factory(username="tom"))


class SseReader:
    """Reads a live SSE response in a thread: raw text + parsed frames (event / id / data / comment)."""

    def __init__(self, base, headers, last_event_id=None):
        h = dict(headers)
        if last_event_id is not None:
            h["Last-Event-ID"] = str(last_event_id)
        self.client = httpx.Client(timeout=httpx.Timeout(10, read=10))
        self.response = self.client.send(self.client.build_request("GET", f"{base}/api/rfp/events", headers=h),
                                         stream=True)
        self.frames: queue.Queue = queue.Queue()
        self.raw: list[str] = []
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        current: dict = {}
        try:
            for line in self.response.iter_lines():
                self.raw.append(line)
                if line == "":
                    if current:
                        self.frames.put(current)
                    current = {}
                elif line.startswith(":"):
                    current["comment"] = line[1:].strip()
                else:
                    key, _, value = line.partition(":")
                    current[key] = value[1:] if value.startswith(" ") else value
        except (httpx.ReadError, httpx.RemoteProtocolError, httpx.StreamClosed, httpx.ReadTimeout):
            pass
        self.frames.put(None)  # stream ended

    def next(self, event=None, timeout=8.0):
        deadline = time.monotonic() + timeout
        while True:
            frame = self.frames.get(timeout=max(0.01, deadline - time.monotonic()))
            if frame is None:
                raise AssertionError("the stream ended")
            if event is None or frame.get("event") == event:
                return frame

    def collect(self, seconds):
        found, deadline = [], time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                frame = self.frames.get(timeout=0.05)
            except queue.Empty:
                continue
            if frame is None:
                break
            found.append(frame)
        return found

    def close(self):
        self.response.close()
        self.client.close()


def upload(base, headers, path):
    with httpx.Client(timeout=30) as c:
        response = c.post(f"{base}/api/rfp/tickets", headers=headers,
                          files={"file": (path.name, path.read_bytes(), "application/pdf")})
    assert response.status_code == 202
    return response.json()


def intake(engine, path):  # noqa: F811
    """A ticket registered outside this API process (the CLI): the event still reaches the stream."""
    ticket = store.receive(engine, path.read_bytes(), created_by="cli")
    runner.run_intake(engine, ticket.ticket_id)
    return ticket.ticket_id


# ================================================================ authentication


def test_the_stream_requires_the_backoffice_jwt(server):
    with httpx.Client(timeout=5) as c:
        anonymous = c.get(f"{server}/api/rfp/events")
        forged = c.get(f"{server}/api/rfp/events", headers={"Authorization": "Bearer not-a-jwt"})
    for response in (anonymous, forged):
        assert response.status_code == 401
        assert not response.headers["content-type"].startswith("text/event-stream")
        assert "rfp_ticket_created" not in response.text


# ================================================================ wire format


def test_a_new_rfp_ticket_is_pushed_as_a_named_event_with_the_briefing_payload(server, headers, engine, model):  # noqa: F811
    stream = SseReader(server, headers)
    try:
        assert stream.response.status_code == 200
        assert stream.response.headers["content-type"].startswith("text/event-stream")
        assert stream.response.headers["cache-control"].startswith("no-cache")
        assert stream.response.headers["x-accel-buffering"] == "no"
        assert stream.next() == {"retry": "3000"}  # reconnection hint for SSE clients
        ready = stream.next()
        assert ready["event"] == "stream_ready" and json.loads(ready["data"])["replayed"] == 0
        ticket = upload(server, headers, FORMAL)
        frame = stream.next("rfp_ticket_created")
        data = json.loads(frame["data"])
        assert list(data) == FIELDS  # exactly the ticket fields of the RFP system, no envelope
        assert data == {"ticket_id": ticket["ticket_id"], "rfp_id": ticket["rfp_id"],
                        "client_name": "Meridian Manufacturing, Inc.", "client_country": "US",
                        "program_type": "occupational_health", "status": "analizando",
                        "created_at": data["created_at"]}
        assert data["created_at"].endswith("Z") and datetime.fromisoformat(data["created_at"].replace("Z", "+00:00"))
        assert int(frame["id"]) == events.latest_id(engine)  # the resume cursor
        raw = "\n".join(stream.raw)
        assert f"id: {frame['id']}\nevent: rfp_ticket_created\ndata: {{\"ticket_id\":" in raw  # SSE framing on the wire
        assert "event: message" not in raw
        assert stream.next(timeout=2)["comment"] == "keep-alive"  # heartbeat keeps the connection open
    finally:
        stream.close()


def test_a_document_the_classifier_discards_never_notifies(server, headers, engine, model):  # noqa: F811
    stream = SseReader(server, headers)
    try:
        stream.next("stream_ready")
        upload(server, headers, INVALID)
        frames = stream.collect(1.5)
        assert [f for f in frames if f.get("event") == "rfp_ticket_created"] == []
        assert events.latest_id(engine) == 0
    finally:
        stream.close()


def test_the_payload_never_carries_phi_and_no_model_is_called(server, headers, engine, model):  # noqa: F811
    stream = SseReader(server, headers)
    try:
        stream.next("stream_ready")
        upload(server, headers, WITH_PHI)  # an "RFP" with an attached clinical summary (redacted by Part 1)
        data = json.loads(stream.next("rfp_ticket_created")["data"])
        assert data["client_name"] == "Gulfline Logistics, Inc." and phi.find_phi(json.dumps(data)) == []
        for _ in range(100):  # let the intake (which does call agents) finish in the background
            ticket = store.ticket_view(engine, data["ticket_id"])
            if ticket["status"] != "analizando":
                break
            time.sleep(0.05)
        calls = len(model.calls)  # publishing is not an agent: no model call
        assert events.publish_ticket_created(engine, data["ticket_id"], ticket["metadata"]) is None  # once per ticket
        assert len(model.calls) == calls
    finally:
        stream.close()


# ================================================================ reconnection and recovery


def test_reconnecting_with_last_event_id_replays_what_was_missed_without_duplicates(server, headers, engine, model):  # noqa: F811
    first = SseReader(server, headers)
    try:
        first.next("stream_ready")
        a = upload(server, headers, FORMAL)
        seen = first.next("rfp_ticket_created")
        assert json.loads(seen["data"])["ticket_id"] == a["ticket_id"]
    finally:
        first.close()  # the connection drops
    b, c = intake(engine, INFORMAL), intake(engine, FORMAL)  # registered while the dashboard was offline
    runner.run_intake(engine, b)  # a reprocess never notifies a second time

    second = SseReader(server, headers, last_event_id=seen["id"])
    try:
        ready = json.loads(second.next("stream_ready")["data"])
        assert ready == {"last_event_id": int(seen["id"]), "replayed": 2, "heartbeat_seconds": 0.3}
        replayed = [second.next("rfp_ticket_created"), second.next("rfp_ticket_created")]
        assert [json.loads(f["data"])["ticket_id"] for f in replayed] == [b, c]
        assert [int(f["id"]) for f in replayed] == [int(seen["id"]) + 1, int(seen["id"]) + 2]
        d = upload(server, headers, INFORMAL)  # and then live again, on the same connection
        live = second.next("rfp_ticket_created")
        assert json.loads(live["data"])["ticket_id"] == d["ticket_id"]
        later = second.collect(1.0)
        ticket_ids = [json.loads(f["data"])["ticket_id"] for f in later if f.get("event") == "rfp_ticket_created"]
        assert ticket_ids == []  # nothing sent twice (the replay/live overlap is skipped by id)
    finally:
        second.close()


def test_an_unknown_last_event_id_asks_the_client_to_resync(server, headers):
    stream = SseReader(server, headers, last_event_id="9999")
    try:
        stream.next("stream_ready")
        resync = stream.next()
        assert resync["event"] == "resync_required" and json.loads(resync["data"])["reason"] == "unknown_last_event_id"
    finally:
        stream.close()


# ================================================================ several dashboards, limits, session expiry


def test_several_dashboards_share_one_hub_per_process(server, headers, engine, model):  # noqa: F811
    streams = [SseReader(server, headers) for _ in range(3)]
    try:
        for s in streams:
            s.next("stream_ready")
        hubs = [h for h in realtime._hubs.values() if h.engine is engine]
        assert len(hubs) == 1 and len(hubs[0].subscribers) == 3  # one reader of rfp_event for all connections
        ticket = upload(server, headers, FORMAL)
        assert {json.loads(s.next("rfp_ticket_created")["data"])["ticket_id"] for s in streams} == {ticket["ticket_id"]}
    finally:
        for s in streams:
            s.close()


def test_connections_per_user_are_limited(server, headers, monkeypatch):
    monkeypatch.setattr(rfp, "MAX_STREAMS_PER_USER", 1)
    first = SseReader(server, headers)
    try:
        first.next("stream_ready")
        with httpx.Client(timeout=5) as c:
            assert c.get(f"{server}/api/rfp/events", headers=headers).status_code == 429
    finally:
        first.close()


def test_a_server_shutdown_ends_every_stream_so_a_restart_is_never_blocked(server, headers):
    """Found in the live check: uvicorn's graceful shutdown waited for the open SSE streams, which
    never end, so the old process kept serving the dashboards after a restart."""
    streams = [SseReader(server, headers) for _ in range(2)]
    try:
        for s in streams:
            s.next("stream_ready")
        started = time.monotonic()
        realtime.shutdown_streams()  # what the SIGTERM hook calls (install_shutdown_hook)
        for s in streams:
            with pytest.raises(AssertionError, match="the stream ended"):
                s.next("rfp_ticket_created", timeout=3)
        assert time.monotonic() - started < 2  # at once, not after a heartbeat or a timeout
        with httpx.Client(timeout=5) as c:  # while shutting down, new streams are refused (the client retries)
            assert c.get(f"{server}/api/rfp/events", headers=headers).status_code == 503
    finally:
        for s in streams:
            s.close()
        realtime._shutting_down.clear()


def test_sigterm_ends_the_streams_and_then_runs_the_server_handler():
    """uvicorn installs its handler before loading the app; the hook wraps whatever is installed."""
    import signal

    called = []
    original = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, lambda signum, frame: called.append(signum))  # stands for uvicorn's handle_exit
    try:
        realtime.install_shutdown_hook()
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert realtime._shutting_down.is_set() and called == [signal.SIGTERM]
    finally:
        signal.signal(signal.SIGTERM, original)
        realtime._shutting_down.clear()


def test_the_stream_ends_when_the_jwt_expires(server, headers, monkeypatch):
    monkeypatch.setattr(rfp, "_token_expiry", lambda _token: datetime.now(timezone.utc) + timedelta(seconds=0.5))
    stream = SseReader(server, headers)
    try:
        stream.next("stream_ready")
        assert stream.next("session_expired", timeout=3)["data"] == '{"reason":"token_expired"}'
        with pytest.raises(AssertionError, match="the stream ended"):
            stream.next(timeout=3)
    finally:
        stream.close()
