"""WebSocket chat of the Compliance assistant (Sistemas en Tiempo Real, Parte 2): the event contract
(token_chunk, interrupt_requested, generation_interrupted, generation_completed, session_snapshot,
user_message), the same JWT as the backoffice, the socket bound to a session, a real abort of the
generation (the model stream is closed; no more tokens), the partial message kept as interrupted
and a new turn for new_input, rehydration on reconnect, pub/sub with several subscribers and no PHI.

The agent is the real graph (services.agent.runner.AgentRuntime); only the network-bound pieces are
stubbed: the retriever, the scope/self-evaluation model calls and the generation model, which
streams token by token with a delay like the real gateway.

    python -m pytest tests/realtime/test_chat_ws.py
"""

import concurrent.futures
import time
from types import SimpleNamespace

import pytest
from starlette.websockets import WebSocketDisconnect

from data.pipelines import rag
from services.agent import planner
from services.agent.chat import router as chat_router
from services.agent.chat.bus import ChatBus, channel
from services.agent.chat.manager import ChatManager, StreamGuard
from services.agent.chat.store import ChatStore
from services.agent.harness import scope
from services.agent.memory import llm as memory_llm
from services.agent.memory import store as memory_store
from services.agent.runner import AgentRuntime
from services.api.tests.conftest import (  # noqa: F401
    admin,
    auth_headers,
    client,
    database,
    outbox,
    password_hash,
    user_factory,
)

HIPAA = ("Bajo HIPAA, en EE. UU. el personal solo puede usar y divulgar PHI para tratamiento, pago y "
         "operaciones de atención, con el mínimo necesario. Cualquier proveedor que maneje PHI firma un "
         "Business Associate Agreement. Una brecha se notifica dentro de los sesenta días. Fuente: marco HIPAA.")
UK_GDPR = ("Según UK GDPR, en Reino Unido los datos de salud son de categoría especial (artículo 9) y cada "
           "encargado firma un Data Processing Agreement. Una brecha se notifica a la ICO en 72 horas. "
           "Fuente: marco UK GDPR.")
CHUNK = {"company": "healthcore", "source_document": "compliance.md", "section": "HIPAA", "language": "es",
         "chunk_index": 0, "text": "Marco regulatorio de HealthCore.", "score": 0.9}


def words(text):
    parts = text.split(" ")
    return [p + (" " if i < len(parts) - 1 else "") for i, p in enumerate(parts)]


class FakeStream:
    def __init__(self, model, tokens):
        self.model, self.tokens, self.closed = model, tokens, False

    def __iter__(self):
        for token in self.tokens:
            if self.closed:
                return
            time.sleep(self.model.delay)
            self.model.consumed[-1] += 1
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=token))])
        self.model.finished_at.append(time.monotonic())

    def close(self):
        self.closed = True
        self.model.closed.append(self.model.consumed[-1])


class FakeModel:
    """OpenAI-compatible streaming client: answers UK GDPR or HIPAA according to the question."""

    def __init__(self):
        self.chat = self.completions = self
        self.delay = 0.01
        self.calls: list[str] = []
        self.consumed: list[int] = []
        self.closed: list[int] = []
        self.finished_at: list[float] = []
        self.override: str | None = None

    def create(self, model, messages, temperature, stream=False):
        user = messages[-1]["content"].split("<mensaje_usuario>")[-1]  # the staff member's question only
        self.calls.append(user)
        answer = self.override or (UK_GDPR if "UK GDPR" in user else HIPAA)
        assert stream, "the chat must stream the generation"
        self.consumed.append(0)
        return FakeStream(self, words(answer))


