"""
Versioned local store.

Every file version the portal ever served is kept as a blob named by its SHA-256, and SQLite
records when each version was first and last seen. Nothing is overwritten: a replaced file is a
new version, a removed file gets a removed_at. Analyses and findings point at the version they
came from, so every finding can be traced back to the exact bytes that caused it.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ask_soony.portal import RemoteFile

SEVERITIES = ["info", "low", "medium", "high", "critical"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS polls (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    n_files INTEGER NOT NULL,
    n_new INTEGER NOT NULL,
    n_changed INTEGER NOT NULL,
    n_removed INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER,
    first_seen TEXT NOT NULL,
    first_poll INTEGER NOT NULL,
    last_seen TEXT NOT NULL,
    removed_at TEXT,
    change TEXT NOT NULL,            -- baseline | new | changed
    analysis_status TEXT NOT NULL,   -- pending | done | skipped | error
    UNIQUE (path, sha256)
);
CREATE TABLE IF NOT EXISTS extracts (
    sha256 TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    source TEXT NOT NULL             -- parser | transcription
);
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY,
    version_id INTEGER NOT NULL REFERENCES versions(id),
    at TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    summary TEXT NOT NULL,
    relevance TEXT NOT NULL,
    raw TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS changes (
    version_id INTEGER PRIMARY KEY REFERENCES versions(id),
    summary TEXT NOT NULL,
    diff TEXT NOT NULL,
    computed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY,
    analysis_id INTEGER NOT NULL REFERENCES analyses(id),
    version_id INTEGER NOT NULL REFERENCES versions(id),
    created_at TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT NOT NULL,
    evidence TEXT NOT NULL,          -- JSON list of {path, quote}
    suggested_action TEXT NOT NULL,
    status TEXT NOT NULL,            -- open | resolved
    resolved_by INTEGER REFERENCES analyses(id)
);
"""


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Version:
    id: int
    path: str
    sha256: str
    size: int | None
    first_seen: str
    change: str
    analysis_status: str


