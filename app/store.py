"""SQLite storage: every submission and its status, and the approved reports table."""
import json
import os
import sqlite3
from datetime import datetime, timezone

from . import config


def connect():
    os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
    con = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE IF NOT EXISTS submissions (
        id TEXT PRIMARY KEY, created_at TEXT, status TEXT, fields TEXT, errors TEXT,
        fix_request TEXT, summary TEXT, reviewer TEXT, note TEXT);
    CREATE TABLE IF NOT EXISTS approved_reports (
        id TEXT PRIMARY KEY, organization TEXT, country TEXT, quarter TEXT, year INTEGER,
        contact_email TEXT, participants INTEGER, budget_spent REAL, highlights TEXT,
        reviewer TEXT, approved_at TEXT);
    """)
    cols = [r[1] for r in con.execute("PRAGMA table_info(submissions)")]
    if "source" not in cols:
        con.execute("ALTER TABLE submissions ADD COLUMN source TEXT")
    return con


CON = connect()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def upsert_submission(sid: str, **cols):
    cols = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in cols.items()}
    exists = CON.execute("SELECT 1 FROM submissions WHERE id=?", (sid,)).fetchone()
    if exists:
        sets = ", ".join(f"{k}=?" for k in cols)
        CON.execute(f"UPDATE submissions SET {sets} WHERE id=?", (*cols.values(), sid))
    else:
        cols = {"id": sid, "created_at": now(), **cols}
        CON.execute(f"INSERT INTO submissions ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", tuple(cols.values()))
    CON.commit()


def save_approved(sid: str, fields: dict, reviewer: str):
    CON.execute("""INSERT OR REPLACE INTO approved_reports VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (sid, fields["organization"], fields["country"], fields["quarter"], fields["year"],
                 fields["contact_email"], fields["participants"], fields["budget_spent"],
                 fields.get("highlights"), reviewer, now()))
    CON.commit()


def list_submissions():
    rows = CON.execute("SELECT * FROM submissions ORDER BY created_at DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["fields"] = json.loads(d["fields"]) if d["fields"] else {}
        d["errors"] = json.loads(d["errors"]) if d["errors"] else []
        out.append(d)
    return out