@pytest.fixture
def model(monkeypatch, tmp_path):
    fake = FakeModel()
    monkeypatch.setattr(rag, "llm_client", lambda: fake)
    monkeypatch.setattr(rag, "settings", lambda: SimpleNamespace(generation_model="fake"))
    monkeypatch.setattr(rag, "retrieve", lambda question: [CHUNK])
    monkeypatch.setattr(planner, "plan_with_model", planner.plan_with_rules)
    monkeypatch.setattr(scope, "classify_scope", lambda message: {"category": "domain", "confidence": 1.0,
                                                                  "classifier": "rules", "reason": "test"})
    monkeypatch.setattr(memory_llm, "self_evaluate", lambda message, answer, memories: {"outcome": "none",
                                                                                        "reason": "test"})
    monkeypatch.setattr(memory_store, "_store", memory_store.MemoryStore(tmp_path / "memory.sqlite"))
    return fake


@pytest.fixture
def chat(client, model, tmp_path):  # noqa: F811
    runtime = AgentRuntime(tmp_path / "agent.sqlite")
    store, bus = ChatStore(tmp_path / "agent.sqlite"), ChatBus()
    service = chat_router.ChatService(store, bus, ChatManager(store, bus, lambda: runtime))
    chat_router.set_chat(service)
    yield service
    chat_router.set_chat(None)


@pytest.fixture
def staff(user_factory, auth_headers):  # noqa: F811
    record = user_factory(username="analista")
    return record, auth_headers(record)["Authorization"].split(" ", 1)[1]


def new_session(client, token):  # noqa: F811
    response = client.post("/agent/chat/sessions", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 201
    return response.json()


_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)


def recv(ws, timeout=10.0):
    return _pool.submit(ws.receive_json).result(timeout=timeout)


def recv_until(ws, event, limit=500, collected=None):
    collected = [] if collected is None else collected
    for _ in range(limit):
        message = recv(ws)
        collected.append(message)
        if message["event"] == event:
            return collected
    raise AssertionError(f"{event} not received")


def connect(client, session_id, token):  # noqa: F811
    ws = client.websocket_connect(f"/agent/chat/ws?session_id={session_id}")
    socket = ws.__enter__()
    socket.send_json({"event": "auth", "data": {"token": token}})
    snapshot = recv(socket)
    assert snapshot["event"] == "session_snapshot"
    return ws, socket, snapshot["data"]


# ================================================================ authentication and binding to a session


def test_the_socket_requires_the_backoffice_jwt_before_any_chat_event(client, chat, staff):  # noqa: F811
    record, token = staff
    session = new_session(client, token)["session_id"]
    with pytest.raises(WebSocketDisconnect) as refused:  # ?token= invalid: refused at the handshake
        with client.websocket_connect(f"/agent/chat/ws?session_id={session}&token=not-a-jwt"):
            pass
    assert refused.value.code == 4401
    with client.websocket_connect(f"/agent/chat/ws?session_id={session}") as ws:  # first frame is not auth
        ws.send_json({"event": "user_message", "data": {"session_id": session, "text": "hola"}})
        with pytest.raises(WebSocketDisconnect) as closed:
            recv(ws)
        assert closed.value.code == 4401
    with client.websocket_connect(f"/agent/chat/ws?session_id={session}&token={token}") as ws:  # ?token= valid
        assert recv(ws)["event"] == "session_snapshot"
    assert chat.store.messages(session) == []  # nothing reached the agent


@pytest.mark.parametrize("query, code", [("", 4400), ("?session_id=chat_000000000000", 4404),
                                         ("?session_id=../etc", 4400)])
def test_the_socket_is_bound_to_an_existing_session(client, chat, staff, query, code):  # noqa: F811
    _record, token = staff
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(f"/agent/chat/ws{query}") as ws:
            ws.send_json({"event": "auth", "data": {"token": token}})
            recv(ws)
    assert closed.value.code == code


def test_another_staff_member_cannot_join_the_session(client, chat, staff, user_factory, auth_headers):  # noqa: F811
    _record, token = staff
    session = new_session(client, token)["session_id"]
    other = auth_headers(user_factory(username="otro"))["Authorization"].split(" ", 1)[1]
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(f"/agent/chat/ws?session_id={session}") as ws:
            ws.send_json({"event": "auth", "data": {"token": other}})
            recv(ws)
    assert closed.value.code == 4403


# ================================================================ event contract and token streaming


