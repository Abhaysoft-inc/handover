"""SQLite persistence (WAL) with a small helper API and an FTS5 knowledge index."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import DATA_DIR

DB_PATH = Path(os.getenv("HANDOVER_DB") or DATA_DIR / "handover.db")   # tests point this elsewhere

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY, source TEXT NOT NULL, source_ref TEXT NOT NULL, name TEXT NOT NULL,
  path TEXT, mime_type TEXT, size INTEGER, modified_at TEXT,
  status TEXT NOT NULL DEFAULT 'discovered', skip_reason TEXT,
  kind TEXT, is_employment INTEGER, language TEXT, language_name TEXT, is_scanned INTEGER,
  title TEXT, summary TEXT, analysis_json TEXT, text_content TEXT, pages INTEGER,
  error TEXT, analyzed_at TEXT, run_id TEXT
);
CREATE TABLE IF NOT EXISTS contracts (
  id TEXT PRIMARY KEY, employee_name TEXT NOT NULL, employee_email TEXT,
  job_title TEXT, department TEXT, employer_entity TEXT, country TEXT, work_location TEXT,
  governing_law TEXT, language TEXT, language_name TEXT, contract_type TEXT,
  start_date TEXT, end_date TEXT, auto_renewal INTEGER, renewal_terms TEXT,
  notice_period_days INTEGER, notice_terms TEXT,
  action_deadline TEXT, action_deadline_reason TEXT, probation_end_date TEXT,
  compensation_text TEXT, compensation_amount REAL, compensation_currency TEXT, compensation_period TEXT,
  non_compete TEXT, termination_terms TEXT, summary TEXT,
  key_dates_json TEXT, key_clauses_json TEXT, risk_flags_json TEXT,
  recommended_action TEXT, recommendation_rationale TEXT, confidence TEXT, status_note TEXT,
  signed INTEGER, primary_document_id TEXT, supporting_document_ids TEXT,
  created_at TEXT, updated_at TEXT, run_id TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, contract_id TEXT NOT NULL UNIQUE, action TEXT NOT NULL,
  params_json TEXT, status TEXT NOT NULL DEFAULT 'pending_draft', created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS drafts (
  id TEXT PRIMARY KEY, contract_id TEXT, decision_id INTEGER, doc_type TEXT, title TEXT,
  language TEXT, body_markdown TEXT, english_summary TEXT, compliance_notes_json TEXT,
  delivery_json TEXT, status TEXT NOT NULL DEFAULT 'draft', version INTEGER DEFAULT 1,
  docx_path TEXT, pdf_path TEXT, created_at TEXT, updated_at TEXT, approved_at TEXT, run_id TEXT
);
CREATE TABLE IF NOT EXISTS emails (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, contract_id TEXT, draft_id TEXT, reminder_id INTEGER,
  to_json TEXT, cc_json TEXT, subject TEXT, body TEXT, attachments_json TEXT,
  status TEXT, transport TEXT, error TEXT, created_at TEXT, sent_at TEXT, run_id TEXT
);
CREATE TABLE IF NOT EXISTS reminders (
  id INTEGER PRIMARY KEY AUTOINCREMENT, contract_id TEXT, draft_id TEXT, due_date TEXT NOT NULL,
  recipients_json TEXT, subject TEXT, message TEXT, kind TEXT,
  status TEXT NOT NULL DEFAULT 'scheduled', created_by TEXT, created_at TEXT, sent_at TEXT, run_id TEXT
);
CREATE TABLE IF NOT EXISTS filings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, draft_id TEXT, contract_id TEXT, folder_path TEXT,
  file_name TEXT, drive_ref TEXT, web_link TEXT, created_at TEXT, run_id TEXT
);
CREATE TABLE IF NOT EXISTS issues (
  id INTEGER PRIMARY KEY AUTOINCREMENT, contract_id TEXT, severity TEXT, title TEXT, detail TEXT,
  source TEXT, created_at TEXT, run_id TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, kind TEXT, status TEXT, mode TEXT, title TEXT, started_at TEXT,
  finished_at TEXT, summary TEXT, error TEXT, usage_json TEXT
);
CREATE TABLE IF NOT EXISTS run_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, seq INTEGER, ts TEXT, type TEXT, data_json TEXT
);
CREATE INDEX IF NOT EXISTS run_events_run ON run_events(run_id, seq);
CREATE TABLE IF NOT EXISTS briefings (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, markdown TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS chat_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT, role TEXT, content TEXT,
  run_id TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY AUTOINCREMENT, contract_id TEXT, ts TEXT, kind TEXT, text TEXT
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS kb USING fts5(
  ref_type, ref_id UNINDEXED, title, body, tokenize = 'unicode61 remove_diacritics 2'
);
"""

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=OFF")
        _conn.executescript(SCHEMA)
    return _conn


