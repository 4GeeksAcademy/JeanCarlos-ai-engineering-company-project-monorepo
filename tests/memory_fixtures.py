"""Shared helpers to run the agent graph in tests without the real memory or model calls."""

import hashlib
import math
import re
import unicodedata

from services.agent.memory import llm as memory_llm
from services.agent.memory import store as memory_store


def fake_embed(text: str) -> list[float]:
    """Bag of words hashed into 64 dimensions: texts sharing words are similar, deterministic."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))
    vector = [0.0] * 64
    for word in re.findall(r"[a-z0-9]{4,}", plain):
        vector[int(hashlib.sha256(word.encode()).hexdigest(), 16) % 64] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


def isolate_memory(monkeypatch, tmp_path, *, evaluation=None, decision=None):
    """Temporary memory store; self-evaluation and decision classifier stubbed (no proposals,
    unclear decisions) unless the test passes its own."""
    store = memory_store.MemoryStore(tmp_path / "agent_memory.sqlite", embed=fake_embed)
    monkeypatch.setattr(memory_store, "_store", store)
    monkeypatch.setattr(memory_llm, "self_evaluate",
                        evaluation or (lambda message, answer, memories: {"outcome": "none", "reason": "stub"}))
    monkeypatch.setattr(memory_llm, "classify_decision", decision or (lambda proposal, message: {
        "label": "ambiguous", "confidence": 0.0, "classifier": "stub", "edited_content": None, "remaining_question": None}))
    return store
