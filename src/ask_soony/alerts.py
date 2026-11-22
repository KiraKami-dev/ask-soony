"""
Alerts: turn a problem found by another module (verification, prediction, the watcher) into the
right notification for the right person, and escalate until someone decides.

- Criticality: a producer may set the severity; with a deadline the module also derives it
  (little time left on something that hits the launch = critical).
- Routing: an escalation policy (JSON, kept out of git because it holds phone numbers) says which
  channels each severity uses, who is called in which order and how long to wait for an answer.
- Decisions: a person approves, rejects or escalates through the dashboard, a phone keypad
  (Twilio) or a voice agent (ElevenLabs). Every step is logged per alert.

Nothing here knows about the customer's parts or suppliers: alerts carry their own text.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ask_soony.store import SEVERITIES, Store, now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL,            -- verification | prediction | watcher | manual
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,           -- what is wrong, in one or two sentences
    action TEXT NOT NULL,            -- what we propose to do about it
    deadline TEXT,                   -- when it becomes a problem (ISO 8601)
    impact TEXT NOT NULL,            -- launch | cost | data
    external INTEGER NOT NULL,       -- 1 if the action goes to a supplier
    status TEXT NOT NULL,            -- open | notified | approved | rejected | escalated | resolved
    decision_by TEXT,
    decision_at TEXT,
    token TEXT NOT NULL,             -- secret for decision links and call webhooks
    chain_index INTEGER NOT NULL DEFAULT 0,
    next_escalation_at TEXT
);
CREATE TABLE IF NOT EXISTS alert_events (
    id INTEGER PRIMARY KEY,
    alert_id INTEGER NOT NULL REFERENCES alerts(id),
    at TEXT NOT NULL,
    kind TEXT NOT NULL,              -- notified | call | decision | escalated | error
    channel TEXT NOT NULL,
    contact TEXT NOT NULL,
    detail TEXT NOT NULL
);
"""

DEFAULT_POLICY = {
    "dashboard_url": "",
    "critical_hours": 24,
    "contacts": [],
    "routes": {
        "critical": {"channels": ["dashboard", "email", "call"], "chain": [], "ack_minutes": 10},
        "high": {"channels": ["dashboard", "email"], "chain": [], "ack_minutes": 120},
        "medium": {"channels": ["dashboard"], "chain": [], "ack_minutes": 0},
        "low": {"channels": ["dashboard"], "chain": [], "ack_minutes": 0},
        "info": {"channels": ["dashboard"], "chain": [], "ack_minutes": 0},
    },
}

DECISIONS = {
    "approve": "approved",
    "reject": "rejected",
    "escalate": "escalated",
    "resolve": "resolved",
}


@dataclass
class Contact:
    id: str
    name: str
    role: str
    phone: str = ""
    email: str = ""


@dataclass
class Policy:
    dashboard_url: str = ""
    critical_hours: float = 24
    contacts: list[Contact] = field(default_factory=list)
    routes: dict = field(default_factory=lambda: dict(DEFAULT_POLICY["routes"]))

    @classmethod
    def load(cls, path: Path) -> "Policy":
        raw = json.loads(path.read_text()) if path.exists() else {}
        data = {**DEFAULT_POLICY, **raw}
        routes = {**DEFAULT_POLICY["routes"], **raw.get("routes", {})}
        return cls(
            dashboard_url=data.get("dashboard_url", "").rstrip("/"),
            critical_hours=float(data.get("critical_hours", 24)),
            contacts=[Contact(**c) for c in data.get("contacts", [])],
            routes=routes,
        )

    def contact(self, contact_id: str) -> Contact | None:
        return next((c for c in self.contacts if c.id == contact_id), None)

    def route(self, severity: str) -> dict:
        return self.routes.get(severity) or self.routes["info"]


