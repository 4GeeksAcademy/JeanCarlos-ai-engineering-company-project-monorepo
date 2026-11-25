"""Server-Sent Events of the RFP flow (Sistemas en Tiempo Real, Parte 1) — the stream that tells
Revenue Cycle's dashboard that a new RFP ticket has arrived, without a reload.

One `EventHub` per API process (per event loop and database): it is the ONLY reader of the
`rfp_event` table for every open connection of that process, and fans each new event out to the
per-connection queues. So 50 open dashboards cost 50 idle asyncio tasks + one query per wake-up,
not 50 queries. The hub wakes up immediately when the intake of this process publishes
(events.add_listener) and polls once per POLL_SECONDS for events published by other processes
(another API worker, the CLI).

Each connection:
1. subscribes to the hub (live events are queued from this moment), then
2. replays from the database every event after its Last-Event-ID (or starts at the newest event
   when it has none: the dashboard has just read the ticket list), then
3. streams the live events, skipping any id it already sent (the overlap of 1 and 2), with a
   comment frame every HEARTBEAT_SECONDS so proxies and the client watchdog see the connection alive.

A slow client whose queue fills up is disconnected instead of buffering without limit; it comes
back with its Last-Event-ID and gets the replay.

Shutdown: uvicorn's graceful shutdown waits for every open response to finish, and an SSE stream
never finishes on its own — a restart or a deploy would hang and the dashboards would keep
listening to the old process. `install_shutdown_hook` wraps the process's SIGTERM / SIGINT handler
(uvicorn's): every stream ends at once, the process exits, and the clients reconnect (backoff +
Last-Event-ID) to the new one without losing anything. When the gap is larger than REPLAY_LIMIT (or its
Last-Event-ID is unknown), the server sends `resync_required` and the client re-reads the list once.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator

from sqlalchemy.engine import Engine

from data.pipelines.rfp_intake import events

logger = logging.getLogger("api.rfp_events")

QUEUE_SIZE = 100
REPLAY_LIMIT = 100


def _seconds(name: str, default: float) -> float:
    try:
        return max(0.05, float(os.environ.get(name, default)))
    except ValueError:
        return default


def heartbeat_seconds() -> float:
    return _seconds("RFP_SSE_HEARTBEAT_SECONDS", 15)


def poll_seconds() -> float:
    return _seconds("RFP_SSE_POLL_SECONDS", 2)


def frame(event: str, data: dict, event_id: int | None = None) -> str:
    """One SSE frame: a named event, its id (the resume cursor) and a single-line JSON body."""
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append("data: " + json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    return "\n".join(lines) + "\n\n"


KEEP_ALIVE = ": keep-alive\n\n"


@dataclass(eq=False)
class Subscriber:
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=QUEUE_SIZE))
    overflow: bool = False


_shutting_down = threading.Event()
_CLOSE = object()  # queue sentinel: the server is shutting down


class EventHub:
    def __init__(self, engine: Engine, loop: asyncio.AbstractEventLoop):
        self.engine = engine
        self.loop = loop
        self.subscribers: set[Subscriber] = set()
        self.wake = asyncio.Event()
        self.last_id: int | None = None
        self.task: asyncio.Task | None = None
        self._remove_listener = events.add_listener(self._wake_from_any_thread)

    def _wake_from_any_thread(self) -> None:
        if self.loop.is_closed():
            self._remove_listener()
            return
        self.loop.call_soon_threadsafe(self.wake.set)

    async def subscribe(self) -> Subscriber:
        if self.last_id is None:
            self.last_id = await asyncio.to_thread(events.latest_id, self.engine)
        subscriber = Subscriber()
        self.subscribers.add(subscriber)
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._run())
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        self.subscribers.discard(subscriber)

    def close_all(self) -> None:
        for subscriber in list(self.subscribers):
            try:
                subscriber.queue.put_nowait(_CLOSE)
            except asyncio.QueueFull:
                subscriber.overflow = True

    async def _run(self) -> None:
        while self.subscribers:
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=poll_seconds())
            except TimeoutError:
                pass
            self.wake.clear()
            try:
                rows = await asyncio.to_thread(events.events_after, self.engine, self.last_id or 0)
            except Exception as error:  # database hiccup: keep the connections, retry on the next tick
                logger.warning("rfp_events hub read failed: %s", type(error).__name__)
                continue
            for row in rows:
                self.last_id = row["id"]
                for subscriber in list(self.subscribers):
                    try:
                        subscriber.queue.put_nowait(row)
                    except asyncio.QueueFull:
                        subscriber.overflow = True  # the stream closes; the client replays with Last-Event-ID
        self.task = None


_hubs: dict[tuple[int, int], EventHub] = {}


def shutdown_streams() -> None:
    """End every open stream of this process (called from any thread)."""
    _shutting_down.set()
    for hub in list(_hubs.values()):
        if not hub.loop.is_closed():
            hub.loop.call_soon_threadsafe(hub.close_all)


def install_shutdown_hook() -> None:
    """Called at app startup. uvicorn registers its SIGTERM/SIGINT handler (a bound
    Server.handle_exit) BEFORE it loads the app, so the hook wraps the handler that is installed
    now: it ends the streams, then hands over to uvicorn's own graceful shutdown."""
    _shutting_down.clear()  # a (re)started app accepts streams again
    if threading.current_thread() is not threading.main_thread():
        return  # signal handlers live in the main thread (e.g. a test server in a thread)
    for sig in (signal.SIGINT, signal.SIGTERM):
        previous = signal.getsignal(sig)
        if getattr(previous, "_rfp_sse_hook", False):
            continue

        def handler(signum, frame, previous=previous):
            shutdown_streams()
            if callable(previous):
                previous(signum, frame)
            elif previous == signal.SIG_DFL and signum == signal.SIGINT:
                raise KeyboardInterrupt

        handler._rfp_sse_hook = True
        signal.signal(sig, handler)


