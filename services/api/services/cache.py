from copy import deepcopy
from threading import RLock
from time import monotonic
from typing import Hashable


class TTLCache:
    def __init__(self, max_entries: int = 1024) -> None:
        self._entries: dict[tuple[str, Hashable], tuple[float, int, object]] = {}
        self._versions: dict[str, int] = {}
        self._lock = RLock()
        self._max_entries = max_entries

    def get(
        self, namespace: str, key: Hashable
    ) -> tuple[bool, object | None, int]:
        now = monotonic()
        with self._lock:
            self._remove_expired(now)
            version = self._versions.get(namespace, 0)
            entry = self._entries.get((namespace, key))
            if entry is None or entry[1] != version:
                return False, None, version
            expires_at, _, value = entry
            if expires_at <= now:
                self._entries.pop((namespace, key), None)
                return False, None, version
            return True, deepcopy(value), version

    def set(
        self,
        namespace: str,
        key: Hashable,
        value: object,
        ttl_seconds: float,
        expected_version: int,
    ) -> None:
        now = monotonic()
        with self._lock:
            self._remove_expired(now)
            if self._versions.get(namespace, 0) != expected_version:
                return
            if len(self._entries) >= self._max_entries:
                self._entries.pop(next(iter(self._entries)))
            self._entries[(namespace, key)] = (
                now + ttl_seconds,
                expected_version,
                deepcopy(value),
            )

    def invalidate(self, namespace: str) -> None:
        with self._lock:
            self._versions[namespace] = self._versions.get(namespace, 0) + 1
            for entry_key in tuple(self._entries):
                if entry_key[0] == namespace:
                    del self._entries[entry_key]

    def _remove_expired(self, now: float) -> None:
        for key, (expires_at, _, _) in tuple(self._entries.items()):
            if expires_at <= now:
                del self._entries[key]


response_cache = TTLCache()