@dataclass
class Alert:
    id: int
    created_at: str
    source: str
    severity: str
    title: str
    message: str
    action: str
    deadline: str | None
    impact: str
    external: bool
    status: str
    token: str
    chain_index: int
    next_escalation_at: str | None
    decision_by: str | None = None
    decision_at: str | None = None

    def spoken(self) -> str:
        """The text a phone call reads out: short, no IDs that are hard to say."""
        text = f"{self.title}. {self.message}"
        if self.action:
            text += f" Proposed action: {self.action}"
        return text


def classify(
    severity: str,
    deadline: str | None,
    impact: str,
    critical_hours: float,
    now: datetime | None = None,
) -> str:
    """
    Derive the severity: never lower than what the producer said, raised to critical when the
    launch is affected and less than `critical_hours` are left before the deadline.
    """
    severity = severity if severity in SEVERITIES else "medium"
    if impact == "launch" and deadline:
        now = now or datetime.now(UTC)
        left = datetime.fromisoformat(deadline) - now
        if left <= timedelta(hours=critical_hours):
            return "critical"
        if left <= timedelta(hours=critical_hours * 3) and SEVERITIES.index(
            severity
        ) < SEVERITIES.index("high"):
            return "high"
    return severity


class AlertBook:
    """Alert persistence in the same SQLite database as the version store."""

    def __init__(self, store: Store):
        self.store = store
        self.db = store.db
        self.db.executescript(SCHEMA)

    def raise_alert(
        self,
        *,
        title: str,
        message: str,
        action: str = "",
        severity: str = "medium",
        deadline: str | None = None,
        impact: str = "launch",
        external: bool = False,
        source: str = "manual",
        policy: Policy,
    ) -> Alert:
        level = classify(severity, deadline, impact, policy.critical_hours)
        aid = self.db.execute(
            "INSERT INTO alerts (created_at, source, severity, title, message, action, deadline, impact,"
            " external, status, token, chain_index) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, 0)",
            (
                now_iso(),
                source,
                level,
                title,
                message,
                action,
                deadline,
                impact,
                int(external),
                secrets.token_urlsafe(16),
            ),
        ).lastrowid
        self.db.commit()
        return self.get(aid)

    def get(self, alert_id: int) -> Alert | None:
        row = self.db.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["external"] = bool(d["external"])
        return Alert(**d)

    def list(self, open_only: bool = False) -> list[Alert]:
        sql = "SELECT id FROM alerts"
        if open_only:
            sql += " WHERE status IN ('open', 'notified', 'escalated')"
        return [self.get(r["id"]) for r in self.db.execute(sql + " ORDER BY id DESC").fetchall()]

    def log(self, alert_id: int, kind: str, channel: str, contact: str, detail: str) -> None:
        self.db.execute(
            "INSERT INTO alert_events (alert_id, at, kind, channel, contact, detail) VALUES (?, ?, ?, ?, ?, ?)",
            (alert_id, now_iso(), kind, channel, contact, detail),
        )
        self.db.commit()

    def events(self, alert_id: int) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM alert_events WHERE alert_id = ? ORDER BY id", (alert_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def update(self, alert_id: int, **fields) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE alerts SET {cols} WHERE id = ?", (*fields.values(), alert_id))
        self.db.commit()

    def decide(
        self, alert_id: int, decision: str, by: str, channel: str, token: str | None = None
    ) -> Alert:
        alert = self.get(alert_id)
        if alert is None:
            raise KeyError(alert_id)
        if token is not None and not secrets.compare_digest(token, alert.token):
            raise PermissionError("invalid alert token")
        if decision not in DECISIONS:
            raise ValueError(f"decision must be one of {', '.join(DECISIONS)}")
        status = DECISIONS[decision]
        fields = {"status": status, "decision_by": by, "decision_at": now_iso()}
        if decision == "escalate":
            fields["chain_index"] = alert.chain_index + 1
            fields["next_escalation_at"] = now_iso()  # escalate right away
        else:
            fields["next_escalation_at"] = None
        self.update(alert_id, **fields)
        self.log(alert_id, "decision", channel, by, decision)
        return self.get(alert_id)

    def as_dict(self, alert: Alert) -> dict:
        d = asdict(alert)
        d.pop("token")
        return d
