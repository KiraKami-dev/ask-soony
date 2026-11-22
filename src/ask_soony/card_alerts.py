"""
Bridge between today's cases (api.build_state cards) and the phone alert stack (AlertBook,
Escalator, the call providers).

- Raise: an open case that matters (level red, or its deadline within ASK_CALL_WITHIN_MIN
  minutes) and that nobody touched for ASK_CALL_AFTER_MIN minutes since this server first saw
  it becomes one alert (source "card"), notified along the alert policy's chain like any other.
  At most ASK_CALL_MAX_OPEN case alerts are open at a time (default 1: one call at a time).
- Apply: a decision on such an alert (call tool, Twilio keypad, ElevenLabs hook) goes to the
  case through the inbox's own approve / escalate / dismiss, with via "call". Right after
  AlertBook.decide, and again in every loop pass for decisions the server missed.
- Close: when the case is decided elsewhere (web, voice, routine, sign-off), its alert is
  resolved, so the chain stops calling.

Links live in the alerts database (table card_alerts), keyed by the state folder too: the demo
and the live instance share one store but not their cases. What happened on the phone is kept
in the state folder (phone_decisions.json) and shown on the case's trail.
"""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ask_soony.alerts import Alert, AlertBook, Policy

SOURCE = "card"
PHONE_FILE = "phone_decisions.json"
VIA = "call"
OPEN_ALERT = ("open", "notified", "escalated")

SCHEMA = """
CREATE TABLE IF NOT EXISTS card_alerts (
    alert_id INTEGER PRIMARY KEY,
    state_dir TEXT NOT NULL,
    card_id TEXT NOT NULL,
    raised_at TEXT NOT NULL,
    applied_at TEXT,                 -- set once the alert's decision reached the case
    result TEXT,
    UNIQUE (state_dir, card_id)
);
CREATE TABLE IF NOT EXISTS card_seen (
    state_dir TEXT NOT NULL,
    card_id TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    PRIMARY KEY (state_dir, card_id)
);
"""


def _api():
    from ask_soony import api

    return api


