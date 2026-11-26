"""WebSocket chat of the Compliance assistant (Sistemas en Tiempo Real, Parte 2) — the same API, under
the agent's routes. POST /agent/query keeps working unchanged.

- POST /agent/chat/sessions                 new ChatSession of the authenticated staff member
- GET  /agent/chat/sessions                 their sessions (to come back to a conversation)
- GET  /agent/chat/sessions/{session_id}    snapshot: history + message in progress
- POST /agent/chat/sessions/{session_id}/close
- WS   /agent/chat/ws?session_id=chat_…     one persistent connection per session

WebSocket handshake. The socket is bound to an existing session by `session_id` in the URL
(session_id is also the agent's conversation_id). Authentication uses the SAME backoffice JWT:
either `?token=` (rejected at the handshake when invalid) or — what the backoffice uses, so the
token never lands in URLs or access logs — a first frame `{"event": "auth", "data": {"token": …}}`
within AUTH_TIMEOUT seconds. Nothing of the chat is sent before the user is authenticated and
allowed on the session (its owner; an admin may join as a read-only observer).

Then: `session_snapshot` (rehydration from the stored history + the text already shown of a
message in progress), and the live events of the session's channel. Client → server:
`user_message`, `interrupt_requested`. Errors of a request: `chat_error` {code, detail}.

Close codes: 4400 bad request, 4401 not authenticated, 4403 not allowed, 4404 unknown session,
4408 the client fell too far behind (reconnect: the snapshot rehydrates it).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import threading
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, WebSocket, WebSocketDisconnect, status

from services.api.core.dependencies import AuthenticatedUser, get_current_user
from services.api.core.rate_limit import RateLimited, limiter_of

from .bus import ChatBus
from .manager import ChatError, ChatManager
from .store import ChatStore

logger = logging.getLogger("api.agent.chat")
router = APIRouter(prefix="/chat", tags=["agent-chat"])

SESSION_ID = re.compile(r"^chat_[0-9a-f]{12}$")
SessionId = Annotated[str, Path(pattern=SESSION_ID.pattern)]
AUTH_TIMEOUT = 5.0


class ChatService:
    def __init__(self, store: ChatStore, bus: ChatBus, manager: ChatManager):
        self.store, self.bus, self.manager = store, bus, manager


_service: ChatService | None = None
_service_lock = threading.Lock()


def get_chat() -> ChatService:
    global _service
    with _service_lock:
        if _service is None:
            from ..runner import get_runtime, state_database

            store, bus = ChatStore(state_database()), ChatBus()
            _service = ChatService(store, bus, ChatManager(store, bus, get_runtime))
        return _service


def set_chat(service: ChatService | None) -> None:
    """Tests: a service with its own database and agent runtime."""
    global _service
    with _service_lock:
        _service = service


def _allowed(session: dict[str, Any] | None, user) -> str | None:
    if session is None:
        return None
    if session["user_id"] == user.id:
        return "participant"
    return "observer" if getattr(user.role, "value", user.role) == "admin" else None


# ---------------------------------------------------------------- REST


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def create_session(user: AuthenticatedUser) -> dict[str, Any]:
    return get_chat().store.create_session(user.id)


@router.get("/sessions")
def list_sessions(user: AuthenticatedUser) -> list[dict[str, Any]]:
    return get_chat().store.sessions_of(user.id)


@router.get("/sessions/{session_id}")
def session_snapshot(session_id: SessionId, user: AuthenticatedUser) -> dict[str, Any]:
    chat = get_chat()
    if not _allowed(chat.store.session(session_id), user):
        raise HTTPException(status_code=404, detail="Sesión desconocida")
    return chat.manager.snapshot(session_id)


@router.post("/sessions/{session_id}/close")
def close_session(session_id: SessionId, user: AuthenticatedUser) -> dict[str, Any]:
    chat = get_chat()
    if _allowed(chat.store.session(session_id), user) != "participant":
        raise HTTPException(status_code=404, detail="Sesión desconocida")
    chat.manager.close_session(session_id)
    return chat.store.session(session_id)


# ---------------------------------------------------------------- WebSocket


def _authenticate(websocket: WebSocket, token: Any):
    if not isinstance(token, str) or not token:
        return None
    try:
        with websocket.app.state.database_lock:
            return get_current_user(token, websocket.app.state.database)
    except HTTPException:
        return None


async def _close(websocket: WebSocket, code: int, reason: str) -> None:
    with contextlib.suppress(Exception):
        await websocket.close(code=code, reason=reason)


@router.websocket("/ws")
async def chat_socket(websocket: WebSocket, session_id: str | None = None, token: str | None = None) -> None:
    if not session_id or not SESSION_ID.match(session_id):
        await _close(websocket, 4400, "session_id required")  # before accept: the handshake is refused
        return
    if token is not None:
        user = _authenticate(websocket, token)
        if user is None:
            await _close(websocket, 4401, "invalid token")  # refused at the handshake, no event sent
            return
        await websocket.accept()
    else:
        await websocket.accept()
        try:
            frame = await asyncio.wait_for(websocket.receive_json(), AUTH_TIMEOUT)
        except (TimeoutError, WebSocketDisconnect, ValueError, KeyError):
            await _close(websocket, 4401, "authentication required")
            return
        is_auth = isinstance(frame, dict) and frame.get("event") == "auth" and isinstance(frame.get("data"), dict)
        user = _authenticate(websocket, frame["data"].get("token")) if is_auth else None
        if user is None:
            await _close(websocket, 4401, "authentication required")
            return

    chat = get_chat()
    session = await asyncio.to_thread(chat.store.session, session_id)
    if session is None:
        await _close(websocket, 4404, "unknown session")
        return
    role = _allowed(session, user)
    if role is None:
        await _close(websocket, 4403, "not allowed")
        return

    subscription = chat.bus.subscribe(session_id)  # first subscribe, then snapshot: nothing falls in between
    logger.info("chat open session=%s role=%s subscribers=%s", session_id, role, chat.bus.subscribers(session_id))
    try:
        snapshot = await asyncio.to_thread(chat.manager.snapshot, session_id)
        await websocket.send_json({"event": "session_snapshot", "data": {**snapshot, "role": role}})
        shown = {snapshot["streaming"]["message_id"]: snapshot["streaming"]["sequence"]} if snapshot["streaming"] else {}

        async def forward() -> None:
            while True:
                event = await subscription.queue.get()
                if subscription.overflow:
                    await _close(websocket, 4408, "client too slow: reconnect")
                    return
                data = event["data"]
                if event["event"] == "token_chunk" and data["sequence"] <= shown.get(data["message_id"], 0):
                    continue  # already in the snapshot
                await websocket.send_json(event)

        async def receive() -> None:
            while True:
                frame = await websocket.receive_json()
                name = frame.get("event") if isinstance(frame, dict) else None
                data = frame.get("data") if isinstance(frame, dict) and isinstance(frame.get("data"), dict) else {}
                try:
                    if name == "auth":
                        continue  # already authenticated
                    if role != "participant":
                        raise ChatError("read_only", "Observas esta sesión en modo lectura.")
                    if data.get("session_id", session_id) != session_id:
                        raise ChatError("wrong_session", "El mensaje es de otra sesión.")
                    if name in ("user_message", "interrupt_requested"):  # each one starts a model-backed turn
                        try:
                            limiter_of(websocket.app).check("agent", str(user.id))
                        except RateLimited as limited:
                            raise ChatError("rate_limited", f"Demasiados mensajes seguidos: espera {limited.retry_after} s.")
                    if name == "user_message":
                        await asyncio.to_thread(chat.manager.user_message, session_id, user.id, user.username,
                                                data.get("text"))
                    elif name == "interrupt_requested":
                        await asyncio.to_thread(chat.manager.interrupt, session_id, user.username, data.get("new_input"))
                    else:
                        raise ChatError("unknown_event", "Evento desconocido.")
                except ChatError as error:
                    await websocket.send_json({"event": "chat_error", "data": {"session_id": session_id,
                                                                                "code": error.code,
                                                                                "detail": str(error)}})

        tasks = [asyncio.create_task(forward()), asyncio.create_task(receive())]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            error = task.exception()
            if error and not isinstance(error, (WebSocketDisconnect, RuntimeError, ValueError)):
                logger.warning("chat socket error session=%s error=%s", session_id, type(error).__name__)
    except WebSocketDisconnect:
        pass
    finally:
        chat.bus.unsubscribe(subscription)
        logger.info("chat closed session=%s role=%s", session_id, role)
