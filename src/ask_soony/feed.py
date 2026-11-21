"""
Activity feed events derived read-only from the watcher store and the mail folders.

Everything here is defensive: a missing database, table, column or folder yields no events.
Each event is {at, actor, text, ref?} with `at` as an ISO timestamp in UTC.
"""

import email
import json
import sqlite3
from datetime import UTC, datetime
from email import policy
from email.utils import parsedate_to_datetime
from pathlib import Path

LIMIT = 50


def iso(value) -> str:
    """Best-effort conversion of ISO or RFC 2822 dates to ISO UTC; empty string if unparseable."""
    if not value:
        return ""
    text = str(value)
    for parse in (datetime.fromisoformat, parsedate_to_datetime):
        try:
            dt = parse(text)
        except (TypeError, ValueError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC).isoformat(timespec="seconds")
    return ""


def _query(db: sqlite3.Connection, sql: str) -> list[sqlite3.Row]:
    try:
        return db.execute(sql).fetchall()
    except sqlite3.Error:
        return []


def db_events(db_path: Path) -> list[dict]:
    if not db_path.is_file():
        return []
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    db.row_factory = sqlite3.Row
    events: list[dict] = []
    try:
        for r in _query(
            db,
            "SELECT at, n_new + n_changed + n_removed AS n FROM polls "
            f"WHERE n_new + n_changed + n_removed > 0 ORDER BY id DESC LIMIT {LIMIT}",
        ):
            events.append(
                {
                    "at": iso(r["at"]),
                    "actor": "watcher",
                    "text": f"New poll: {r['n']} changed files",
                }
            )

        # The unmerged watcher branch adds a `changes` table with a summary; use it if present.
        rows = _query(
            db,
            "SELECT v.path, v.change, v.first_seen, c.summary FROM versions v "
            "LEFT JOIN changes c ON c.version_id = v.id "
            f"WHERE v.change != 'baseline' ORDER BY v.id DESC LIMIT {LIMIT}",
        ) or _query(
            db,
            "SELECT path, change, first_seen, NULL AS summary FROM versions "
            f"WHERE change != 'baseline' ORDER BY id DESC LIMIT {LIMIT}",
        )
        for r in rows:
            text = f"{'Changed' if r['change'] == 'changed' else 'New'} file {r['path']}"
            if r["summary"]:
                text += f": {r['summary']}"
            events.append(
                {"at": iso(r["first_seen"]), "actor": "watcher", "text": text, "ref": r["path"]}
            )

        for r in _query(
            db,
            "SELECT a.at, a.summary, v.path FROM analyses a JOIN versions v ON v.id = a.version_id "
            f"ORDER BY a.id DESC LIMIT {LIMIT}",
        ):
            events.append(
                {
                    "at": iso(r["at"]),
                    "actor": "analyst",
                    "text": f"Analysis done for {r['path']}: {r['summary']}",
                    "ref": r["path"],
                }
            )

        for r in _query(
            db,
            "SELECT f.created_at, f.severity, f.title, v.path FROM findings f "
            f"JOIN versions v ON v.id = f.version_id ORDER BY f.id DESC LIMIT {LIMIT}",
        ):
            events.append(
                {
                    "at": iso(r["created_at"]),
                    "actor": "analyst",
                    "text": f"Finding opened ({r['severity']}): {r['title']}",
                    "ref": r["path"],
                }
            )
    finally:
        db.close()
    return events


def mail_events(root: Path) -> list[dict]:
    """Supplier mail log: outbox/*.json (sent) and inbox/*.eml (received) next to the store."""
    events: list[dict] = []
    for f in sorted((root / "outbox").glob("*.json")):
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
            events.append(
                {
                    "at": iso(rec.get("date")),
                    "actor": "mailer",
                    "text": f"Sent to {rec.get('to', '?')}: {rec.get('subject', '')}",
                    "ref": f.name,
                }
            )
        except (OSError, ValueError, AttributeError):
            continue
    for f in sorted((root / "inbox").glob("*.eml")):
        try:
            m = email.message_from_bytes(f.read_bytes(), policy=policy.default)
            events.append(
                {
                    "at": iso(m["Date"]),
                    "actor": "mailer",
                    "text": f"Received from {m['From']}: {m['Subject']}",
                    "ref": f.name,
                }
            )
        except (OSError, ValueError):
            continue
    return events
