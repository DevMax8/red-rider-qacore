"""Transactional SQLite persistence for source dialogs, assessments and reviews."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from app.defaults import default_checklist
from app.domain import Checklist, Conversation, EvaluationResult, ModelInfo


class ConflictError(ValueError):
    """A stored resource changed or an operation conflicts with current state."""


def dump(value) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


def fingerprint(value) -> str:
    return hashlib.sha256(dump(value).encode("utf-8")).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "cqa.sqlite3"
        with self.connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS datasets (
                    group_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                    total_records INTEGER NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS checklists (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS records (
                    group_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
                    conversation TEXT NOT NULL, original_result TEXT,
                    final_result TEXT, checklist TEXT, model TEXT, evaluation_id TEXT,
                    PRIMARY KEY (group_id, conversation_id)
                );
                CREATE TABLE IF NOT EXISTS appeals (
                    id TEXT PRIMARY KEY, group_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS appeals_conversation
                    ON appeals(group_id, conversation_id);
            """)
            checklist = default_checklist()
            conn.execute("INSERT OR IGNORE INTO checklists VALUES (?, ?)", (checklist.id, dump(checklist)))

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def import_dataset(self, group_id: str, conversations: list[Conversation]) -> dict:
        if not isinstance(group_id, str) or not group_id.strip() or not conversations:
            raise ValueError("Dataset group and records must not be empty")
        conversations = [Conversation.model_validate(c) for c in conversations]
        ids = [c.conversation_id for c in conversations]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate conversation IDs")
        digest = fingerprint(sorted([c.model_dump(mode="json") for c in conversations], key=lambda c: c["conversation_id"]))
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT fingerprint FROM datasets WHERE group_id=?", (group_id,)).fetchone()
            if existing:
                if existing["fingerprint"] != digest:
                    raise ConflictError("This group already contains different source data; choose another group")
                return {"group_id": group_id, "total_records": len(conversations), "already_imported": True}
            conn.execute("INSERT INTO datasets VALUES (?, ?, ?, ?)", (group_id, digest, len(conversations), now()))
            conn.executemany("INSERT INTO records (group_id, conversation_id, conversation) VALUES (?, ?, ?)",
                             [(group_id, c.conversation_id, dump(c)) for c in conversations])
        return {"group_id": group_id, "total_records": len(conversations), "already_imported": False}

    def list_datasets(self) -> list[dict]:
        with self.connection() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT group_id,total_records,created_at FROM datasets ORDER BY created_at")]

    def list_conversations(self, group_id: str) -> list[Conversation]:
        with self.connection() as conn:
            if not conn.execute("SELECT 1 FROM datasets WHERE group_id=?", (group_id,)).fetchone():
                raise KeyError("Dataset not found")
            return [Conversation.model_validate_json(r["conversation"]) for r in conn.execute(
                "SELECT conversation FROM records WHERE group_id=? ORDER BY conversation_id", (group_id,))]

    def list_checklists(self) -> list[Checklist]:
        with self.connection() as conn:
            return [Checklist.model_validate_json(r["payload"]) for r in conn.execute(
                "SELECT payload FROM checklists ORDER BY id")]

    def get_checklist(self, checklist_id: str = "default") -> Checklist:
        with self.connection() as conn:
            row = conn.execute("SELECT payload FROM checklists WHERE id=?", (checklist_id,)).fetchone()
        if not row:
            raise KeyError("Checklist not found")
        return Checklist.model_validate_json(row["payload"])

    def save_checklist(self, checklist: Checklist, *, create: bool = False) -> Checklist:
        checklist = Checklist.model_validate(checklist)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT payload FROM checklists WHERE id=?", (checklist.id,)).fetchone()
            if create and row:
                raise ConflictError("Checklist already exists")
            if not create and not row:
                raise KeyError("Checklist not found")
            version = 1
            if row:
                previous = Checklist.model_validate_json(row["payload"])
                if checklist.version != previous.version:
                    raise ConflictError("Checklist has changed; reload its current version")
                version = previous.version + 1
            saved = Checklist.model_validate({**checklist.model_dump(), "version": version})
            conn.execute("INSERT INTO checklists VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
                         (saved.id, dump(saved)))
        return saved

    def get_record(self, group_id: str, conversation_id: str) -> dict:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM records WHERE group_id=? AND conversation_id=?",
                               (group_id, conversation_id)).fetchone()
            if not row:
                raise KeyError("Conversation not found")
            appeals = [json.loads(a["payload"]) for a in conn.execute(
                "SELECT payload FROM appeals WHERE group_id=? AND conversation_id=? ORDER BY rowid",
                (group_id, conversation_id))]
        return {"group_id": group_id, "evaluation_id": row["evaluation_id"],
                **{key: json.loads(row[key]) if row[key] is not None else None
                   for key in ("conversation", "original_result", "final_result", "checklist", "model")},
                "appeals": appeals}

    def save_result(self, group_id: str, conversation_id: str, checklist: Checklist,
                    model: ModelInfo, result: EvaluationResult) -> None:
        result = EvaluationResult.model_validate(result)
        if result.conversation_id != conversation_id:
            raise ValueError("Result conversation ID mismatch")
        with self.connection() as conn:
            cursor = conn.execute("""UPDATE records SET original_result=?,final_result=?,checklist=?,model=?,evaluation_id=?
                WHERE group_id=? AND conversation_id=?""",
                (dump(result), dump(result), dump(checklist), dump(model), uuid4().hex, group_id, conversation_id))
            if cursor.rowcount != 1:
                raise KeyError("Conversation not found")

    def create_appeal(self, group_id: str, conversation_id: str, criterion_id: str, comment: str) -> dict:
        if not isinstance(comment, str) or not comment.strip():
            raise ValueError("An appeal needs a comment")
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM records WHERE group_id=? AND conversation_id=?",
                               (group_id, conversation_id)).fetchone()
            if not row:
                raise KeyError("Conversation not found")
            result = EvaluationResult.model_validate_json(row["final_result"]) if row["final_result"] else None
            if not result or result.status != "ok":
                raise ConflictError("A successful evaluation is required before an appeal")
            criterion = next((c for c in result.criteria if c.criterion_id == criterion_id), None)
            if not criterion:
                raise ValueError("Unknown criterion")
            appeal = {"id": uuid4().hex, "group_id": group_id, "conversation_id": conversation_id,
                      "evaluation_id": row["evaluation_id"], "criterion_id": criterion_id,
                      "comment": comment, "original_criterion": criterion.model_dump(mode="json"),
                      "status": "pending", "created_at": now(), "decision": None,
                      "result_fingerprint": fingerprint(result)}
            conn.execute("INSERT INTO appeals VALUES (?, ?, ?, ?)",
                         (appeal["id"], group_id, conversation_id, dump(appeal)))
        return appeal

    def get_appeal(self, appeal_id: str) -> dict:
        with self.connection() as conn:
            row = conn.execute("SELECT payload FROM appeals WHERE id=?", (appeal_id,)).fetchone()
        if not row:
            raise KeyError("Appeal not found")
        return json.loads(row["payload"])

    def resolve_appeal(self, appeal_id: str, result: EvaluationResult, decision: dict) -> dict:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT payload FROM appeals WHERE id=?", (appeal_id,)).fetchone()
            if not row:
                raise KeyError("Appeal not found")
            appeal = json.loads(row["payload"])
            if appeal["status"] != "pending":
                raise ConflictError("Appeal already decided")
            record = conn.execute("SELECT * FROM records WHERE group_id=? AND conversation_id=?",
                                  (appeal["group_id"], appeal["conversation_id"])).fetchone()
            if record["evaluation_id"] != appeal["evaluation_id"] or fingerprint(json.loads(record["final_result"])) != appeal["result_fingerprint"]:
                raise ConflictError("Evaluation has changed; create a new appeal against the current result")
            appeal.update(status="resolved", decision=decision, resolved_at=now())
            conn.execute("UPDATE records SET final_result=? WHERE group_id=? AND conversation_id=?",
                         (dump(result), appeal["group_id"], appeal["conversation_id"]))
            conn.execute("UPDATE appeals SET payload=? WHERE id=?", (dump(appeal), appeal_id))
        return appeal