def accepting() -> bool:
    return not _shutting_down.is_set()


def hub_for(engine: Engine) -> EventHub:
    loop = asyncio.get_running_loop()
    key = (id(loop), id(engine))
    hub = _hubs.get(key)
    if hub is None or hub.loop is not loop:
        hub = _hubs[key] = EventHub(engine, loop)
    return hub


def parse_last_event_id(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = int(value.strip())
    except ValueError:
        return -1  # unknown cursor → resync
    return parsed if parsed >= 0 else -1


async def stream(engine: Engine, last_event_id: int | None, expires_at: datetime | None, user_id: str) -> AsyncIterator[str]:
    hub = hub_for(engine)
    subscriber = await hub.subscribe()
    logger.info("rfp_events open user=%s last_event_id=%s connections=%s", user_id, last_event_id, len(hub.subscribers))
    try:
        latest = await asyncio.to_thread(events.latest_id, engine)
        replay: list[dict] = []
        resync = None
        if last_event_id is None:
            cursor = latest  # a fresh dashboard has just read the ticket list: only what comes next
        elif last_event_id < 0 or last_event_id > latest:
            cursor, resync = latest, "unknown_last_event_id"
        else:
            replay = await asyncio.to_thread(events.events_after, engine, last_event_id, REPLAY_LIMIT + 1)
            if len(replay) > REPLAY_LIMIT:
                cursor, replay, resync = latest, [], "gap_too_large"
            else:
                cursor = last_event_id
        yield "retry: 3000\n\n"
        yield frame("stream_ready", {"last_event_id": cursor, "replayed": len(replay),
                                     "heartbeat_seconds": heartbeat_seconds()})
        if resync:
            yield frame("resync_required", {"reason": resync, "last_event_id": latest})
            cursor = latest
        for row in replay:
            yield frame(row["event"], row["data"], row["id"])
            cursor = row["id"]
        while True:
            if _shutting_down.is_set():
                logger.info("rfp_events closed (server shutting down) user=%s", user_id)
                return
            if subscriber.overflow:
                logger.info("rfp_events closed (slow client) user=%s", user_id)
                return
            timeout = heartbeat_seconds()
            if expires_at is not None:
                remaining = (expires_at - datetime.now(timezone.utc)).total_seconds()
                if remaining <= 0:  # the JWT expired: the client must come back with a valid session
                    yield frame("session_expired", {"reason": "token_expired"})
                    return
                timeout = min(timeout, remaining)
            try:
                row = await asyncio.wait_for(subscriber.queue.get(), timeout=timeout)
            except TimeoutError:
                yield KEEP_ALIVE
                continue
            if row is _CLOSE:
                continue  # the loop head ends the stream
            if row["id"] <= cursor:  # already sent by the replay
                continue
            yield frame(row["event"], row["data"], row["id"])
            cursor = row["id"]
    finally:
        hub.unsubscribe(subscriber)
        logger.info("rfp_events closed user=%s connections=%s", user_id, len(hub.subscribers))