def test_the_answer_streams_token_by_token_with_the_event_contract(client, chat, staff, model):  # noqa: F811
    record, token = staff
    session = new_session(client, token)
    assert session["agent_id"] == "compliance_assistant" and session["user_id"] == record["id"]
    assert session["status"] == "active" and session["session_id"].startswith("chat_")
    ws, socket, snapshot = connect(client, session["session_id"], token)
    try:
        assert snapshot["messages"] == [] and snapshot["streaming"] is None and snapshot["role"] == "participant"
        model.delay = 0.02
        socket.send_json({"event": "user_message", "data": {"session_id": session["session_id"],
                                                            "text": "¿Qué permite HIPAA sobre compartir PHI?"}})
        events = recv_until(socket, "generation_completed", collected=[])
    finally:
        ws.__exit__(None, None, None)
    names = [e["event"] for e in events]
    assert names[:2] == ["user_message", "generation_started"] and names[-1] == "generation_completed"
    chunks = [e["data"] for e in events if e["event"] == "token_chunk"]
    assert len(chunks) > 10  # many small events, not one answer at the end
    assert all(set(c) == {"session_id", "message_id", "token", "sequence"} for c in chunks)
    assert [c["sequence"] for c in chunks] == list(range(1, len(chunks) + 1))
    done = events[-1]["data"]
    assert set(done) == {"session_id", "message_id", "status", "text", "sequence"} and done["status"] == "complete"
    assert "".join(c["token"] for c in chunks) == done["text"] == HIPAA  # exactly what the model generated
    assert {e["data"]["session_id"] for e in events} == {session["session_id"]}
    user = events[0]["data"]
    assert set(user) == {"session_id", "message_id", "text"}
    assert chat.store.messages(session["session_id"])[1]["status"] == "complete"


def test_tokens_arrive_while_the_model_is_still_generating(client, chat, staff, model):  # noqa: F811
    _record, token = staff
    session = new_session(client, token)["session_id"]
    model.delay = 0.03
    ws, socket, _ = connect(client, session, token)
    try:
        socket.send_json({"event": "user_message", "data": {"session_id": session, "text": "¿Qué es un BAA bajo HIPAA?"}})
        while recv(socket)["event"] != "token_chunk":
            pass
        first_token = time.monotonic()
        recv_until(socket, "generation_completed")
    finally:
        ws.__exit__(None, None, None)
    assert first_token < model.finished_at[0]  # the first token was on screen before the model finished


def test_a_second_message_while_answering_must_be_an_interrupt(client, chat, staff, model):  # noqa: F811
    _record, token = staff
    session = new_session(client, token)["session_id"]
    model.delay = 0.03
    ws, socket, _ = connect(client, session, token)
    try:
        socket.send_json({"event": "user_message", "data": {"session_id": session, "text": "¿Qué dice HIPAA?"}})
        recv_until(socket, "generation_started")
        socket.send_json({"event": "user_message", "data": {"session_id": session, "text": "otra"}})
        error = recv_until(socket, "chat_error")[-1]["data"]
        assert error["code"] == "generation_in_progress"
        recv_until(socket, "generation_completed")
    finally:
        ws.__exit__(None, None, None)


# ================================================================ interrupt (abort, not a HITL pause)


