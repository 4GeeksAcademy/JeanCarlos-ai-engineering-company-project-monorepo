"""Persistent agent memory: explicit read/write interface, proposals and an audit log.

A store of its own (SQLite file AGENT_STATE_DIR/agent_memory.sqlite), never the RAG collection
`healthcore_knowledge`: the knowledge base stays read-only curated documents; this store holds
approved operational facts learned from conversations.

Tables:
- agent_memories: approved facts (kind, scope, content, embedding, who approved it, from which
  proposal, TTL and usage). The only table the agent reads to recall.
- memory_proposals: every proposal the agent made and how it ended (pending → approved /
  approved_edited / rejected / discarded_ambiguous / discarded_topic_change / expired /
  blocked_phi), with the decision label, confidence, classifier and decision message.
- memory_events: append-only audit trail of everything above plus consolidation actions.
  Nothing is ever deleted from it.

Invariants enforced here (not only in the prompt):
- one pending proposal per conversation (partial unique index);
- no write without an approved proposal and the identity of who approved it;
- the PHI guard runs on every proposal, every write and every consolidation;
- the audit trail never stores PHI (messages are redacted by the guard).
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from .phi_guard import find_phi, redact_for_audit

Kind = Literal["clinic_correction", "incident_pattern", "staff_preference"]
KINDS: tuple[str, ...] = ("clinic_correction", "incident_pattern", "staff_preference")

# Expiry policy (see docs/agent/memory-design.md): days without being used before a memory is
# archived. Preferences are stable; incident patterns go stale fastest.
TTL_DAYS = {"clinic_correction": 180, "incident_pattern": 90, "staff_preference": 365}
MAX_ACTIVE_MEMORIES = 200  # hard cap: least recently used are archived beyond it
DUPLICATE_SIMILARITY = 0.92  # same scope and this similar = the same fact (newest wins)
PENDING_TTL = timedelta(hours=24)  # a proposal nobody answers expires (never approved)
RECALL_MIN_SCORE = 0.5  # cosine similarity (+0.1 if the question names the memory's clinic)
RECALL_LIMIT = 3

SCHEMA = """
create table if not exists agent_memories (
    id text primary key,
    kind text not null check (kind in ('clinic_correction', 'incident_pattern', 'staff_preference')),
    scope_clinic text,
    scope_country text check (scope_country in ('US', 'UK') or scope_country is null),
    scope_staff text,
    content text not null,
    embedding text not null,
    source_proposal_id text not null,
    approved_by text not null,
    created_at text not null,
    last_used_at text,
    use_count integer not null default 0,
    status text not null default 'active' check (status in ('active', 'archived', 'superseded')),
    archived_reason text,
    superseded_by text
);
create table if not exists memory_proposals (
    id text primary key,
    conversation_id text not null,
    run_id text,
    proposed_to text not null,
    kind text,
    scope_clinic text,
    scope_country text,
    scope_staff text,
    content text,
    reason text,
    source_message text,
    status text not null,
    created_at text not null,
    resolved_at text,
    decision_label text,
    decision_confidence real,
    decision_classifier text,
    decision_message text,
    decided_by text,
    final_content text,
    memory_id text
);
create unique index if not exists one_pending_proposal_per_conversation
    on memory_proposals (conversation_id) where status = 'pending';
create table if not exists memory_events (
    seq integer primary key autoincrement,
    at text not null,
    event text not null,
    actor text,
    conversation_id text,
    proposal_id text,
    memory_id text,
    details text
);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


class MemoryPolicyError(ValueError):
    """A write that breaks a memory rule (PHI, no approval, second pending, unknown kind…)."""


@dataclass(frozen=True)
class Proposal:
    kind: str
    content: str
    reason: str
    scope_clinic: str | None = None
    scope_country: str | None = None
    scope_staff: str | None = None


