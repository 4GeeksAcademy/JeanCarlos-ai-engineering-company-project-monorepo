"""Rate limiting of the endpoints that call a language model (NIST Protect · cost and abuse control).

Every request that ends in model calls goes through `ModelRateLimiter.check(scope, user_id)`:

| scope       | entry points                                                         | default            |
| ----------- | -------------------------------------------------------------------- | ------------------ |
| knowledge   | POST /knowledge/query (RAG: 1 embedding + 1 generation)               | 10 / min per user  |
| agent       | POST /agent/query, WebSocket chat user_message / interrupt_requested  | 10 / min per user  |
| rfp         | POST /api/rfp/tickets (≈10 calls per PDF), POST …/generate, a decision that sends a section back to its generator | 5 / 10 min per user |

On top of the per-user window, each scope has a GLOBAL ceiling per minute (all users together), so
a loop in a client, a script or a compromised account cannot run up the LLM bill: the request is
refused with 429 + Retry-After before any model is called, and the refusal is logged as a security
event (`api.security`, user id only).

Sliding windows in memory, one limiter per API process (app.state). Limits are environment
settings (MODEL_RATE_<SCOPE>_PER_USER, MODEL_RATE_<SCOPE>_WINDOW_SECONDS, MODEL_RATE_<SCOPE>_GLOBAL_PER_MINUTE).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass

security_logger = logging.getLogger("api.security")


@dataclass(frozen=True)
class Limit:
    per_user: int
    window_seconds: int
    global_per_minute: int


DEFAULTS = {
    "knowledge": Limit(per_user=10, window_seconds=60, global_per_minute=60),
    "agent": Limit(per_user=10, window_seconds=60, global_per_minute=60),
    "rfp": Limit(per_user=5, window_seconds=600, global_per_minute=20),
}


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


def limit_for(scope: str) -> Limit:
    base = DEFAULTS[scope]
    prefix = f"MODEL_RATE_{scope.upper()}_"
    return Limit(_env_int(prefix + "PER_USER", base.per_user), _env_int(prefix + "WINDOW_SECONDS", base.window_seconds),
                 _env_int(prefix + "GLOBAL_PER_MINUTE", base.global_per_minute))


class RateLimited(Exception):
    def __init__(self, scope: str, retry_after: int, level: str):
        super().__init__(f"{scope} rate limit ({level})")
        self.scope, self.retry_after, self.level = scope, retry_after, level


class ModelRateLimiter:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self.refused = 0  # observability (and tests)

    def _window(self, key: str, seconds: int, now: float) -> deque[float]:
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= seconds:
            hits.popleft()
        return hits

    def check(self, scope: str, user_id: str) -> None:
        """Count one model-backed request or raise RateLimited (nothing is counted when refused)."""
        limit = limit_for(scope)
        now = self.clock()
        with self._lock:
            mine = self._window(f"{scope}:user:{user_id}", limit.window_seconds, now)
            everyone = self._window(f"{scope}:global", 60, now)
            if len(mine) >= limit.per_user:
                retry, level = int(mine[0] + limit.window_seconds - now) + 1, "user"
            elif len(everyone) >= limit.global_per_minute:
                retry, level = int(everyone[0] + 60 - now) + 1, "global"
            else:
                mine.append(now)
                everyone.append(now)
                return
            self.refused += 1
        security_logger.warning("model rate limit scope=%s level=%s user=%s retry_after=%ss", scope, level, user_id,
                                max(1, retry))
        raise RateLimited(scope, max(1, retry), level)


def limiter_of(app) -> ModelRateLimiter:
    limiter = getattr(app.state, "model_rate_limiter", None)
    if limiter is None:  # app started without the lifespan (some tests)
        limiter = app.state.model_rate_limiter = ModelRateLimiter()
    return limiter


def enforce(request, scope: str, user_id: str) -> None:
    """For HTTP routes: 429 + Retry-After when the limit is reached."""
    from fastapi import HTTPException

    try:
        limiter_of(request.app).check(scope, str(user_id))
    except RateLimited as limited:
        raise HTTPException(status_code=429, detail="Demasiadas solicitudes al asistente. Espera un momento.",
                            headers={"Retry-After": str(limited.retry_after)}) from limited
