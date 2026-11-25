"""Turns of the Compliance assistant over WebSocket: the producer side of the chat.

A turn runs the SAME agent (services.agent.runner.AgentRuntime.stream_run: same graph, nodes,
tools, guards and memory) in a worker thread, with conversation_id = session_id. Every token the
generation model produces goes through the streaming output guard and is published on the
session's channel (bus.py) as `token_chunk`; consumers (WebSockets) never call the agent.

Interrupt (abort, not a LangGraph HITL pause): `interrupt_requested` sets the turn's cancel flag.
From that instant no `token_chunk` of that generation is published; the model stream is closed by
the node that is generating (data/pipelines/rag.py) and no further node runs. The assistant
message keeps exactly the text that was shown, with status `interrupted`; `new_input` becomes a
new user message and a NEW assistant turn (a new message_id, a new agent run).

Events (BRIEFING §4), always {"event", "data"} with session_id and no PHI:
    user_message, generation_started, token_chunk, interrupt_requested, generation_interrupted,
    generation_completed, generation_failed, session_snapshot
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from data.pipelines.rag import GenerationCancelled

from ..harness import output_guard
from .bus import ChatBus
from .store import ChatStore

logger = logging.getLogger("api.agent.chat")

MAX_INPUT_CHARS = 1000
HOLDBACK_CHARS = 120  # longer than any PHI / secret pattern of the output guard
JOIN_TIMEOUT_SECONDS = 10


class ChatError(ValueError):
    """A client request that cannot be served (the WebSocket answers with chat_error)."""

    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


class StreamGuard:
    """The agent's output guard, applied while streaming. Text is released only once it is
    HOLDBACK_CHARS behind the newest token and the whole text so far passes the guard; when a
    finding appears nothing more is released (the final answer then comes from guard_output)."""

    def __init__(self, holdback: int = HOLDBACK_CHARS):
        self.holdback = holdback
        self.text = ""
        self.released = 0
        self.blocked = False

    def _unsafe(self) -> bool:
        findings = output_guard.check_output(self.text).findings
        return any(category not in ("empty_answer", "answer_too_long") for _type, category in findings)

    def feed(self, token: str) -> str:
        if self.blocked:
            return ""
        self.text += token
        if self._unsafe():
            self.blocked = True
            return ""
        until = len(self.text) - self.holdback
        if until <= self.released:
            return ""
        out, self.released = self.text[self.released:until], until
        return out

    def flush(self) -> str:
        if self.blocked or self._unsafe():
            self.blocked = True
            return ""
        out, self.released = self.text[self.released:], len(self.text)
        return out

    @property
    def shown(self) -> str:
        return self.text[:self.released]


@dataclass
class Generation:
    session_id: str
    message_id: str
    run_id: str
    cancel: threading.Event = field(default_factory=threading.Event)
    guard: StreamGuard = field(default_factory=StreamGuard)
    sequence: int = 0
    shown: str = ""
    finished: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)
    thread: threading.Thread | None = None


class ChatManager:
    def __init__(self, store: ChatStore, bus: ChatBus, runtime_factory: Callable[[], Any]):
        self.store = store
        self.bus = bus
        self.runtime_factory = runtime_factory
        self._active: dict[str, Generation] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ snapshot (rehydration)

    def snapshot(self, session_id: str) -> dict[str, Any]:
        """History + the message being generated, with the text already shown and its last
        sequence: the client restores the thread before accepting new tokens."""
        session = self.store.session(session_id)
        with self._lock:
            generation = self._active.get(session_id)
        if generation:
            with generation.lock:  # consistent with the tokens published so far
                messages = self.store.messages(session_id)
                for m in messages:
                    if m["message_id"] == generation.message_id and not generation.finished:
                        m["text"], m["sequence"] = generation.shown, generation.sequence
                streaming = None if generation.finished else {"message_id": generation.message_id,
                                                              "sequence": generation.sequence}
        else:
            messages, streaming = self.store.messages(session_id), None
        return {"session_id": session_id, "status": session["status"], "agent_id": session["agent_id"],
                "messages": messages, "streaming": streaming}

    def generating(self, session_id: str) -> Generation | None:
        with self._lock:
            generation = self._active.get(session_id)
        return generation if generation and not generation.finished else None

    # ------------------------------------------------------------ client requests

    def _clean(self, text: Any) -> str:
        if not isinstance(text, str) or not text.strip():
            raise ChatError("invalid_input", "El mensaje está vacío.")
        if len(text) > MAX_INPUT_CHARS:
            raise ChatError("invalid_input", f"El mensaje supera los {MAX_INPUT_CHARS} caracteres.")
        safe, _phi = self.runtime_factory().screen(text.strip())  # the agent's patient-identifier screen
        return safe

    def user_message(self, session_id: str, user_id: str, actor: str, text: Any) -> str:
        session = self.store.session(session_id)
        if session is None or session["status"] == "closed":
            raise ChatError("session_closed", "La sesión está cerrada.")
        if self.generating(session_id):
            raise ChatError("generation_in_progress", "El asistente está respondiendo: usa interrupt_requested "
                                                      "para interrumpirlo y redirigirlo.")
        return self._start_turn(session_id, actor, self._clean(text))

    def interrupt(self, session_id: str, actor: str, new_input: Any) -> str | None:
        """Abort the generation in progress, keep its partial message as `interrupted`, then start
        a new turn with new_input. Returns the new assistant message_id."""
        safe = self._clean(new_input)
        generation = self.generating(session_id)
        if generation is None:  # nothing to interrupt: it is simply the next message
            return self._start_turn(session_id, actor, safe)
        self.bus.publish(session_id, "interrupt_requested", {"session_id": session_id, "new_input": safe})
        self._abort(generation)
        if generation.thread is not None:  # the producer stops at its next token / node
            generation.thread.join(JOIN_TIMEOUT_SECONDS)
        return self._start_turn(session_id, actor, safe)

    def _abort(self, generation: Generation) -> None:
        with generation.lock:
            if generation.finished:
                return
            generation.cancel.set()  # from here on, no token_chunk of this generation is published
            generation.finished = True
            shown = generation.shown
            self.store.finish_message(generation.message_id, shown, "interrupted")
            self.store.set_status(generation.session_id, "interrupted")
            self.bus.publish(generation.session_id, "generation_interrupted",
                             {"session_id": generation.session_id, "message_id": generation.message_id,
                              "status": "interrupted", "sequence": generation.sequence})
        logger.info("chat interrupted session=%s message=%s tokens_shown=%s", generation.session_id,
                    generation.message_id, generation.sequence)

    # ------------------------------------------------------------ producer

    def _start_turn(self, session_id: str, actor: str, text: str) -> str:
        user = self.store.add_message(session_id, "user", text, "complete")
        self.bus.publish(session_id, "user_message", {"session_id": session_id, "message_id": user["message_id"],
                                                      "text": text})
        run_id = str(uuid.uuid4())
        assistant = self.store.add_message(session_id, "assistant", "", "streaming", run_id=run_id)
        generation = Generation(session_id, assistant["message_id"], run_id)
        with self._lock:
            self._active[session_id] = generation
        self.store.set_status(session_id, "active")
        self.bus.publish(session_id, "generation_started", {"session_id": session_id,
                                                            "message_id": generation.message_id})
        generation.thread = threading.Thread(target=self._produce, args=(generation, actor, text),
                                             name=f"chat-{session_id}", daemon=True)
        generation.thread.start()
        return generation.message_id

    def _emit(self, generation: Generation, text: str) -> None:
        if not text:
            return
        with generation.lock:
            if generation.finished or generation.cancel.is_set():
                return
            generation.sequence += 1
            generation.shown += text
            self.bus.publish(generation.session_id, "token_chunk",
                             {"session_id": generation.session_id, "message_id": generation.message_id,
                              "token": text, "sequence": generation.sequence})

    def _produce(self, generation: Generation, actor: str, text: str) -> None:
        runtime = self.runtime_factory()
        try:
            result = runtime.stream_run(text, generation.run_id, conversation_id=generation.session_id, actor=actor,
                                        on_token=lambda token: self._emit(generation, generation.guard.feed(token)),
                                        cancel=generation.cancel)
        except GenerationCancelled:
            return  # _abort already recorded and published the interruption
        except Exception as error:  # AgentRejected / AgentRunError: an honest message, never internals
            self._fail(generation, error)
            return
        self._emit(generation, generation.guard.flush())
        with generation.lock:
            if generation.finished:
                return
            generation.finished = True
            final = result.answer
            self.store.finish_message(generation.message_id, final, "complete")
            self.bus.publish(generation.session_id, "generation_completed",
                             {"session_id": generation.session_id, "message_id": generation.message_id,
                              "status": "complete", "text": final, "sequence": generation.sequence})
        logger.info("chat completed session=%s message=%s tokens=%s", generation.session_id, generation.message_id,
                    generation.sequence)

    def _fail(self, generation: Generation, error: Exception) -> None:
        from ..runner import AgentRejected

        detail = (str(error) if isinstance(error, AgentRejected)
                  else "El asistente no pudo responder en este momento. Inténtalo de nuevo en unos minutos.")
        with generation.lock:
            if generation.finished:
                return
            generation.finished = True
            self.store.finish_message(generation.message_id, generation.shown or detail, "failed")
            self.bus.publish(generation.session_id, "generation_failed",
                             {"session_id": generation.session_id, "message_id": generation.message_id,
                              "status": "failed", "detail": detail})
        logger.warning("chat failed session=%s message=%s error=%s", generation.session_id, generation.message_id,
                       type(error).__name__)

    def close_session(self, session_id: str) -> None:
        generation = self.generating(session_id)
        if generation:
            self._abort(generation)
        self.store.set_status(session_id, "closed")
        self.bus.publish(session_id, "session_closed", {"session_id": session_id, "status": "closed"})