def test_an_interrupt_aborts_the_generation_keeps_the_partial_and_starts_a_new_turn(client, chat, staff, model):  # noqa: F811
    _record, token = staff
    session = new_session(client, token)["session_id"]
    model.delay = 0.04
    ws, socket, _ = connect(client, session, token)
    try:
        socket.send_json({"event": "user_message", "data": {"session_id": session,
                                                            "text": "¿Qué permite HIPAA sobre compartir PHI?"}})
        events = recv_until(socket, "token_chunk")
        events += [recv(socket), recv(socket)]  # a few tokens of the first answer are on screen
        new_input = "espera, mi pregunta era sobre UK GDPR, no HIPAA"
        socket.send_json({"event": "interrupt_requested", "data": {"session_id": session, "new_input": new_input}})
        events = recv_until(socket, "generation_completed", collected=events)
    finally:
        ws.__exit__(None, None, None)

    names = [e["event"] for e in events]
    interrupted_at = names.index("generation_interrupted")
    first = events[interrupted_at]["data"]
    assert first["status"] == "interrupted"
    assert events[names.index("interrupt_requested")]["data"] == {"session_id": session, "new_input": new_input}
    # measurable abort: no token of the original generation after generation_interrupted …
    assert not [e for e in events[interrupted_at:] if e["event"] == "token_chunk"
                and e["data"]["message_id"] == first["message_id"]]
    # … and the model stream itself was closed before the end (not consumed and ignored)
    assert model.closed and model.closed[0] < len(words(HIPAA)) and model.consumed[0] < len(words(HIPAA))
    shown = "".join(e["data"]["token"] for e in events[:interrupted_at]
                    if e["event"] == "token_chunk" and e["data"]["message_id"] == first["message_id"])
    # the new turn reflects new_input
    after = events[interrupted_at + 1:]
    assert after[0]["event"] == "user_message" and after[0]["data"]["text"] == new_input
    second = [e for e in after if e["event"] == "generation_started"][0]["data"]["message_id"]
    assert second != first["message_id"]
    assert after[-1]["data"]["message_id"] == second and after[-1]["data"]["text"] == UK_GDPR
    assert "UK GDPR" in model.calls[-1] and len(model.calls) == 2
    # history: the interrupted message is kept as it was shown, never overwritten
    history = chat.store.messages(session)
    assert [(m["role"], m["status"]) for m in history] == [("user", "complete"), ("assistant", "interrupted"),
                                                           ("user", "complete"), ("assistant", "complete")]
    assert history[1]["message_id"] == first["message_id"] and history[1]["text"] == shown and shown
    assert history[2]["text"] == new_input and history[3]["text"] == UK_GDPR
    assert chat.store.session(session)["status"] == "active"


# ================================================================ reconnect and rehydration


def test_reconnecting_with_the_same_session_id_restores_the_conversation(client, chat, staff, model):  # noqa: F811
    _record, token = staff
    session = new_session(client, token)["session_id"]
    ws, socket, _ = connect(client, session, token)
    try:
        socket.send_json({"event": "user_message", "data": {"session_id": session, "text": "¿Qué dice HIPAA?"}})
        recv_until(socket, "generation_completed")
    finally:
        ws.__exit__(None, None, None)  # the connection drops
    ws, socket, snapshot = connect(client, session, token)  # same session_id
    ws.__exit__(None, None, None)
    assert [(m["role"], m["text"], m["status"]) for m in snapshot["messages"]] == [
        ("user", "¿Qué dice HIPAA?", "complete"), ("assistant", HIPAA, "complete")]  # not an empty chat


def test_reconnecting_in_the_middle_of_an_answer_resumes_it_without_gaps_or_duplicates(client, chat, staff, model):  # noqa: F811
    _record, token = staff
    session = new_session(client, token)["session_id"]
    model.delay = 0.04
    ws, socket, _ = connect(client, session, token)
    socket.send_json({"event": "user_message", "data": {"session_id": session, "text": "¿Qué dice HIPAA?"}})
    recv_until(socket, "token_chunk")
    ws.__exit__(None, None, None)  # drops while the agent keeps answering (the producer does not depend on it)
    time.sleep(0.3)
    ws, socket, snapshot = connect(client, session, token)
    try:
        streaming = snapshot["streaming"]
        assert streaming and streaming["sequence"] > 0
        partial = next(m for m in snapshot["messages"] if m["message_id"] == streaming["message_id"])
        assert partial["status"] == "streaming" and partial["text"] and partial["sequence"] == streaming["sequence"]
        events = recv_until(socket, "generation_completed")
    finally:
        ws.__exit__(None, None, None)
    chunks = [e["data"] for e in events if e["event"] == "token_chunk"]
    assert [c["sequence"] for c in chunks] == list(range(streaming["sequence"] + 1, streaming["sequence"] + 1 + len(chunks)))
    assert partial["text"] + "".join(c["token"] for c in chunks) == HIPAA  # rehydrated + live = the whole answer


