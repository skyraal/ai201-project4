"""SQLite-backed storage: content state + append-only structured audit log."""
import json
import sqlite3
from datetime import datetime, timezone

DB_PATH = "provenance.db"


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _connect()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS content (
            content_id TEXT PRIMARY KEY,
            creator_id TEXT,
            text TEXT,
            status TEXT,
            attribution TEXT,
            confidence REAL,
            label TEXT,
            llm_score REAL,
            stylometric_score REAL,
            created_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_id TEXT,
            creator_id TEXT,
            event_type TEXT,
            timestamp TEXT,
            attribution TEXT,
            confidence REAL,
            llm_score REAL,
            stylometric_score REAL,
            label TEXT,
            status TEXT,
            appeal_reasoning TEXT,
            signal_details TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def _now():
    return datetime.now(timezone.utc).isoformat()


def create_content(content_id, creator_id, text, attribution, confidence, label,
                    llm_score, stylometric_score, signal_details):
    now = _now()
    conn = _connect()
    conn.execute(
        """INSERT INTO content
           (content_id, creator_id, text, status, attribution, confidence,
            label, llm_score, stylometric_score, created_at)
           VALUES (?, ?, ?, 'classified', ?, ?, ?, ?, ?, ?)""",
        (content_id, creator_id, text, attribution, confidence, label,
         llm_score, stylometric_score, now),
    )
    conn.execute(
        """INSERT INTO audit_log
           (content_id, creator_id, event_type, timestamp, attribution, confidence,
            llm_score, stylometric_score, label, status, appeal_reasoning, signal_details)
           VALUES (?, ?, 'submission', ?, ?, ?, ?, ?, ?, 'classified', NULL, ?)""",
        (content_id, creator_id, now, attribution, confidence, llm_score,
         stylometric_score, label, json.dumps(signal_details)),
    )
    conn.commit()
    conn.close()
    return now


def get_content(content_id):
    conn = _connect()
    row = conn.execute("SELECT * FROM content WHERE content_id = ?", (content_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def file_appeal(content_id, creator_reasoning):
    """Set status to under_review and log the appeal alongside the original decision."""
    content = get_content(content_id)
    if content is None:
        return None

    now = _now()
    conn = _connect()
    conn.execute("UPDATE content SET status = 'under_review' WHERE content_id = ?", (content_id,))
    conn.execute(
        """INSERT INTO audit_log
           (content_id, creator_id, event_type, timestamp, attribution, confidence,
            llm_score, stylometric_score, label, status, appeal_reasoning, signal_details)
           VALUES (?, ?, 'appeal', ?, ?, ?, ?, ?, ?, 'under_review', ?, NULL)""",
        (content_id, content["creator_id"], now, content["attribution"], content["confidence"],
         content["llm_score"], content["stylometric_score"], content["label"], creator_reasoning),
    )
    conn.commit()
    conn.close()
    return now


def get_log(limit=50):
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    entries = []
    for row in rows:
        entry = dict(row)
        if entry.get("signal_details"):
            entry["signal_details"] = json.loads(entry["signal_details"])
        entries.append(entry)
    return entries