@dataclass
class PollResult:
    poll_id: int
    new: list[Version]
    changed: list[Version]
    removed: list[str]
    baseline: bool
    stored: int = 0

    @property
    def has_changes(self) -> bool:
        return bool(self.new or self.changed or self.removed)


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.blobs = root / "blobs"
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(root / "ask-soony.db")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # --- blobs -----------------------------------------------------------------------------

    def blob_path(self, sha256: str) -> Path:
        return self.blobs / sha256[:2] / sha256

    def has_blob(self, sha256: str) -> bool:
        return self.blob_path(sha256).exists()

    def put_blob(self, sha256: str, data: bytes) -> None:
        path = self.blob_path(sha256)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    def read_blob(self, sha256: str) -> bytes:
        return self.blob_path(sha256).read_bytes()

    # --- polling ---------------------------------------------------------------------------

    def is_empty(self) -> bool:
        return self.db.execute("SELECT COUNT(*) FROM versions").fetchone()[0] == 0

    def current(self) -> dict[str, Version]:
        """Latest live version per path."""
        rows = self.db.execute(
            "SELECT * FROM versions v WHERE removed_at IS NULL AND id = "
            "(SELECT MAX(id) FROM versions w WHERE w.path = v.path AND w.removed_at IS NULL)"
        ).fetchall()
        return {r["path"]: _version(r) for r in rows}

    def record_listing(
        self, listing: list[RemoteFile], analyze_baseline: bool = False
    ) -> PollResult:
        """
        Compare a portal listing with what we know and record new versions.

        On the very first poll every file is a baseline version: stored but not queued for
        analysis unless analyze_baseline is set.
        """
        at = now_iso()
        baseline = self.is_empty()
        known = self.current()
        cur = self.db.execute(
            "INSERT INTO polls (at, n_files, n_new, n_changed, n_removed) VALUES (?, ?, 0, 0, 0)",
            (at, len(listing)),
        )
        poll_id = cur.lastrowid
        new, changed = [], []
        stored = 0
        seen = set()
        for f in listing:
            seen.add(f.path)
            prev = known.get(f.path)
            if prev and prev.sha256 == f.sha256:
                self.db.execute("UPDATE versions SET last_seen = ? WHERE id = ?", (at, prev.id))
                continue
            if baseline:
                change, status = "baseline", ("pending" if analyze_baseline else "skipped")
            else:
                change, status = ("changed" if prev else "new"), "pending"
            if prev:
                # The old version stays; it is only no longer the live one.
                self.db.execute("UPDATE versions SET removed_at = ? WHERE id = ?", (at, prev.id))
            vid = self.db.execute(
                "INSERT INTO versions (path, sha256, size, first_seen, first_poll, last_seen, change,"
                " analysis_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (path, sha256) DO UPDATE SET removed_at = NULL, last_seen = excluded.last_seen"
                " RETURNING id",
                (f.path, f.sha256, f.size, at, poll_id, at, change, status),
            ).fetchone()[0]
            stored += 1
            version = Version(vid, f.path, f.sha256, f.size, at, change, status)
            if change == "changed":
                changed.append(version)
            elif change == "new":
                new.append(version)
        removed = sorted(set(known) - seen)
        for path in removed:
            self.db.execute("UPDATE versions SET removed_at = ? WHERE id = ?", (at, known[path].id))
        self.db.execute(
            "UPDATE polls SET n_new = ?, n_changed = ?, n_removed = ? WHERE id = ?",
            (len(new), len(changed), len(removed), poll_id),
        )
        self.db.commit()
        return PollResult(poll_id, new, changed, removed, baseline, stored)

    def previous_version(self, version: Version) -> Version | None:
        row = self.db.execute(
            "SELECT * FROM versions WHERE path = ? AND id < ? ORDER BY id DESC LIMIT 1",
            (version.path, version.id),
        ).fetchone()
        return _version(row) if row else None

    def pending(self, limit: int | None = None) -> list[Version]:
        sql = "SELECT * FROM versions WHERE analysis_status = 'pending' ORDER BY id"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [_version(r) for r in self.db.execute(sql).fetchall()]

    def mark_pending(self, path_prefix: str) -> int:
        cur = self.db.execute(
            "UPDATE versions SET analysis_status = 'pending' WHERE removed_at IS NULL AND path LIKE ?",
            (path_prefix + "%",),
        )
        self.db.commit()
        return cur.rowcount

    def set_status(self, version_id: int, status: str) -> None:
        self.db.execute(
            "UPDATE versions SET analysis_status = ? WHERE id = ?", (status, version_id)
        )
        self.db.commit()

    # --- extracted text --------------------------------------------------------------------

    def get_extract(self, sha256: str) -> tuple[str, str, str] | None:
        row = self.db.execute(
            "SELECT kind, text, source FROM extracts WHERE sha256 = ?", (sha256,)
        ).fetchone()
        return (row["kind"], row["text"], row["source"]) if row else None

    def put_extract(self, sha256: str, kind: str, text: str, source: str) -> None:
        self.db.execute(
            "INSERT INTO extracts (sha256, kind, text, source) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (sha256) DO UPDATE SET kind = excluded.kind, text = excluded.text,"
            " source = excluded.source",
            (sha256, kind, text, source),
        )
        self.db.commit()

    # --- analyses and findings -------------------------------------------------------------

    def save_analysis(self, version: Version, provider: str, model: str, result: dict) -> int:
        at = now_iso()
        aid = self.db.execute(
            "INSERT INTO analyses (version_id, at, provider, model, summary, relevance, raw)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                version.id,
                at,
                provider,
                model,
                str(result.get("summary", "")),
                str(result.get("launch_relevance", "unknown")),
                json.dumps(result, ensure_ascii=False),
            ),
        ).lastrowid
        for f in result.get("findings") or []:
            severity = str(f.get("severity", "info")).lower()
            if severity not in SEVERITIES:
                severity = "info"
            self.db.execute(
                "INSERT INTO findings (analysis_id, version_id, created_at, severity, title, detail,"
                " evidence, suggested_action, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open')",
                (
                    aid,
                    version.id,
                    at,
                    severity,
                    str(f.get("title", "")),
                    str(f.get("detail", "")),
                    json.dumps(f.get("evidence") or [], ensure_ascii=False),
                    str(f.get("suggested_action", "")),
                ),
            )
        for fid in result.get("resolves") or []:
            self.db.execute(
                "UPDATE findings SET status = 'resolved', resolved_by = ? WHERE id = ? AND status = 'open'",
                (aid, int(fid)),
            )
        self.db.execute("UPDATE versions SET analysis_status = 'done' WHERE id = ?", (version.id,))
        self.db.commit()
        return aid

    def findings(self, status: str | None = "open", analysis_id: int | None = None) -> list[dict]:
        sql = (
            "SELECT f.*, v.path, v.sha256 FROM findings f JOIN versions v ON v.id = f.version_id"
            " WHERE 1=1"
        )
        args: list = []
        if status:
            sql += " AND f.status = ?"
            args.append(status)
        if analysis_id is not None:
            sql += " AND f.analysis_id = ?"
            args.append(analysis_id)
        sql += " ORDER BY f.id"
        rows = self.db.execute(sql, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["evidence"] = json.loads(d["evidence"])
            out.append(d)
        return out

    # --- change summaries and views --------------------------------------------------------

    def put_change(self, version_id: int, summary: str, diff: str) -> None:
        self.db.execute(
            "INSERT INTO changes (version_id, summary, diff, computed_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (version_id) DO UPDATE SET summary = excluded.summary, diff = excluded.diff,"
            " computed_at = excluded.computed_at",
            (version_id, summary, diff, now_iso()),
        )
        self.db.commit()

    def versions_without_change(self) -> list[Version]:
        rows = self.db.execute(
            "SELECT v.* FROM versions v LEFT JOIN changes c ON c.version_id = v.id"
            " WHERE c.version_id IS NULL AND v.change != 'baseline' ORDER BY v.id"
        ).fetchall()
        return [_version(r) for r in rows]

    def polls(self, limit: int = 200) -> list[dict]:
        rows = self.db.execute("SELECT * FROM polls ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def poll_versions(self, poll_id: int) -> list[dict]:
        rows = self.db.execute(
            "SELECT v.*, c.summary FROM versions v LEFT JOIN changes c ON c.version_id = v.id"
            " WHERE v.first_poll = ? AND v.change != 'baseline' ORDER BY v.path",
            (poll_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def poll_removals(self, poll: dict) -> list[str]:
        rows = self.db.execute(
            "SELECT path FROM versions v WHERE removed_at = ? AND NOT EXISTS"
            " (SELECT 1 FROM versions w WHERE w.path = v.path AND w.id > v.id)",
            (poll["at"],),
        ).fetchall()
        return [r["path"] for r in rows]

    def version_detail(self, version_id: int) -> dict | None:
        row = self.db.execute(
            "SELECT v.*, c.summary, c.diff FROM versions v LEFT JOIN changes c ON c.version_id = v.id"
            " WHERE v.id = ?",
            (version_id,),
        ).fetchone()
        return dict(row) if row else None

    def path_history(self, path: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT v.*, c.summary FROM versions v LEFT JOIN changes c ON c.version_id = v.id"
            " WHERE v.path = ? ORDER BY v.id DESC",
            (path,),
        ).fetchall()
        return [dict(r) for r in rows]

    def previous_version_dict(self, version_id: int) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM versions WHERE path = (SELECT path FROM versions WHERE id = ?) AND id < ?"
            " ORDER BY id DESC LIMIT 1",
            (version_id, version_id),
        ).fetchone()
        return dict(row) if row else None

    def analysis_for(self, version_id: int) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM analyses WHERE version_id = ? ORDER BY id DESC LIMIT 1", (version_id,)
        ).fetchone()
        return dict(row) if row else None


def severity_at_least(severity: str, minimum: str) -> bool:
    s = SEVERITIES.index(severity) if severity in SEVERITIES else 0
    m = SEVERITIES.index(minimum) if minimum in SEVERITIES else 0
    return s >= m


def _version(row: sqlite3.Row) -> Version:
    return Version(
        id=row["id"],
        path=row["path"],
        sha256=row["sha256"],
        size=row["size"],
        first_seen=row["first_seen"],
        change=row["change"],
        analysis_status=row["analysis_status"],
    )