# ================================================================ pub/sub: several subscribers, one agent call


def test_a_supervisor_observes_the_same_events_without_a_second_agent_call(client, chat, staff, admin, auth_headers, model):  # noqa: F811
    _record, token = staff
    session = new_session(client, token)["session_id"]
    supervisor = auth_headers(admin)["Authorization"].split(" ", 1)[1]
    ws1, owner, _ = connect(client, session, token)
    ws2, observer, observed = connect(client, session, supervisor)
    try:
        assert observed["role"] == "observer"
        assert chat.bus.subscribers(session) == 2
        owner.send_json({"event": "user_message", "data": {"session_id": session, "text": "¿Qué dice HIPAA?"}})
        seen_by_owner = recv_until(owner, "generation_completed")
        seen_by_supervisor = recv_until(observer, "generation_completed")
        observer.send_json({"event": "user_message", "data": {"session_id": session, "text": "hola"}})
        assert recv(observer)["data"]["code"] == "read_only"
    finally:
        ws1.__exit__(None, None, None)
        ws2.__exit__(None, None, None)
    assert seen_by_owner == seen_by_supervisor  # the same events, in the same order
    assert len(model.calls) == 1  # one producer per session: the agent ran once


# ================================================================ no PHI


def test_no_phi_in_events_messages_or_tokens(client, chat, staff, model):  # noqa: F811
    _record, token = staff
    session = new_session(client, token)["session_id"]
    model.override = HIPAA.replace("Bajo HIPAA,", "Bajo HIPAA, por ejemplo Patient: John Smith was diagnosed with diabetes,")
    ws, socket, _ = connect(client, session, token)
    try:
        socket.send_json({"event": "user_message", "data": {"session_id": session,
                                                            "text": "Paciente: Maria Lopez, MRN 123456, ¿puedo compartir su historia?"}})
        events = recv_until(socket, "generation_completed")
    finally:
        ws.__exit__(None, None, None)
    wire = str(events) + str(chat.store.messages(session))
    for value in ("Maria Lopez", "123456", "John Smith"):
        assert value not in wire
    assert events[0]["data"]["text"].startswith("[mensaje redactado")  # the model never saw the identifiers
    assert all("John" not in e["data"]["token"] for e in events if e["event"] == "token_chunk")


def test_phi_generated_by_the_model_never_reaches_the_screen(client, chat, staff, model):  # noqa: F811
    from services.agent.harness import prompts

    _record, token = staff
    session = new_session(client, token)["session_id"]
    model.override = HIPAA.replace("Una brecha", "Por ejemplo, Patient: John Smith was diagnosed with diabetes. Una brecha")
    ws, socket, _ = connect(client, session, token)
    try:
        socket.send_json({"event": "user_message", "data": {"session_id": session, "text": "¿Qué permite HIPAA?"}})
        events = recv_until(socket, "generation_completed")
    finally:
        ws.__exit__(None, None, None)
    streamed = "".join(e["data"]["token"] for e in events if e["event"] == "token_chunk")
    assert streamed and "John" not in streamed and "Smith" not in streamed  # held back, then never released
    assert events[-1]["data"]["text"] == prompts.OUTPUT_FALLBACK  # the agent's output guard replaced the answer
    assert "John" not in str(chat.store.messages(session))


def test_the_stream_guard_holds_back_text_until_it_is_checked():
    guard = StreamGuard(holdback=20)
    released = "".join(guard.feed(t) for t in words("El personal sigue el mínimo necesario según HIPAA en EE. UU. siempre."))
    assert released and len(guard.text) - len(released) == 20  # the newest text waits for the check
    assert released + guard.flush() == guard.text
    blocked = StreamGuard(holdback=20)
    out = "".join(blocked.feed(t) for t in words("Texto previo largo para liberar algo. Patient: John Smith was diagnosed with cancer."))
    assert "John" not in out and blocked.blocked and blocked.flush() == ""


def test_one_channel_per_session():
    assert channel("chat_0123456789ab") == "chat.chat_0123456789ab"