def execute(sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    with _lock:
        return conn().execute(sql, tuple(params))


def query(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, tuple(params)).fetchall()]


def one(sql: str, params: Iterable[Any] = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def scalar(sql: str, params: Iterable[Any] = ()) -> Any:
    with _lock:
        row = conn().execute(sql, tuple(params)).fetchone()
    return row[0] if row else None


def insert(table: str, values: dict) -> int:
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    cur = execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", values.values())
    return cur.lastrowid


def upsert(table: str, values: dict, key: str = "id") -> None:
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    updates = ", ".join(f"{c}=excluded.{c}" for c in values if c != key)
    execute(f"INSERT INTO {table} ({cols}) VALUES ({marks}) ON CONFLICT({key}) DO UPDATE SET {updates}",
            values.values())


def update(table: str, key_value: Any, values: dict, key: str = "id") -> None:
    sets = ", ".join(f"{c}=?" for c in values)
    execute(f"UPDATE {table} SET {sets} WHERE {key}=?", [*values.values(), key_value])


def kv_get(k: str, default: Any = None) -> Any:
    v = scalar("SELECT v FROM kv WHERE k=?", (k,))
    return json.loads(v) if v is not None else default


def kv_set(k: str, v: Any) -> None:
    execute("INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))


def loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def next_id(table: str, prefix: str) -> str:
    n = scalar(f"SELECT COUNT(*) FROM {table}") or 0
    while True:
        n += 1
        candidate = f"{prefix}-{n:03d}"
        if not scalar(f"SELECT 1 FROM {table} WHERE id=?", (candidate,)):
            return candidate


def log_activity(contract_id: str | None, kind: str, text: str) -> None:
    if contract_id:
        insert("activity", {"contract_id": contract_id, "ts": now_iso(), "kind": kind, "text": text})


# ── knowledge index ───────────────────────────────────────────────────────


def kb_index(ref_type: str, ref_id: str, title: str, body: str) -> None:
    execute("DELETE FROM kb WHERE ref_type=? AND ref_id=?", (ref_type, ref_id))
    execute("INSERT INTO kb (ref_type, ref_id, title, body) VALUES (?, ?, ?, ?)", (ref_type, ref_id, title, body))


def kb_search(text: str, limit: int = 8) -> list[dict]:
    """Full-text search; falls back to substring match (useful for CJK)."""
    terms = [t for t in "".join(c if c.isalnum() else " " for c in text).split() if len(t) > 1]
    rows: list[dict] = []
    if terms:
        fts = " OR ".join(f'"{t}"' for t in terms[:12])
        try:
            rows = query(
                "SELECT ref_type, ref_id, title, snippet(kb, 3, '«', '»', ' … ', 18) AS snippet, "
                "bm25(kb) AS score FROM kb WHERE kb MATCH ? ORDER BY score LIMIT ?", (fts, limit))
        except sqlite3.OperationalError:
            rows = []
    if not rows and text.strip():
        like = f"%{text.strip()}%"
        rows = query("SELECT ref_type, ref_id, title, substr(body, 1, 220) AS snippet, 0 AS score FROM kb "
                     "WHERE body LIKE ? OR title LIKE ? LIMIT ?", (like, like, limit))
    return rows


RESET_TABLES = ["documents", "contracts", "decisions", "drafts", "emails", "reminders", "filings", "issues",
                "runs", "run_events", "briefings", "chat_messages", "activity", "kb"]


def reset_all() -> None:
    with _lock:
        for t in RESET_TABLES:
            conn().execute(f"DELETE FROM {t}")
        conn().execute("DELETE FROM kv WHERE k NOT LIKE 'google_%'")