class MemoryStore:
    def __init__(self, path: Path | str, embed=None):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._embed = embed
        with self._lock, self._conn:
            self._conn.executescript(SCHEMA)

    # ------------------------------------------------------------ helpers

    def embed(self, text: str) -> list[float]:
        if self._embed is None:
            from data.process.rag import embed  # the same embedding model as the RAG

            self._embed = embed
        return self._embed(text)

    def _event(self, event: str, *, actor: str | None = None, conversation_id: str | None = None,
               proposal_id: str | None = None, memory_id: str | None = None, **details: Any) -> None:
        self._conn.execute(
            "insert into memory_events (at, event, actor, conversation_id, proposal_id, memory_id, details) "
            "values (?, ?, ?, ?, ?, ?, ?)",
            (_iso(_now()), event, actor, conversation_id, proposal_id, memory_id,
             json.dumps(details, ensure_ascii=False) if details else None))

    @staticmethod
    def _check_content(content: str) -> None:
        findings = find_phi(content)
        if findings:
            raise MemoryPolicyError("phi:" + ",".join(sorted({f.category for f in findings})))

    # ------------------------------------------------------------ read (recall)

    def recall(self, query: str, *, actor: str | None = None, limit: int | None = None,
               min_score: float | None = None) -> list[dict]:
        """Approved, active memories relevant to `query` (cosine similarity on the embedding).
        Staff preferences only come back for the staff member they belong to, or when the query
        names that person."""
        with self._lock:
            rows = [dict(r) for r in self._conn.execute("select * from agent_memories where status = 'active'")]
        if not rows:
            return []
        limit = RECALL_LIMIT if limit is None else limit
        min_score = RECALL_MIN_SCORE if min_score is None else min_score
        vector = self.embed(query)
        lowered = query.lower()
        scored = []
        for row in rows:
            if row["kind"] == "staff_preference" and row["scope_staff"]:
                staff = row["scope_staff"].lower()
                if staff not in lowered and (actor or "").lower() != staff:
                    continue
            score = cosine(vector, json.loads(row["embedding"]))
            if row["scope_clinic"] and row["scope_clinic"].lower() in lowered:
                score += 0.1  # the question names the memory's clinic
            if score >= min_score:
                scored.append((score, row))
        scored.sort(key=lambda item: -item[0])
        chosen = scored[:limit]
        with self._lock, self._conn:
            for score, row in chosen:
                self._conn.execute("update agent_memories set last_used_at = ?, use_count = use_count + 1 where id = ?",
                                   (_iso(_now()), row["id"]))
        return [{"id": r["id"], "kind": r["kind"], "content": r["content"], "scope_clinic": r["scope_clinic"],
                 "scope_country": r["scope_country"], "scope_staff": r["scope_staff"], "approved_by": r["approved_by"],
                 "created_at": r["created_at"], "score": round(s, 4)} for s, r in chosen]

    def list_memories(self, status: str = "active") -> list[dict]:
        with self._lock:
            return [{k: r[k] for k in r.keys() if k != "embedding"}
                    for r in self._conn.execute("select * from agent_memories where status = ? order by created_at", (status,))]

    # ------------------------------------------------------------ proposals

    def pending(self, conversation_id: str) -> dict | None:
        self.expire_stale_proposals()
        with self._lock:
            row = self._conn.execute("select * from memory_proposals where conversation_id = ? and status = 'pending'",
                                     (conversation_id,)).fetchone()
        return dict(row) if row else None

    def propose(self, proposal: Proposal, *, conversation_id: str, proposed_to: str, source_message: str,
                run_id: str | None = None) -> dict:
        """Register a proposal shown to the user. Never writes memory."""
        if proposal.kind not in KINDS:
            raise MemoryPolicyError(f"kind:{proposal.kind}")
        self._check_content(proposal.content)
        proposal_id = str(uuid.uuid4())
        with self._lock, self._conn:
            try:
                self._conn.execute(
                    "insert into memory_proposals (id, conversation_id, run_id, proposed_to, kind, scope_clinic, scope_country, "
                    "scope_staff, content, reason, source_message, status, created_at) "
                    "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                    (proposal_id, conversation_id, run_id, proposed_to, proposal.kind, proposal.scope_clinic,
                     proposal.scope_country, proposal.scope_staff, proposal.content, proposal.reason,
                     redact_for_audit(source_message), _iso(_now())))
            except sqlite3.IntegrityError as error:
                raise MemoryPolicyError("pending:another proposal is still pending in this conversation") from error
            self._event("proposed", actor=proposed_to, conversation_id=conversation_id, proposal_id=proposal_id,
                        kind=proposal.kind, content=proposal.content)
        return self.get_proposal(proposal_id)

    def record_blocked(self, *, conversation_id: str, proposed_to: str, source_message: str, categories: list[str],
                       run_id: str | None = None) -> dict:
        """A memory attempt refused because it looked like patient information. Only the
        categories are stored: the content never reaches the database."""
        proposal_id = str(uuid.uuid4())
        with self._lock, self._conn:
            self._conn.execute(
                "insert into memory_proposals (id, conversation_id, run_id, proposed_to, source_message, status, created_at, "
                "resolved_at, decision_label, decision_classifier) values (?, ?, ?, ?, ?, 'blocked_phi', ?, ?, 'blocked_phi', 'phi_guard')",
                (proposal_id, conversation_id, run_id, proposed_to, redact_for_audit(source_message), _iso(_now()), _iso(_now())))
            self._event("blocked_phi", actor=proposed_to, conversation_id=conversation_id, proposal_id=proposal_id,
                        categories=sorted(set(categories)))
        return self.get_proposal(proposal_id)

    def get_proposal(self, proposal_id: str) -> dict:
        with self._lock:
            return dict(self._conn.execute("select * from memory_proposals where id = ?", (proposal_id,)).fetchone())

    def resolve(self, proposal_id: str, *, label: str, confidence: float, classifier: str, decided_by: str,
                decision_message: str, edited_content: str | None = None) -> dict:
        """Close a pending proposal with the classified decision. Only approve / edit write memory."""
        proposal = self.get_proposal(proposal_id)
        if proposal["status"] != "pending":
            raise MemoryPolicyError(f"not_pending:{proposal['status']}")
        status = {"approve": "approved", "edit": "approved_edited", "reject": "rejected",
                  "unrelated": "discarded_topic_change"}.get(label, "discarded_ambiguous")
        final_content = edited_content if label == "edit" else proposal["content"]
        memory_id = None
        if status in ("approved", "approved_edited"):
            try:
                self._check_content(final_content or "")
            except MemoryPolicyError as error:  # an edit that introduces PHI is blocked, not stored
                status, final_content = "blocked_phi", None
                self._event("blocked_phi", actor=decided_by, conversation_id=proposal["conversation_id"],
                            proposal_id=proposal_id, categories=str(error).removeprefix("phi:").split(","), stage="edit")
        with self._lock, self._conn:
            if status in ("approved", "approved_edited"):
                memory_id = self._write(proposal, final_content, approved_by=decided_by)
            self._conn.execute(
                "update memory_proposals set status = ?, resolved_at = ?, decision_label = ?, decision_confidence = ?, "
                "decision_classifier = ?, decision_message = ?, decided_by = ?, final_content = ?, memory_id = ? where id = ?",
                (status, _iso(_now()), label, confidence, classifier, redact_for_audit(decision_message), decided_by,
                 final_content, memory_id, proposal_id))
            self._event(status, actor=decided_by, conversation_id=proposal["conversation_id"], proposal_id=proposal_id,
                        memory_id=memory_id, label=label, confidence=confidence, classifier=classifier)
        self.consolidate()
        return self.get_proposal(proposal_id)

    def expire_stale_proposals(self) -> int:
        cutoff = _iso(_now() - PENDING_TTL)
        with self._lock, self._conn:
            stale = [r["id"] for r in self._conn.execute(
                "select id from memory_proposals where status = 'pending' and created_at < ?", (cutoff,))]
            for proposal_id in stale:
                self._conn.execute("update memory_proposals set status = 'expired', resolved_at = ?, decision_label = 'no_answer' "
                                   "where id = ?", (_iso(_now()), proposal_id))
                self._event("expired", proposal_id=proposal_id, reason=f"no answer in {PENDING_TTL}")
        return len(stale)

    # ------------------------------------------------------------ write (only from an approved proposal)

    def _write(self, proposal: dict, content: str, *, approved_by: str) -> str:
        """Insert an approved memory, superseding a near-duplicate of the same scope."""
        if not approved_by:
            raise MemoryPolicyError("no_approver")
        self._check_content(content)
        vector = self.embed(content)
        memory_id = str(uuid.uuid4())
        self._conn.execute(
            "insert into agent_memories (id, kind, scope_clinic, scope_country, scope_staff, content, embedding, "
            "source_proposal_id, approved_by, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (memory_id, proposal["kind"], proposal["scope_clinic"], proposal["scope_country"], proposal["scope_staff"],
             content, json.dumps(vector), proposal["id"], approved_by, _iso(_now())))
        self._event("memory_written", actor=approved_by, conversation_id=proposal["conversation_id"],
                    proposal_id=proposal["id"], memory_id=memory_id, kind=proposal["kind"])
        for row in self._conn.execute(
                "select id, embedding, scope_clinic, scope_staff, kind from agent_memories where status = 'active' and id != ?",
                (memory_id,)).fetchall():
            same_scope = (row["kind"] == proposal["kind"] and (row["scope_clinic"] or "") == (proposal["scope_clinic"] or "")
                          and (row["scope_staff"] or "") == (proposal["scope_staff"] or ""))
            if same_scope and cosine(vector, json.loads(row["embedding"])) >= DUPLICATE_SIMILARITY:
                self._conn.execute("update agent_memories set status = 'superseded', superseded_by = ? where id = ?",
                                   (memory_id, row["id"]))
                self._event("superseded", memory_id=row["id"], superseded_by=memory_id)
        return memory_id

    # ------------------------------------------------------------ consolidation / cleanup

    def consolidate(self, now: datetime | None = None) -> dict[str, int]:
        """Keep the memory small and clean (run after every write and on demand):
        1. re-verify PHI on every active memory (BRIEFING) → archived as phi_recheck;
        2. archive memories unused for longer than their kind's TTL → expired;
        3. archive the least recently used beyond MAX_ACTIVE_MEMORIES → capacity;
        4. expire pending proposals older than PENDING_TTL.
        Archived memories are never recalled again; the audit trail keeps the history."""
        now = now or _now()
        counts = {"phi_recheck": 0, "expired": 0, "capacity": 0}
        with self._lock, self._conn:
            rows = [dict(r) for r in self._conn.execute("select * from agent_memories where status = 'active'")]
            for row in rows:
                reason = None
                if find_phi(row["content"]):
                    reason = "phi_recheck"
                else:
                    last = datetime.fromisoformat(row["last_used_at"] or row["created_at"])
                    if now - last > timedelta(days=TTL_DAYS[row["kind"]]):
                        reason = "expired"
                if reason:
                    counts[reason] += 1
                    self._conn.execute("update agent_memories set status = 'archived', archived_reason = ? where id = ?",
                                       (reason, row["id"]))
                    self._event("archived", memory_id=row["id"], reason=reason)
            active = [dict(r) for r in self._conn.execute(
                "select id from agent_memories where status = 'active' order by coalesce(last_used_at, created_at) desc")]
            for row in active[MAX_ACTIVE_MEMORIES:]:
                counts["capacity"] += 1
                self._conn.execute("update agent_memories set status = 'archived', archived_reason = 'capacity' where id = ?",
                                   (row["id"],))
                self._event("archived", memory_id=row["id"], reason="capacity")
        counts["expired_proposals"] = self.expire_stale_proposals()
        return counts

    # ------------------------------------------------------------ audit

    def audit_trail(self, conversation_id: str | None = None) -> list[dict]:
        query = "select * from memory_events" + (" where conversation_id = ?" if conversation_id else "") + " order by seq"
        with self._lock:
            rows = self._conn.execute(query, (conversation_id,) if conversation_id else ()).fetchall()
        return [{**dict(r), "details": json.loads(r["details"]) if r["details"] else None} for r in rows]

    def proposals(self, conversation_id: str | None = None) -> list[dict]:
        query = "select * from memory_proposals" + (" where conversation_id = ?" if conversation_id else "") + " order by created_at"
        with self._lock:
            return [dict(r) for r in self._conn.execute(query, (conversation_id,) if conversation_id else ())]


_store: MemoryStore | None = None
_store_lock = threading.Lock()


def memory_database() -> Path:
    from data.process.rag import REPO_ROOT

    return Path(os.environ.get("AGENT_STATE_DIR", REPO_ROOT / "data" / "agent_state")) / "agent_memory.sqlite"


def get_store() -> MemoryStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = MemoryStore(memory_database())
        return _store
