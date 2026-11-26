"""In-memory pub/sub of the chat (BRIEFING §5): one channel per session, `chat.<session_id>`.

The producer (the thread running the agent turn) publishes named events to the channel and does
not know who is listening; every WebSocket connected to the session (the staff member in several
tabs, a supervisor observing) is a consumer with its own bounded queue. So N viewers never mean N
agent calls, and a slow or dead connection never blocks the producer: when its queue is full it is
disconnected and, on reconnect, rehydrates from the session snapshot.

Single process: enough for this delivery (BRIEFING §5). With several API processes the same
interface would sit on Redis pub/sub or Postgres LISTEN/NOTIFY.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field

QUEUE_SIZE = 1000


def channel(session_id: str) -> str:
    return f"chat.{session_id}"


@dataclass(eq=False)
class Subscription:
    channel: str
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=QUEUE_SIZE))
    overflow: bool = False

    def _put(self, event: dict) -> None:
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.overflow = True


class ChatBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[Subscription]] = {}
        self._lock = threading.Lock()
        self.published: dict[str, int] = {}  # events published per channel (observability, tests)

    def subscribe(self, session_id: str) -> Subscription:
        subscription = Subscription(channel(session_id), asyncio.get_running_loop())
        with self._lock:
            self._subscribers.setdefault(subscription.channel, set()).add(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        with self._lock:
            subscribers = self._subscribers.get(subscription.channel)
            if subscribers:
                subscribers.discard(subscription)
                if not subscribers:
                    del self._subscribers[subscription.channel]

    def subscribers(self, session_id: str) -> int:
        with self._lock:
            return len(self._subscribers.get(channel(session_id), ()))

    def publish(self, session_id: str, event: str, data: dict) -> None:
        """Thread-safe: called from the producer thread; delivered on each consumer's event loop."""
        message = {"event": event, "data": data}
        name = channel(session_id)
        with self._lock:
            subscribers = list(self._subscribers.get(name, ()))
            self.published[name] = self.published.get(name, 0) + 1
        for subscription in subscribers:
            if not subscription.loop.is_closed():
                subscription.loop.call_soon_threadsafe(subscription._put, message)