def _env_min(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _state_key() -> str:
    return str(_api().state_dir().resolve())


def _db(book: AlertBook) -> sqlite3.Connection:
    book.db.executescript(SCHEMA)
    return book.db


def link_of(book: AlertBook, alert_id: int) -> dict | None:
    row = _db(book).execute("SELECT * FROM card_alerts WHERE alert_id = ?", (alert_id,)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------- what an alert says

_CODE = re.compile(r"\s*\((?:[A-Z]{2,5}-)+[A-Z0-9-]+\)")


_ISO_DAY = re.compile(r"\b(\d{4}-\d{2}-\d{2})(?:T[\d:.]+(?:Z|[+-]\d{2}:\d{2})?)?\b")


def _plain(text: Any) -> str:
    """Readable aloud: no bracketed internal codes, ISO dates as "Sunday 22 November"."""
    out = _CODE.sub("", str(text or ""))
    out = _ISO_DAY.sub(lambda m: datetime.fromisoformat(m.group(1)).strftime("%A %d %B"), out)
    return " ".join(out.split())


def recommended(card: dict) -> int:
    opts = card.get("options") or []
    return next((i for i, o in enumerate(opts) if o.get("recommended")), 0)


def matters(card: dict, now: datetime, within_min: float) -> bool:
    if card.get("level") == "red":
        return True
    deadline = _api().parse_when(card.get("deadline"))
    return bool(deadline and now <= deadline <= now + timedelta(minutes=within_min))


def alert_text(card: dict, now: datetime | None = None) -> dict:
    """Headline, the case in one sentence with its deadline, the recommendation with its cost."""
    api = _api()
    title = _plain(card.get("headline") or card["id"])[:200]
    summary = _plain(card.get("summary"))
    deadline = api.parse_when(card.get("deadline"))
    if deadline:
        local, cur = deadline.astimezone(api.tz()), (now or api.now()).astimezone(api.tz())
        day = "today" if local.date() == cur.date() else f"{local:%A}"
        when = (
            f"It was due {day} at {local:%H:%M}."
            if local < cur
            else f"Decide by {day} at {local:%H:%M}."
        )
        summary = f"{summary} {when}".strip()
    action = ""
    opts = card.get("options") or []
    if opts:
        opt = opts[recommended(card)]
        action = _plain(opt.get("action")).rstrip(".")
        cost = str(opt.get("cost") or "").strip()
        if cost and cost.lower() not in ("none", "0", "free", "no cost", "-"):
            action += f". Cost: {_plain(cost)}"
        elif action:
            action += ". No cost"
        action += "."
    return {"title": title, "message": summary or title, "action": action}


# ---------------------------------------------------------------- raise and close


def tick(book: AlertBook, policy: Policy, notify, now: datetime | None = None) -> dict:
    """
    One pass: apply missed decisions, close alerts of cases decided elsewhere, raise new ones.
    `notify(alert)` sends a new alert down its chain (Escalator.notify). Returns counts.
    """
    api = _api()
    now = now or api.now()
    key = _state_key()
    db = _db(book)
    out = {"applied": 0, "closed": 0, "raised": 0}
    out["applied"] = reconcile(book)
    cards = {c["id"]: c for c in api.build_state()["cards"]}
    stamp = now.isoformat(timespec="seconds")

    links = db.execute(
        "SELECT * FROM card_alerts WHERE state_dir = ? AND applied_at IS NULL", (key,)
    ).fetchall()
    for link in links:
        alert = book.get(link["alert_id"])
        card = cards.get(link["card_id"])
        if alert is None or alert.status not in OPEN_ALERT or alert.decision_by:
            continue
        if card is None or card.get("status") != "open":
            status = (card or {}).get("status") or "gone"
            book.update(alert.id, status="resolved", next_escalation_at=None)
            book.log(alert.id, "decision", "-", "-", f"case decided elsewhere ({status})")
            db.execute(
                "UPDATE card_alerts SET applied_at = ?, result = ? WHERE alert_id = ?",
                (stamp, f"closed: {status} elsewhere", alert.id),
            )
            db.commit()
            out["closed"] += 1

    after = _env_min("ASK_CALL_AFTER_MIN", 15)
    within = _env_min("ASK_CALL_WITHIN_MIN", 120)
    gap = timedelta(minutes=float(policy.route("critical").get("ack_minutes") or 10))
    room = int(_env_min("ASK_CALL_MAX_OPEN", 1)) - _open_count(book, key, now - gap)
    linked = {
        r["card_id"]
        for r in db.execute("SELECT card_id FROM card_alerts WHERE state_dir = ?", (key,))
    }
    for c in cards.values():  # build_state ranks the cards: the most urgent first
        if c.get("status") != "open" or c["id"] in linked:
            continue
        db.execute(
            "INSERT OR IGNORE INTO card_seen (state_dir, card_id, first_seen) VALUES (?, ?, ?)",
            (key, c["id"], stamp),
        )
        db.commit()
        if room <= 0 or c.get("signoff_request") or not matters(c, now, within):
            continue
        seen = db.execute(
            "SELECT first_seen FROM card_seen WHERE state_dir = ? AND card_id = ?", (key, c["id"])
        ).fetchone()["first_seen"]
        if now - datetime.fromisoformat(seen) < timedelta(minutes=after):
            continue
        alert = raise_for(book, policy, c, now)
        if alert is None:
            continue
        notify(alert)
        room -= 1
        out["raised"] += 1
    return out


def _open_count(book: AlertBook, key: str, recent: datetime) -> int:
    """
    Case alerts still walking their chain: raised after `recent` (one answer time) or with a
    next call due. An exhausted chain stays decidable but no longer holds the next case back.
    """
    n = 0
    for r in book.db.execute(
        "SELECT alert_id, raised_at FROM card_alerts WHERE state_dir = ? AND applied_at IS NULL",
        (key,),
    ):
        a = book.get(r["alert_id"])
        if not a or a.status not in OPEN_ALERT or a.decision_by:
            continue
        if a.next_escalation_at or datetime.fromisoformat(r["raised_at"]) > recent:
            n += 1
    return n


def raise_for(book: AlertBook, policy: Policy, card: dict, now: datetime) -> Alert | None:
    """One alert per case and state folder; None if the case already has one."""
    db = _db(book)
    key = _state_key()
    if db.execute(
        "SELECT 1 FROM card_alerts WHERE state_dir = ? AND card_id = ?", (key, card["id"])
    ).fetchone():
        return None
    text = alert_text(card, now)
    deadline = _api().parse_when(card.get("deadline"))
    alert = book.raise_alert(
        **text,
        severity="critical",
        deadline=deadline.isoformat() if deadline else None,
        impact="launch",
        external=any(o.get("channel") == "email" for o in card.get("options") or []),
        source=SOURCE,
        policy=policy,
    )
    try:
        db.execute(
            "INSERT INTO card_alerts (alert_id, state_dir, card_id, raised_at) VALUES (?, ?, ?, ?)",
            (alert.id, key, card["id"], now.isoformat(timespec="seconds")),
        )
        db.commit()
    except sqlite3.IntegrityError:  # another pass linked this case first
        book.update(alert.id, status="resolved", next_escalation_at=None)
        return None
    book.log(alert.id, "notified", "-", "-", f"raised for case {card['id']}")
    return alert


# ---------------------------------------------------------------- apply decisions


def _caller_name(book: AlertBook, alert: Alert) -> str:
    """The person the call went to (last call event), else whoever the decision names."""
    from ask_soony.config import Settings

    calls = [e for e in book.events(alert.id) if e["kind"] == "call"]
    if calls:
        c = Policy.load(Settings.from_env().alert_policy).contact(calls[-1]["contact"])
        if c:
            return c.name
    by = alert.decision_by or "someone"
    m = re.match(r"voice \((.+)\)$", by)
    return m.group(1) if m else by


def _phone_note(card_id: str, entry: dict) -> None:
    api = _api()
    with api._lock:
        data = api.read_json(PHONE_FILE, {})
        data[card_id] = entry
        api.write_json(PHONE_FILE, data)


def apply(book: AlertBook, alert_id: int) -> str | None:
    """
    Carry a decided case alert over to its case, exactly once. None when there is nothing to do
    (no link, not decided, already applied, or a case of another state folder).
    """
    link = link_of(book, alert_id)
    alert = book.get(alert_id)
    if not link or not alert or link["applied_at"] or link["state_dir"] != _state_key():
        return None
    decision = {"approved": "approve", "escalated": "escalate", "rejected": "reject"}.get(
        alert.status
    )
    if not decision or not alert.decision_by:
        return None
    api = _api()
    stamp = api.now().isoformat(timespec="seconds")
    claim = book.db.execute(
        "UPDATE card_alerts SET applied_at = ? WHERE alert_id = ? AND applied_at IS NULL",
        (stamp, alert_id),
    )
    book.db.commit()
    if claim.rowcount != 1:  # someone else applied it in the meantime
        return None
    # A decision on a case alert ends its chain: nobody else is called about it.
    book.update(alert_id, next_escalation_at=None)
    who = _caller_name(book, alert)
    result = _carry_out(link["card_id"], decision, who)
    book.db.execute("UPDATE card_alerts SET result = ? WHERE alert_id = ?", (result, alert_id))
    book.db.commit()
    book.log(alert_id, "decision", VIA, who, f"case {link['card_id']}: {result}")
    _phone_note(
        link["card_id"],
        {"alert_id": alert_id, "by": who, "at": stamp, "decision": decision, "result": result},
    )
    return result


def _carry_out(card_id: str, decision: str, who: str) -> str:
    from fastapi import HTTPException

    from ask_soony import signoff

    api = _api()
    try:
        card = api._card(card_id)
    except HTTPException:
        return "case no longer exists"
    if card.get("status") != "open":
        return f"already {card.get('status')} elsewhere; nothing changed"
    try:
        if decision == "escalate":
            api.escalate(card_id, {"note": "Passed on from a phone call", "via": VIA})
            return "passed on"
        if decision == "reject":
            api.dismiss(card_id, {"note": "Rejected on a phone call", "via": VIA})
            return "dismissed"
        idx = recommended(card)
        # A case someone else must sign (`signoff`: routing put money and decision messages with
        # them): a yes on the phone from anyone else asks the signer, it does not carry it out.
        signer = card.get("signoff")
        if signer and not signoff._same(signer, who):
            api.escalate(
                card_id,
                {
                    "option": idx,
                    "note": f"Approved on a phone call by {who}; needs your sign-off",
                    "via": VIA,
                },
            )
            return f"sent to {signer} for sign-off"
        out = api.approve(card_id, {"option": idx, "via": VIA, "by": who})
        return str(out["action"].get("status") or "approved")
    except HTTPException as e:
        if e.status_code == 409:  # the same email went out by hand already: never force it
            return "already sent by hand; nothing sent again"
        return f"not done: {e.detail}"


def reconcile(book: AlertBook) -> int:
    """Apply every decided case alert that has not reached its case yet. Returns the count."""
    rows = (
        _db(book)
        .execute(
            "SELECT alert_id FROM card_alerts WHERE state_dir = ? AND applied_at IS NULL",
            (_state_key(),),
        )
        .fetchall()
    )
    return sum(1 for r in rows if apply(book, r["alert_id"]) is not None)


def on_decided(book: AlertBook, alert: Alert) -> None:
    """Hook for AlertBook.decide: a case alert's decision goes to the case right away."""
    if alert.source != SOURCE:
        return
    try:
        apply(book, alert.id)
    except Exception as e:  # the loop's reconcile pass tries again; never fail the webhook
        book.log(alert.id, "error", VIA, "-", f"applying to the case failed: {str(e)[:250]}")


# ---------------------------------------------------------------- state view


def attach(cards: list[dict]) -> None:
    """Add "Decided on the phone by <name> at HH:MM" to the trail of cases decided on a call."""
    api = _api()
    data = api.read_json(PHONE_FILE, {})
    if not isinstance(data, dict) or not data:
        return
    for c in cards:
        e = data.get(c["id"])
        if not e:
            continue
        at = api.parse_when(e.get("at"))
        hhmm = f" at {at.astimezone(api.tz()):%H:%M}" if at else ""
        c["trail"] = [
            *(c.get("trail") or []),
            {
                "agent": "Phone call",
                "at": e.get("at"),
                "text": f"Decided on the phone by {e.get('by')}{hhmm}: {e.get('result')}.",
            },
        ]


def forget(state_dir: str) -> int:
    """
    Drop the links of one state folder (a demo reset starts its cases afresh); their open
    alerts are resolved so nobody is called about them. Returns the number of links dropped.
    """
    from ask_soony.config import Settings
    from ask_soony.store import Store

    key = str(Path(state_dir).resolve())
    store = Store(Settings.from_env().data_dir)
    try:
        book = AlertBook(store)
        db = _db(book)
        rows = db.execute("SELECT alert_id FROM card_alerts WHERE state_dir = ?", (key,)).fetchall()
        for r in rows:
            a = book.get(r["alert_id"])
            if a and a.status in OPEN_ALERT:
                book.update(a.id, status="resolved", next_escalation_at=None)
                book.log(a.id, "decision", "-", "-", "state reset")
        db.execute("DELETE FROM card_alerts WHERE state_dir = ?", (key,))
        db.execute("DELETE FROM card_seen WHERE state_dir = ?", (key,))
        db.commit()
        return len(rows)
    finally:
        store.db.close()


if __name__ == "__main__":  # python -m ask_soony.card_alerts forget <state dir>
    import sys

    if len(sys.argv) == 3 and sys.argv[1] == "forget":
        print(f"case alerts: dropped {forget(sys.argv[2])} links of {sys.argv[2]}")
    else:
        sys.exit("usage: python -m ask_soony.card_alerts forget <state dir>")
