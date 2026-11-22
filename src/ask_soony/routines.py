"""
Routines: after a person decided something by hand, an agent offers to do it automatically next
time. It writes a small Python routine from a fixed set of building blocks, which runs in the
Monty sandbox (pydantic-monty) on every check: no network, no files, no imports except the
`soony` module below, type-checked against its stubs before it is saved.

The trust ladder stays: a routine can carry out an option only if it just communicates (an email
or a message to a person) and costs nothing. Re-plans, allocations, orders and anything with a
price still go to a person; the routine can only notify about them. Every run
is recorded with what it printed and what it did, and every action it takes lands in the inbox
history like a click, marked with the routine's name.

A routine watches one of four things (recipe "on"): open decisions, decisions sent to a supplier
that wait for an answer, decisions whose supplier answered, or shipments that stopped updating.
Each routine has a trust mode: "ask" turns every outgoing action (carry out, chase, ask for a
status, call) into a proposal the buyer accepts or skips; "tell" acts and pushes one line per
action to the buyer's phone; "quiet" acts and only logs. `enabled` stays the on/off switch.

Building blocks (module `soony`):
  open_cards() -> list[dict]         open decisions: id, headline, level, kind, part, supplier,
                                     number, deadline, hours_left, has_reply, evidence, options
  waiting_cards() -> list[dict]      sent decisions without a reply: hours_since_sent, to, subject
  replied_cards() -> list[dict]      sent decisions with a reply: reply, reply_date, ...
  silent_shipments() -> list[dict]   shipments with hours since their last update
  carry_out(card_id, option_index, reason) -> dict   approve an option (sends its email if any)
  chase(card_id, message) -> dict    one follow-up in the same email thread to the same supplier
  ask_status(shipment_id, message) -> dict   ask the shipment's supplier for its status
  call(text, card_id) -> dict        phone the responsible person (alert/escalation path)
  notify(text, card_id) -> None      push and Teams
  log(text) -> None                  a line in the run record
  first_time(key) -> bool            True once per key per routine (avoid repeating yourself)
  now() -> str                       current time, ISO 8601
"""

import csv
import io
import json
import logging
import os
import re
import threading
import time
import uuid

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ask_soony.config import load_dotenv

log = logging.getLogger("ask_soony.routines")
router = APIRouter()
_lock = threading.Lock()
_plock = threading.Lock()  # routine_proposals.json

MODES = ("ask", "tell", "quiet")
PROPOSALS = "routine_proposals.json"

STUBS = '''
def open_cards() -> list[dict]:
    """Open decisions. Keys: id, headline, level ('red'|'amber'|'grey'|'blue'), kind, part,
    supplier_id, supplier_name, number (copies at risk or units short, may be None), deadline,
    hours_left (hours until the decide-by, may be None), has_reply (bool), reply_after_launch
    (bool), evidence (list of {'source': str, 'value': str}), options (list of {'index': int,
    'action': str, 'cost': str | None, 'free': bool, 'channel': str | None, 'to': str | None,
    'routine_type': str | None}). `free` is True only for an option that just sends a message
    (email or note) and costs nothing; re-plans, allocations and orders are never free."""
    ...

def waiting_cards() -> list[dict]:
    """Decisions already sent to a supplier with no reply yet: the open_cards() keys plus
    hours_since_sent (= waiting_hours), to, subject, chases (follow-ups sent so far)."""
    ...

def replied_cards() -> list[dict]:
    """Sent decisions the supplier answered: the open_cards() keys plus reply (summary),
    reply_date (ISO or None), reply_quantity (number or None), reply_after_launch (bool)."""
    ...

def silent_shipments() -> list[dict]:
    """Open shipments with id, supplier_id, supplier_name, silent_hours (hours since their last
    update, relative to the newest update of any shipment), silent (past the silence
    threshold), eta, status."""
    ...

def carry_out(card_id: str, option_index: int, reason: str) -> dict:
    """Approve one option of a card, exactly like the Approve button (an email option to a known
    supplier is sent). Refused for options that cost money: notify a person instead.
    Returns {'ok': bool, 'status': str, 'error': str | None}."""
    ...

def chase(card_id: str, message: str) -> dict:
    """Send one polite follow-up in the same email thread to the supplier the decision was sent
    to. Returns {'ok': bool, 'status': str, 'error': str | None}."""
    ...

def ask_status(shipment_id: str, message: str) -> dict:
    """Email the shipment's supplier contact (from its profile) asking for a status.
    Returns {'ok': bool, 'status': str, 'error': str | None}."""
    ...

def call(text: str, card_id: str, who: str = "responsible") -> dict:
    """Phone the person responsible for the decision (who='responsible') or the buyer
    (who='me') and read out `text`.
    Returns {'ok': bool, 'status': str, 'error': str | None}."""
    ...

def notify(text: str, card_id: str, channel: str) -> None:
    """Tell the buyer: channel 'phone' (push), 'teams' (Teams channel) or 'both'."""
    ...

def log(text: str) -> None:
    """Write one line to this run's record."""
    ...

def first_time(key: str) -> bool:
    """True the first time this routine sees `key`, False afterwards."""
    ...

def now() -> str:
    """Current time, ISO 8601."""
    ...
'''

WRITER_PROMPT = """You turn a buyer's decision into an automation routine for a procurement
decision inbox. The buyer just decided a card by hand. Write a routine that handles SIMILAR
future cards the same way, so they do not have to do it again. Generalise from this card: match
on the situation (words in the evidence, the proposed action and its recipient role), never on
this card's id, part or supplier unless the decision clearly depends on them. Do not match on
the urgency colour: it changes over time. If the buyer sent an email to a supplier, a follow-up
routine ("on": "waiting") that chases once and calls the buyer later is often what they want
next; only write it if the decision suggests it.

{spec_help}

The card the buyer decided:
{card}

What the buyer did: {action}

Answer with JSON only: {{"name": "<4-7 words>", "description": "<one or two plain sentences a
buyer understands: when it triggers and what it does>", "spec": <the recipe>}}

Name, description and every "text" are read by a buyer, not a developer: describe the business
situation and the action ("When a container sits at the port ready for pick-up, tell the DC
site lead to collect it"). Never use internal field names or codes such as amber, red, level,
kind, mismatch, routine_type, channel, option, card, waiting_hours or on."""

EDIT_PROMPT = """You change an automation routine of a procurement decision inbox exactly as the
buyer asks, and nothing else.

{spec_help}

Current routine:
{routine}

The buyer asks: "{instruction}"

Keep the ids of unchanged conditions and steps; give new ones new ids. If the request cannot be
expressed with the fields above (for example a filter the data does not have), leave the recipe
unchanged and say so in "note". Answer with JSON only: {{"name": "...", "description": "...",
"spec": <the new recipe>, "note": "<empty, or one sentence for the buyer>"}}
Plain buyer language only, no internal field names or codes."""


# ---------------------------------------------------------------- storage


def _all() -> list[dict]:
    from ask_soony import api

    return api.read_list("routines.json")


def _save(items: list[dict]) -> None:
    from ask_soony import api

    api.write_json("routines.json", items)
    _would_cache.clear()


def _get(rid: str) -> dict:
    r = next((x for x in _all() if x.get("id") == rid), None)
    if r is None:
        raise HTTPException(404, "unknown routine")
    return r


def mode_of(routine: dict) -> str:
    m = routine.get("mode")
    return m if m in MODES else "tell"


# ---------------------------------------------------------------- building blocks


def _hours(a, b) -> float | None:
    """Hours from datetime a to datetime b, one decimal; None if either is missing."""
    if a is None or b is None:
        return None
    return round((b - a).total_seconds() / 3600, 1)


def _card_view(c: dict, cur=None) -> dict:
    from ask_soony import api

    ui = c.get("ui") or {}
    cur = cur or api.now()
    return {
        "id": c["id"],
        "headline": c.get("headline"),
        "level": c.get("level"),
        "kind": c.get("kind"),
        "part": c.get("part"),
        "supplier_id": c.get("supplier_id"),
        "supplier_name": c.get("supplier_name"),
        "number": c.get("number"),
        "deadline": c.get("deadline"),
        "status": c.get("status"),
        "hours_left": _hours(cur, api.parse_ts(c.get("deadline"))),
        "has_reply": bool(ui.get("reply")),
        "reply": ui.get("reply"),
        "reply_date": ui.get("reply_date"),
        "reply_quantity": ui.get("reply_quantity"),
        "reply_after_launch": ui.get("reply_after_launch") is True,
        "evidence": [
            {
                "source": str(e.get("source") or ""),
                "value": str(e.get("value") or e.get("quote") or ""),
            }
            for e in c.get("evidence") or []
        ],
        "options": [
            {
                "index": o.get("index", i),
                "action": o.get("action"),
                "cost": o.get("cost"),
                "free": automatable(o),
                "channel": o.get("channel"),
                "to": o.get("to"),
                "routine_type": o.get("routine_type"),
            }
            for i, o in enumerate(c.get("options") or [])
        ],
    }


def _costs_money(option: dict) -> bool:
    cost = str(option.get("cost") or "").strip().lower()
    return bool(cost) and cost not in ("none", "0", "free", "no cost", "-")


# Routines may only carry out options that communicate (an email or a message to a person).
# Re-plans, allocations and orders change the plan or commit money: never automatic, whatever
# their cost text says.
AUTOMATABLE_CHANNELS = {"email", "notify"}


def automatable(option: dict) -> bool:
    return option.get("channel") in AUTOMATABLE_CHANNELS and not _costs_money(option)


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def supplier_email(supplier_id: str | None) -> str | None:
    """The contact address of one supplier, from its profile in the portal (CSV or any text)."""
    from ask_soony import api

    root = api.portal_root()
    if root is None or not supplier_id:
        return None
    for path in sorted((root / "suppliers").glob("*/profile.*")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if supplier_id not in text and not path.parent.name.startswith(supplier_id):
            continue
        if path.suffix.lower() == ".csv":
            for row in csv.DictReader(io.StringIO(text)):
                if (row.get("supplier_id") or "").strip() == supplier_id:
                    for v in row.values():
                        if v and _EMAIL.fullmatch(v.strip()):
                            return v.strip().lower()
        m = _EMAIL.search(text)
        if m:
            return m.group(0).lower()
    return None


def _push(title: str, body: str, card_id: str | None = None) -> None:
    try:
        from ask_soony import push

        push.send(title, body, card_id or None)
    except Exception:  # no devices or no keys: the action is in the history anyway
        pass


def _append_action(action: dict) -> dict:
    from ask_soony import api

    action = {"id": uuid.uuid4().hex[:10], "at": api.now().isoformat(), **action}
    with api._lock:
        actions = api.read_list("actions.json")
        actions.append(action)
        api.write_json("actions.json", actions)
    return action


def _place_call(text: str, card: dict | None, who: str) -> dict:
    """
    Phone one person through the alert path (AlertBook + make_caller, provider from
    ASK_CALL_PROVIDER: dry_run unless configured). `who` is "me" (the buyer) or "responsible"
    (the card's approver). Returns {contact, detail, alert_id, dialled}.
    """
    from ask_soony import api
    from ask_soony.alerts import AlertBook, Policy
    from ask_soony.config import Settings
    from ask_soony.escalation import make_caller
    from ask_soony.store import Store

    settings = Settings.from_env()
    policy = Policy.load(settings.alert_policy)
    person = (card or {}).get("approver") if who != "me" else None
    person = person or api.viewer_name(api.portal_root())
    first = api.first_name(person).lower() if person else ""
    contact = next(
        (c for c in policy.contacts if first and api.first_name(c.name).lower() == first), None
    )
    if contact is None:
        chain = policy.route("critical").get("chain") or [c.id for c in policy.contacts]
        contact = next((policy.contact(i) for i in chain if policy.contact(i)), None)
    store = Store(settings.data_dir)
    try:
        book = AlertBook(store)
        alert = book.raise_alert(
            title=str((card or {}).get("headline") or "Launch routine")[:200],
            message=text,
            severity="critical",
            deadline=None,
            source="routine",
            policy=policy,
        )
        caller = make_caller(settings.call_provider)
        if contact is None or not contact.phone:
            detail, dialled = f"no phone number for {person or 'the buyer'}", False
        else:
            detail = caller.call(alert, contact, settings.public_url)
            dialled = caller.name != "dry_run"
        book.log(alert.id, "call", "call", contact.id if contact else "-", detail)
        return {
            "contact": contact.name if contact else person,
            "detail": detail,
            "alert_id": alert.id,
            "dialled": dialled,
        }
    finally:
        store.db.close()


def _ok(status: str, **extra) -> dict:
    return {"ok": True, "status": status, "error": None, **extra}


def _no(error: str, status: str = "open") -> dict:
    return {"ok": False, "status": status, "error": error}


def _fill(template: str, item: dict) -> str:
    return (
        template.replace("{headline}", str(item.get("headline") or ""))
        .replace("{supplier}", str(item.get("supplier_name") or "the supplier"))
        .replace("{shipment}", str(item.get("shipment_id") or item.get("id") or ""))
        .replace("{eta}", str(item.get("eta") or ""))
    )


class _Run:
    """
    The building blocks for one run. In dry mode side effects are only recorded; in "ask" mode
    they become proposals; `accepting` executes a proposal (no new proposal, no push).
    """

    def __init__(self, routine: dict, dry: bool, state: dict | None = None):
        from ask_soony import api

        self.routine, self.dry = routine, dry
        self.mode = mode_of(routine)
        self.accepting = False
        self.lines: list[str] = []
        self.did: list[dict] = []
        self.state = state if state is not None else api.build_state()
        self.cur = api.now()
        self.cards = [
            c
            for c in self.state["cards"]
            if c["id"] in self.state["inbox_ids"] and c["status"] == "open"
        ]
        self.seen = set(routine.get("seen") or [])
        self._actions: list[dict] | None = None
        self.chased: set[str] = set()

    @property
    def name(self) -> str:
        return str(self.routine.get("name") or "Routine")

    @property
    def via(self) -> str:
        return f"routine: {self.name}"

    def actions(self) -> list[dict]:
        if self._actions is None:
            from ask_soony import api

            self._actions = api.read_list("actions.json")
        return self._actions

    # -- reading

    def open_cards(self) -> list[dict]:
        return [_card_view(c, self.cur) for c in self.cards]

    def _thread(self, card_id: str) -> list[dict]:
        """Messages we sent on this card (the approved email and any follow-ups), oldest first."""
        return [
            a
            for a in self.actions()
            if str(a.get("decision_id")) == card_id
            and a.get("message_id")
            and a.get("kind") in ("approve", "chase")
        ]

    def _sent_cards(self, replied: bool) -> list[dict]:
        from ask_soony import api

        out = []
        for c in self.state["cards"]:
            ui = c.get("ui") or {}
            if c.get("status") != "sent" or bool(ui.get("reply")) != replied:
                continue
            thread = self._thread(c["id"])
            first = next((a for a in thread if a.get("kind") == "approve"), None)
            if first is None:
                continue
            waited = _hours(api.parse_ts(first.get("at")), self.cur) or 0.0
            out.append(
                {
                    **_card_view(c, self.cur),
                    "hours_since_sent": waited,
                    "waiting_hours": waited,
                    "to": first.get("to"),
                    "subject": first.get("subject"),
                    "chases": sum(1 for a in thread if a.get("kind") == "chase"),
                    "clock_hours": waited,
                }
            )
        return out

    def waiting_cards(self) -> list[dict]:
        return self._sent_cards(replied=False)

    def replied_cards(self) -> list[dict]:
        rows = self._sent_cards(replied=True)
        for r in rows:
            r["clock_hours"] = self.cur.timestamp() / 3600
        return rows

    def silent_shipments(self) -> list[dict]:
        from ask_soony import api

        ships = self.state.get("shipments") or []
        upds = [api.parse_ts(s.get("updated_at")) for s in ships]
        ref = max((u for u in upds if u), default=None)
        names = {
            n.get("id"): n.get("name") for n in (self.state.get("map") or {}).get("nodes") or []
        }
        out = []
        for s, upd in zip(ships, upds, strict=True):
            hours = s.get("silent_hours")
            if hours is None:
                hours = _hours(upd, ref)
            if hours is None:
                continue
            out.append(
                {
                    "id": s.get("id"),
                    "shipment_id": s.get("id"),
                    "headline": f"Shipment {s.get('id')}",
                    "kind": "shipment",
                    "supplier_id": s.get("supplier_id"),
                    "supplier_name": names.get(s.get("supplier_id")),
                    "silent_hours": float(hours),
                    "silent": bool(s.get("silent")),
                    "eta": s.get("eta"),
                    "status": s.get("status"),
                    "mode": s.get("mode"),
                    "updated_at": s.get("updated_at"),
                    "clock_hours": float(hours),
                }
            )
        return out

    # -- acting

    def _side(self, step: dict, preview: str, execute, line: str, card_id: str = "") -> dict:
        """One outgoing action: record it (dry), propose it (ask) or do it (tell, quiet)."""
        if self.dry:
            self.did.append({**step, "dry": True, "preview": preview})
            verb = "approve" if step["do"] == "carry_out" else step["do"].replace("_", " ")
            return _ok("would " + verb)
        if self.mode == "ask" and not self.accepting:
            pid = propose(self.routine, step, preview)
            self.did.append({**step, "proposed": pid})
            return _ok("proposed", proposal_id=pid)
        out = execute()
        self.did.append(
            {**step, "status": out.get("status"), "action_id": out.get("action_id"), "dry": False}
        )
        if out.get("ok") and self.mode == "tell" and not self.accepting:
            _push(f"Routine: {self.name}", line, card_id or None)
        return out

    def carry_out(self, card_id: str, option_index: int, reason: str) -> dict:
        from ask_soony import api

        card = next((c for c in self.cards if c["id"] == card_id), None)
        if card is None:
            return _no("not an open card", "unknown")
        options = card.get("options") or []
        if not 0 <= int(option_index) < len(options):
            return _no("no such option")
        opt = options[int(option_index)]
        if not automatable(opt):
            why = "costs money" if _costs_money(opt) else "changes the plan"
            return _no(f"{why}: a person decides")
        step = {
            "do": "carry_out",
            "card": card_id,
            "option": opt.get("action"),
            "option_index": int(option_index),
            "reason": reason,
            "text": reason,
        }
        draft = str(opt.get("draft") or "")
        preview = f"{opt.get('action')}" + (f"\nTo {opt.get('to')}:\n{draft}" if draft else "")

        def execute() -> dict:
            out = api.approve(card_id, {"option": int(option_index), "via": self.via})
            return _ok(str(out["action"].get("status")), action_id=out["action"].get("id"))

        return self._side(
            step, preview, execute, f"{opt.get('action')}: {card.get('headline')}", card_id
        )

    def chase(self, card_id: str, message: str) -> dict:
        from ask_soony import api, cardtools

        thread = self._thread(card_id)
        first = next((a for a in thread if a.get("kind") == "approve"), None)
        card = next((c for c in self.state["cards"] if c["id"] == card_id), None)
        if first is None or card is None:
            return _no("nothing was sent to a supplier on this decision yet", "unknown")
        if card_id in self.chased:
            return _no("already chased in this run", "sent")
        to = str(first.get("to") or "").strip().lower()
        if to not in cardtools.supplier_addresses():
            return _no("not a known supplier address", "sent")
        self.chased.add(card_id)
        body = str(message).strip()
        subject = str(first.get("subject") or card.get("headline") or "Soony procurement")
        step = {"do": "chase", "card": card_id, "to": to, "message": body, "text": "Chase"}

        def execute() -> dict:
            refs = [str(a["message_id"]) for a in thread]
            sent = cardtools.send_followup(to, subject, body, refs[-1], refs)
            act = _append_action(
                {
                    "decision_id": card_id,
                    "kind": "chase",
                    "channel": "email",
                    "to": to,
                    "subject": sent["subject"],
                    "draft": body,
                    "status": "sent",
                    "message_id": sent["message_id"],
                    "in_reply_to": refs[-1],
                    "via": self.via,
                }
            )
            with api._lock:
                sts = api.read_json("decision_status.json", {})
                if card_id in sts:
                    sts[card_id]["chased_at"] = act["at"]
                    sts[card_id]["chases"] = int(sts[card_id].get("chases") or 0) + 1
                    api.write_json("decision_status.json", sts)
            return _ok("sent", action_id=act["id"])

        preview = f"Follow-up to {to} (Re: {subject}):\n{body}"
        return self._side(step, preview, execute, f"Chased {to}: {card.get('headline')}", card_id)

    def ask_status(self, shipment_id: str, message: str) -> dict:
        from ask_soony import cardtools

        ship = next(
            (s for s in self.state.get("shipments") or [] if s.get("id") == shipment_id), None
        )
        if ship is None:
            return _no("unknown shipment", "unknown")
        to = supplier_email(ship.get("supplier_id"))
        if not to:
            return _no("no contact address for this supplier", "open")
        body = str(message).strip()
        subject = f"Status of shipment {shipment_id}"
        step = {
            "do": "ask_status",
            "shipment": shipment_id,
            "to": to,
            "message": body,
            "text": "Ask for a status",
        }

        def execute() -> dict:
            mid = cardtools._send_mail(to, subject, body)
            act = _append_action(
                {
                    "shipment_id": shipment_id,
                    "supplier_id": ship.get("supplier_id"),
                    "kind": "ask_status",
                    "channel": "email",
                    "to": to,
                    "subject": subject,
                    "draft": body,
                    "status": "sent",
                    "message_id": mid,
                    "via": self.via,
                }
            )
            return _ok("sent", action_id=act["id"])

        preview = f"Email to {to} ({subject}):\n{body}"
        return self._side(step, preview, execute, f"Asked {to} for the status of {shipment_id}")

    def call(self, text: str, card_id: str = "", who: str = "responsible") -> dict:
        card = next((c for c in self.state["cards"] if c["id"] == card_id), None)
        step = {"do": "call", "card": card_id, "message": str(text), "who": who, "text": "Call"}
        target = "the buyer" if who == "me" else ((card or {}).get("approver") or "the buyer")

        def execute() -> dict:
            out = _place_call(str(text), card, who)
            act = _append_action(
                {
                    "decision_id": card_id or None,
                    "kind": "call",
                    "channel": "call",
                    "to": out["contact"],
                    "text": str(text),
                    "status": "called" if out["dialled"] else "dry run",
                    "detail": out["detail"],
                    "alert_id": out["alert_id"],
                    "via": self.via,
                }
            )
            return _ok("called" if out["dialled"] else "dry run", action_id=act["id"])

        line = f"Calling {target}: {(card or {}).get('headline') or text}"
        return self._side(step, f"Call {target} and say: {text}", execute, line, card_id)

    def notify(self, text: str, card_id: str = "", channel: str = "both") -> None:
        channel = channel if channel in ("phone", "teams", "both") else "both"
        self.did.append(
            {"do": "notify", "card": card_id, "text": text, "channel": channel, "dry": self.dry}
        )
        if self.dry:
            return
        if channel in ("phone", "both"):
            _push(f"Routine: {self.name}", text, card_id or None)
        if channel in ("teams", "both") and card_id:
            try:
                from ask_soony import teams

                teams.notify_reply(card_id, f"Routine · {self.name}", text)
            except Exception:
                pass

    def log(self, text: str) -> None:
        self.lines.append(str(text)[:300])

    def first_time(self, key: str) -> bool:
        if key in self.seen:
            return False
        self.seen.add(key)
        return True

    def now(self) -> str:
        return self.cur.isoformat()


def run(routine: dict, dry: bool = False, state: dict | None = None) -> dict:
    """Run one routine in the Monty sandbox; returns the run record."""
    from pydantic_monty import CollectString, ExternalModule, Monty

    ctx = _Run(routine, dry, state)
    module = ExternalModule(
        {
            "open_cards": ctx.open_cards,
            "waiting_cards": ctx.waiting_cards,
            "replied_cards": ctx.replied_cards,
            "silent_shipments": ctx.silent_shipments,
            "carry_out": ctx.carry_out,
            "chase": ctx.chase,
            "ask_status": ctx.ask_status,
            "call": ctx.call,
            "notify": ctx.notify,
            "log": ctx.log,
            "first_time": ctx.first_time,
            "now": ctx.now,
        },
        stubs=STUBS,
    )
    printed = CollectString(max_bytes=20_000)
    record: dict = {"at": ctx.now(), "dry": dry, "mode": ctx.mode}
    try:
        with Monty() as pool:
            with pool.checkout(
                external_modules={"soony": module},
                type_check=True,
                limits={"max_feed_duration_secs": 10, "max_memory": 64 * 1024 * 1024},
            ) as session:
                result = session.feed_run(routine["code"], print_callback=printed)
        record.update(ok=True, result=result if isinstance(result, (int, str, float)) else None)
    except Exception as exc:  # syntax, type or runtime errors from the sandbox
        record.update(ok=False, error=str(exc)[:1500])
    output = str(getattr(printed, "output", "") or "")
    record.update(log=ctx.lines + ([output.strip()] if output.strip() else []), did=ctx.did)
    if not dry:
        routine["seen"] = sorted(ctx.seen)
    return record


# ---------------------------------------------------------------- proposals (mode "ask")


def _proposals() -> list[dict]:
    from ask_soony import api

    return api.read_list(PROPOSALS)


def propose(routine: dict, step: dict, preview: str) -> str:
    """Store one outgoing action as a pending proposal; the same pending one is not repeated."""
    from ask_soony import api

    with _plock:
        items = _proposals()
        same = next(
            (
                p
                for p in items
                if p.get("status") == "pending"
                and p.get("routine_id") == routine.get("id")
                and p.get("step") == step
            ),
            None,
        )
        if same:
            return str(same["id"])
        p = {
            "id": uuid.uuid4().hex[:8],
            "routine_id": routine.get("id"),
            "routine_name": routine.get("name"),
            **({"card_id": step["card"]} if step.get("card") else {}),
            **({"shipment_id": step["shipment"]} if step.get("shipment") else {}),
            "step": step,
            "preview_text": preview,
            "created_at": api.now().isoformat(),
            "status": "pending",
        }
        api.write_json(PROPOSALS, items + [p])
    _push(f"Routine asks: {routine.get('name')}", preview.split("\n")[0][:160], step.get("card"))
    return p["id"]


def accept(pid: str) -> dict:
    """Carry out one proposal now, through the same building blocks as a real run; once only."""
    from ask_soony import api

    with _plock:
        items = _proposals()
        p = next((x for x in items if x.get("id") == pid), None)
        if p is None:
            raise HTTPException(404, "unknown proposal")
        if p.get("status") != "pending":
            return {"ok": False, "result": {"error": f"already {p.get('status')}"}}
        p["status"] = "running"
        api.write_json(PROPOSALS, items)
    routine = next(
        (r for r in _all() if r.get("id") == p.get("routine_id")),
        {"id": p.get("routine_id"), "name": p.get("routine_name") or "Routine"},
    )
    st = p.get("step") or {}
    result: dict
    try:
        ctx = _Run(routine, dry=False)
        ctx.accepting = True
        do = st.get("do")
        if do == "carry_out":
            result = ctx.carry_out(
                str(st.get("card")),
                int(st.get("option_index") or 0),
                str(st.get("reason") or "accepted"),
            )
        elif do == "chase":
            result = ctx.chase(str(st.get("card")), str(st.get("message") or ""))
        elif do == "ask_status":
            result = ctx.ask_status(str(st.get("shipment")), str(st.get("message") or ""))
        elif do == "call":
            result = ctx.call(
                str(st.get("message") or ""),
                str(st.get("card") or ""),
                str(st.get("who") or "responsible"),
            )
        else:
            result = _no(f"cannot carry out {do}")
    except Exception as exc:
        result = _no(str(exc)[:300], "error")
    with _plock:
        items = _proposals()
        for x in items:
            if x.get("id") == pid:
                x["status"] = "done" if result.get("ok") else "pending"
                if result.get("ok"):
                    x["done_at"] = api.now().isoformat()
                    x["result"] = result
                else:
                    x["error"] = result.get("error")
        api.write_json(PROPOSALS, items)
    _would_cache.clear()
    return {"ok": bool(result.get("ok")), "result": result}


def skip(pid: str) -> dict:
    from ask_soony import api

    with _plock:
        items = _proposals()
        p = next((x for x in items if x.get("id") == pid), None)
        if p is None:
            raise HTTPException(404, "unknown proposal")
        if p.get("status") == "pending":
            p["status"] = "skipped"
            p["skipped_at"] = api.now().isoformat()
            api.write_json(PROPOSALS, items)
    return {"ok": True, "proposal": p}


# ---------------------------------------------------------------- undo


EMAIL_GONE = "The email already went out. Send a correction instead?"
CALL_GONE = "The call already happened."


def undo(action_id: str) -> dict:
    """
    Undo one recorded action: decisions without an email go back to open; mail and calls
    cannot be taken back.
    """
    from ask_soony import api

    with api._lock:
        actions = api.read_list("actions.json")
        act = next((a for a in actions if str(a.get("id")) == action_id), None)
        if act is None:
            raise HTTPException(404, "unknown action")
        if act.get("kind") == "undo" or any(
            a.get("kind") == "undo" and a.get("undoes") == action_id for a in actions
        ):
            return {"ok": False, "reason": "Already undone."}
        if act.get("kind") == "call" or act.get("channel") == "call":
            return {"ok": False, "reason": CALL_GONE}
        if (
            act.get("message_id")
            or act.get("status") == "sent"
            or act.get("kind") in ("chase", "ask_status")
        ):
            return {"ok": False, "reason": EMAIL_GONE}
        if act.get("kind") == "reply":
            return {"ok": False, "reason": "A supplier's reply cannot be undone."}
        card_id = str(act.get("decision_id") or "")
        sts = api.read_json("decision_status.json", {})
        entry = sts.get(card_id)
        if entry and entry.get("action_id") not in (None, action_id):
            return {"ok": False, "reason": "Something newer happened on this decision since."}
        sts.pop(card_id, None)
        api.write_json("decision_status.json", sts)
        undo_act = {
            "id": uuid.uuid4().hex[:10],
            "decision_id": card_id,
            "at": api.now().isoformat(),
            "kind": "undo",
            "undoes": action_id,
            "status": "open",
            "via": "inbox",
        }
        actions.append(undo_act)
        api.write_json("actions.json", actions)
    _would_cache.clear()
    return {"ok": True, "undone": True, "card_id": card_id, "action": undo_act}


# ---------------------------------------------------------------- would handle


_would_cache: dict = {}
WOULD_TTL = 30.0


def would_handle() -> dict[str, list[dict]]:
    """Which routines that are off, or only ask, would act on which open card (dry runs)."""
    hit = _would_cache.get("v")
    if hit and time.monotonic() - hit[0] < WOULD_TTL:
        return hit[1]
    from ask_soony import api

    out: dict[str, list[dict]] = {}
    candidates = [
        r for r in _all() if r.get("code") and (not r.get("enabled") or mode_of(r) == "ask")
    ]
    if candidates:
        state = api.build_state()
        for r in candidates:
            try:
                rec = run(dict(r), dry=True, state=state)
            except Exception as exc:
                log.warning("would-handle %s failed: %s", r.get("id"), exc)
                continue
            info = {
                "routine_id": r.get("id"),
                "routine_name": r.get("name"),
                "mode": mode_of(r),
                "enabled": bool(r.get("enabled")),
            }
            for cid in {str(d["card"]) for d in rec.get("did") or [] if d.get("card")}:
                out.setdefault(cid, []).append(info)
    _would_cache["v"] = (time.monotonic(), out)
    return out


# ---------------------------------------------------------------- recipe (spec) and compiler
#
# The writer agent returns a recipe, not code: what it watches ("on"), conditions ("when") and
# steps ("then") in plain phrases plus a few machine fields. compile_spec() turns it into the
# sandbox code, so edits by text or drag and drop change data only, and the money/plan guard is
# always compiled in.

TRIGGERS = ("decisions", "waiting", "replies", "shipments")
TEXT_FIELDS = {"evidence", "headline", "option_action", "option_to", "supplier_name", "kind"}
NUM_FIELDS = {"hours_left", "waiting_hours", "silent_hours"}
FLAG_FIELDS = {"has_reply", "reply_after_launch"}
FIELDS = TEXT_FIELDS | NUM_FIELDS | FLAG_FIELDS
OPS = {"contains", "is", "any_of", "lt", "gt"}
STEPS = {"carry_out", "notify", "log", "chase", "call", "ask_status"}
CHANNELS = {"phone", "teams", "both"}
# What each trigger can look at and do.
FIELD_ON = {
    "waiting_hours": {"waiting"},
    "silent_hours": {"shipments"},
    "hours_left": {"decisions", "waiting", "replies"},
    "has_reply": {"decisions", "waiting", "replies"},
    "reply_after_launch": {"decisions", "waiting", "replies"},
    "evidence": {"decisions", "waiting", "replies"},
    "option_action": {"decisions", "waiting", "replies"},
    "option_to": {"decisions", "waiting", "replies"},
}
STEP_ON = {
    "carry_out": {"decisions"},
    "chase": {"waiting", "replies"},
    "ask_status": {"shipments"},
}
ON_TEXT = {
    "decisions": "open decisions",
    "waiting": "messages waiting for a supplier's answer",
    "replies": "supplier answers",
    "shipments": "shipments",
}
DEFAULT_MESSAGE = {
    "chase": "Hello {supplier} team,\n\nfollowing up on our message below about {headline}: "
    "could you let us know where this stands? A short answer is enough.\n\nThank you and kind "
    "regards,\nSoony Procurement",
    "ask_status": "Hello {supplier} team,\n\ncould you tell us the current status and expected "
    "arrival of shipment {shipment}? Our tracking has not updated for a while.\n\nThank you and "
    "kind regards,\nSoony Procurement",
    "call": "{headline}",
}


def _clean_cond(c: dict, label: str, on: str) -> dict:
    if not isinstance(c, dict):
        raise ValueError(f"{label} is not understood")
    field = str(c.get("field") or "")
    op = str(
        c.get("op")
        or ("lt" if field in NUM_FIELDS else "is" if field in FLAG_FIELDS else "contains")
    )
    raw = c.get("value")
    value: str | float = str(raw if raw is not None else "").strip()
    if field not in FIELDS or op not in OPS or not value:
        raise ValueError(f"{label} is not understood")
    if on not in FIELD_ON.get(field, set(TRIGGERS)):
        raise ValueError(f"{label} does not apply to {ON_TEXT[on]}")
    if field in NUM_FIELDS:
        if op not in ("lt", "gt"):
            raise ValueError(f"{label} needs 'less than' or 'more than' a number of hours")
        try:
            value = float(str(value).replace(",", "."))
        except ValueError:
            raise ValueError(f"{label} needs a number of hours") from None
        value = int(value) if value == int(value) else value
    elif field in FLAG_FIELDS:
        value = str(value).lower()
        if op != "is" or value not in ("yes", "no"):
            raise ValueError(f"{label} must be yes or no")
    elif op in ("lt", "gt"):
        raise ValueError(f"{label} compares words with a number")
    else:
        value = str(value)[:120]
    return {
        "id": str(c.get("id") or label.replace(" ", "")),
        "field": field,
        "op": op,
        "value": value,
        "text": str(c.get("text") or value)[:160],
    }


def clean_spec(spec: dict) -> dict:
    """Validate and normalise a recipe; raises ValueError with a buyer-readable reason."""
    if not isinstance(spec, dict):
        raise ValueError("the recipe is not an object")
    on = str(spec.get("on") or "decisions")
    if on not in TRIGGERS:
        raise ValueError("the routine does not say what to watch")
    when, then = [], []
    for i, c in enumerate(spec.get("when") or []):
        cond = _clean_cond(c, f"condition {i + 1}", on)
        cond["id"] = str((c or {}).get("id") or f"c{i + 1}")
        when.append(cond)
    for i, st in enumerate(spec.get("then") or []):
        if not isinstance(st, dict):
            raise ValueError(f"step {i + 1} is not understood")
        do = str(st.get("do") or "")
        if do not in STEPS:
            raise ValueError(f"step {i + 1} is not understood")
        if on not in STEP_ON.get(do, set(TRIGGERS)):
            raise ValueError(f"step {i + 1} cannot be done on {ON_TEXT[on]}")
        step: dict = {
            "id": str(st.get("id") or f"s{i + 1}"),
            "do": do,
            "text": str(st.get("text") or do)[:160],
        }
        if do == "carry_out":
            m = st.get("match") or {}
            step["match"] = {k: str(m[k])[:80] for k in ("to", "action_contains") if m.get(k)}
        if do == "notify":
            ch = str(st.get("channel") or "both")
            step["channel"] = ch if ch in CHANNELS else "both"
            step["message"] = str(st.get("message") or "{headline}")[:200]
        if do == "log":
            step["message"] = str(st.get("message") or st.get("text") or "")[:200]
        if do in ("chase", "ask_status", "call"):
            step["message"] = str(st.get("message") or DEFAULT_MESSAGE[do])[:1200]
        if do == "call":
            step["who"] = "me" if str(st.get("who") or "") == "me" else "responsible"
        if do in ("chase", "ask_status", "notify", "call") and st.get("repeat_hours"):
            try:
                hours = float(st["repeat_hours"])
            except (TypeError, ValueError):
                raise ValueError(f"step {i + 1}: repeat needs a number of hours") from None
            if hours < 0.25:
                raise ValueError(f"step {i + 1}: repeat at most every 15 minutes")
            step["repeat_hours"] = int(hours) if hours == int(hours) else hours
        if st.get("when"):
            step["when"] = []
            for j, c in enumerate(st["when"]):
                cond = _clean_cond(c, f"step {i + 1}, condition {j + 1}", on)
                cond["id"] = str((c or {}).get("id") or f"s{i + 1}c{j + 1}")
                step["when"].append(cond)
        then.append(step)
    if not when:
        raise ValueError("a routine needs at least one condition")
    if not then:
        raise ValueError("a routine needs at least one step")
    return {"on": on, "when": when, "then": then}


COMPILED = """
from soony import open_cards, waiting_cards, replied_cards, silent_shipments
from soony import carry_out, chase, ask_status, call, notify, log, first_time

ON = {on}
CONDITIONS = {conditions}
STEPS = {steps}
NEEDS_SILENT = {needs_silent}


def field_text(item, field):
    if field == "evidence":
        parts = []
        for e in item.get("evidence") or []:
            parts.append(e["source"] + " " + e["value"])
        return " ".join(parts)
    if field == "option_action" or field == "option_to":
        key = "action" if field == "option_action" else "to"
        parts = []
        for o in item.get("options") or []:
            parts.append(o[key] or "")
        return " ".join(parts)
    value = item.get(field)
    return value if isinstance(value, str) else ""


def holds(item, cond):
    field = str(cond["field"])
    op = str(cond["op"])
    value = str(cond["value"]).lower()
    if op == "lt" or op == "gt":
        number = item.get(field)
        if number is None:
            return False
        if op == "lt":
            return float(number) < float(value)
        return float(number) > float(value)
    if field == "has_reply" or field == "reply_after_launch":
        return (item.get(field) is True) == (value == "yes")
    text = field_text(item, field).lower()
    if op == "is":
        return text == value
    if op == "any_of":
        for v in value.split(","):
            if v.strip() and v.strip() in text:
                return True
        return False
    return value in text


def all_hold(item, conds):
    for cond in conds:
        if not holds(item, cond):
            return False
    return True


def fill(template, item):
    out = template.replace("{{headline}}", item.get("headline") or "")
    out = out.replace("{{supplier}}", item.get("supplier_name") or "the supplier")
    out = out.replace("{{shipment}}", str(item.get("shipment_id") or item.get("id") or ""))
    return out.replace("{{eta}}", str(item.get("eta") or ""))


def pick(card, match):
    for o in card["options"]:
        to = str(match.get("to") or "").lower()
        words = str(match.get("action_contains") or "").lower()
        if to and to not in str(o["to"] or "").lower():
            continue
        if words and words not in str(o["action"] or "").lower():
            continue
        return o
    return None


def items():
    if ON == "waiting":
        return waiting_cards()
    if ON == "replies":
        return replied_cards()
    if ON == "shipments":
        return silent_shipments()
    return open_cards()


def step_key(item, step):
    repeat = step.get("repeat_hours")
    if ON == "decisions" and not step.get("when") and not repeat:
        return ""
    key = str(item["id"]) + ":" + str(step["id"])
    if repeat:
        key = key + ":" + str(int(float(item.get("clock_hours") or 0) // float(repeat)))
    return key


def report(step, item, result):
    log(str(step["text"]) + ": " + str(item.get("headline") or "") + " (" + str(result["status"])
        + ")")


handled = 0
for item in items():
    if NEEDS_SILENT and not item.get("silent"):
        continue
    if not all_hold(item, CONDITIONS):
        continue
    card_id = "" if ON == "shipments" else str(item["id"])
    fresh = 0
    acted = False
    for step in STEPS:
        if not all_hold(item, step.get("when") or []):
            continue
        key = step_key(item, step)
        if key == "":
            if fresh == 0:
                fresh = 1 if first_time(str(item["id"])) else 2
            if fresh == 2:
                continue
        elif not first_time(key):
            continue
        acted = True
        do = step["do"]
        if do == "carry_out":
            option = pick(item, step.get("match") or {{}})
            if option is None:
                log("No matching option on: " + item["headline"])
            elif option["free"]:
                report(step, item, carry_out(item["id"], option["index"], str(step["text"])))
            else:
                notify("Needs you, it costs money or changes the plan: " + item["headline"],
                       item["id"], "both")
        elif do == "notify":
            notify(fill(str(step["message"]), item), card_id, str(step["channel"]))
        elif do == "chase":
            report(step, item, chase(str(item["id"]), fill(str(step["message"]), item)))
        elif do == "ask_status":
            report(step, item, ask_status(str(item["id"]), fill(str(step["message"]), item)))
        elif do == "call":
            report(step, item, call(fill(str(step["message"]), item), card_id,
                                    str(step.get("who") or "responsible")))
        else:
            log(fill(str(step["message"]), item))
    if acted:
        handled += 1
handled
"""


def compile_spec(spec: dict) -> str:
    spec = clean_spec(spec)
    needs_silent = spec["on"] == "shipments" and not any(
        c["field"] == "silent_hours" for c in spec["when"]
    )
    code = COMPILED.format(
        on=repr(spec["on"]),
        conditions=repr(spec["when"]),
        steps=repr(spec["then"]),
        needs_silent=repr(needs_silent),
    )
    return code.strip() + "\n"


SPEC_HELP = """A routine is a recipe (JSON):
{"on": what it watches, "when": [conditions, all must hold], "then": [steps, in order]}
on: "decisions" (open decisions in the inbox, the default) | "waiting" (decisions whose message
  went to a supplier and nobody answered yet) | "replies" (decisions the supplier answered) |
  "shipments" (open shipments; without a silent_hours condition only those that stopped
  updating)
condition: {"id": "c1", "field": ..., "op": ..., "value": ..., "text": "<the condition as a
  plain phrase>"}
  words: field evidence | headline | option_action | option_to | supplier_name | kind, op
    contains | is | any_of (comma-separated), value "<lowercase words>"
  hours: field hours_left (hours until the decision's deadline) | waiting_hours (hours since
    our message went out; only on "waiting") | silent_hours (hours since a shipment last
    updated; only on "shipments"), op lt (less than) | gt (more than), value a number
  yes/no: field has_reply | reply_after_launch (the supplier's answer gives a date after the
    launch), op "is", value "yes" | "no"
step: {"id": "s1", "do": "carry_out", "match": {"to": "<recipient role or address part>",
  "action_contains": "<words in the option>"}, "text": "<plain phrase>"}   (only on decisions)
  | {"id": "s2", "do": "notify", "channel": "phone" | "teams" | "both",
     "message": "<what to tell the buyer; may use {headline} and {supplier}>", "text": "<phrase>"}
  | {"id": "s3", "do": "chase", "message": "<polite follow-up; may use {headline}, {supplier}>",
     "text": "<phrase>", "repeat_hours": <optional number>}   (on waiting or replies: one
     follow-up in the same email thread to the same supplier; once unless repeat_hours is set)
  | {"id": "s4", "do": "call", "who": "me" | "responsible", "message": "<what the call says>",
     "text": "<phrase>"}   (phones the buyer or the person responsible for the decision)
  | {"id": "s5", "do": "ask_status", "message": "<may use {supplier}, {shipment}, {eta}>",
     "text": "<phrase>", "repeat_hours": <optional number>}   (only on shipments: emails the
     supplier contact asking for the shipment's status)
  | {"id": "s6", "do": "log", "message": "<line for the history>", "text": "<phrase>"}
Any step may have its own "when": [conditions] that must also hold before it runs (for a later
step such as calling after more hours). Each step runs once per decision or shipment.
The system always adds: options that cost money or change the plan are never carried out; the
buyer is told instead. Fields: evidence = what the system and the documents say;
option_action / option_to = the proposed actions and their recipients.

Examples:
"If a supplier has not answered within 2 hours, chase once; if still nothing after 4 hours,
call me" ->
{"on": "waiting", "when": [{"id": "c1", "field": "waiting_hours", "op": "gt", "value": 2,
 "text": "the supplier has not answered for 2 hours"}], "then": [{"id": "s1", "do": "chase",
 "message": "Hello {supplier} team, following up on our message below about {headline}: could
 you let us know where this stands? Thank you, Soony Procurement", "text": "Send one friendly
 reminder"}, {"id": "s2", "do": "call", "who": "me", "when": [{"id": "s2c1", "field":
 "waiting_hours", "op": "gt", "value": 4, "text": "still no answer after 4 hours"}], "message":
 "{supplier} has not answered about {headline} for 4 hours.", "text": "Call me"}]}
"If a red decision is still open 2 hours before its deadline, call the person responsible" ->
{"on": "decisions", "when": [{"id": "c1", "field": "hours_left", "op": "lt", "value": 2, "text":
 "less than 2 hours left to decide"}], "then": [{"id": "s1", "do": "call", "who":
 "responsible", "message": "{headline} is still open and must be decided within 2 hours.",
 "text": "Call the person who decides"}]}
"When a shipment has not updated for 12 hours, ask the supplier for its status" ->
{"on": "shipments", "when": [{"id": "c1", "field": "silent_hours", "op": "gt", "value": 12,
 "text": "no update for 12 hours"}], "then": [{"id": "s1", "do": "ask_status", "text": "Ask the
 supplier where it is"}]}"""


# ---------------------------------------------------------------- the writer agent


def _complete(prompt: str) -> str:
    """One completion from Claude (ANTHROPIC_API_KEY) or OpenAI (OPENAI_API_KEY)."""
    load_dotenv()
    if os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic

        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=os.environ.get("ASK_ROUTINE_MODEL", "claude-opus-5-5"),
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in msg.content if b.type == "text")
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise HTTPException(503, "no model configured: set ANTHROPIC_API_KEY or OPENAI_API_KEY")
    r = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": os.environ.get("ASK_ROUTINE_MODEL", "gpt-4.1"),
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        },
        timeout=90,
    )
    if r.status_code >= 300:
        raise HTTPException(502, f"model refused: {r.text[:200]}")
    return r.json()["choices"][0]["message"]["content"]


def _parse(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    data = json.loads(m.group(0) if m else text)
    return {
        "name": str(data.get("name") or "New routine")[:80],
        "description": str(data.get("description") or "")[:400],
        "spec": data.get("spec") or {},
        "note": str(data.get("note") or "")[:300],
    }


def _ask_for_spec(prompt: str, fallback: dict | None = None) -> dict:
    """Ask the model for a recipe; one repair round if it is not valid. Returns the parsed draft
    with a cleaned spec and its compiled code."""
    error = ""
    for _ in range(2):
        draft = _parse(
            _complete(prompt + (f"\n\nThat recipe was invalid: {error}. Fix it." if error else ""))
        )
        try:
            draft["spec"] = clean_spec(draft["spec"])
            draft["code"] = compile_spec(draft["spec"])
            return draft
        except ValueError as exc:
            error = str(exc)
    if fallback is not None:
        return {**fallback, "note": f"Could not apply that change: {error}."}
    raise HTTPException(502, f"the agent could not write a valid routine: {error}")


def preview(routine: dict) -> dict:
    """Compile (if it has a recipe) and dry-run a routine on today's inbox."""
    if routine.get("spec"):
        routine["code"] = compile_spec(routine["spec"])
    return run(routine, dry=True)


def suggest(card_id: str) -> dict:
    """Draft a routine from the person's last decision on this card, checked by a dry run."""
    from ask_soony import api

    card = next((c for c in api.build_state()["cards"] if c["id"] == card_id), None)
    if card is None:
        raise HTTPException(404, "unknown card")
    act = next(
        (
            a
            for a in reversed(api.read_list("actions.json"))
            if str(a.get("decision_id")) == card_id
            and a.get("kind") in ("approve", "escalate", "dismiss")
        ),
        None,
    )
    if act is None:
        raise HTTPException(409, "decide this card first, then automate it")
    action = {
        "approve": f"approved: {act.get('action')} (to {act.get('to')}, {act.get('status')})",
        "escalate": f"escalated it to {act.get('to')}",
        "dismiss": "dismissed it",
    }[act["kind"]]
    prompt = WRITER_PROMPT.format(
        spec_help=SPEC_HELP, card=json.dumps(_card_view(card), ensure_ascii=False), action=action
    )
    draft = _ask_for_spec(prompt)
    draft.update(id=uuid.uuid4().hex[:8], created_from=card_id, enabled=False, mode="tell")
    return {"routine": draft, "preview": run(draft, dry=True)}


def edit(routine: dict, instruction: str) -> dict:
    """Change a routine (draft or saved) as the buyer asks; returns the new version and its test
    run. Nothing is saved here."""
    current = {k: routine.get(k) for k in ("name", "description", "spec")}
    prompt = EDIT_PROMPT.format(
        spec_help=SPEC_HELP,
        routine=json.dumps(current, ensure_ascii=False),
        instruction=instruction.replace('"', "'")[:300],
    )
    new = _ask_for_spec(prompt, fallback={**current, "code": routine.get("code", "")})
    after = {**routine, **{k: new[k] for k in ("name", "description", "spec", "code")}}
    return {"after": after, "note": new.get("note", ""), "preview": run(after, dry=True)}


# ---------------------------------------------------------------- loop and routes


def _count_handled(rec: dict) -> int:
    return len({d.get("card") or d.get("shipment") for d in rec.get("did") or []} - {None, ""})


def run_enabled() -> list[dict]:
    with _lock:
        items = _all()
        records = []
        for r in items:
            if not r.get("enabled"):
                continue
            rec = run(r)
            r["runs"] = ([rec] + (r.get("runs") or []))[:20]
            r["handled"] = int(r.get("handled") or 0) + _count_handled(rec)
            records.append({"routine": r["id"], **rec})
        _save(items)
    return records


def _loop(interval: int) -> None:
    while True:
        try:
            for rec in run_enabled():
                if rec.get("did") or not rec.get("ok"):
                    log.info("routine %s: %s", rec["routine"], rec.get("did") or rec.get("error"))
        except Exception as exc:
            log.warning("routines failed: %s", exc)
        time.sleep(interval)


def start_background() -> None:
    interval = int(os.environ.get("ASK_ROUTINES_SECONDS", "60"))
    threading.Thread(target=_loop, args=(interval,), daemon=True, name="routines").start()


class Suggest(BaseModel):
    card_id: str


class Draft(BaseModel):
    id: str
    name: str
    description: str
    spec: dict | None = None
    code: str | None = None
    created_from: str | None = None
    mode: str | None = None


class EditRequest(BaseModel):
    routine: Draft
    instruction: str


class Toggle(BaseModel):
    enabled: bool


class ModeBody(BaseModel):
    mode: str


def _public(r: dict) -> dict:
    return {**{k: v for k, v in r.items() if k != "seen"}, "mode": mode_of(r)}


@router.get("/api/routines")
def list_routines():
    return {"routines": [_public(r) for r in _all()]}


@router.post("/api/routines/suggest")
def post_suggest(body: Suggest):
    return suggest(body.card_id)


@router.post("/api/routines")
def save_routine(body: Draft):
    """Save a new routine or a changed version; a changed one keeps its switch, runs and memory."""
    from ask_soony import api

    data = body.model_dump()
    try:
        if data.get("spec"):
            data["spec"] = clean_spec(data["spec"])
            data["code"] = compile_spec(data["spec"])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not data.get("code"):
        raise HTTPException(400, "a routine needs a recipe")
    with _lock:
        items = _all()
        old = next((r for r in items if r.get("id") == body.id), None)
        keep = (
            {k: old.get(k) for k in ("enabled", "created_at", "runs", "seen", "handled")}
            if old
            else {
                "enabled": True,
                "created_at": api.now().isoformat(),
                "runs": [],
                "seen": [],
                "handled": 0,
            }
        )
        mode = data.get("mode") if data.get("mode") in MODES else mode_of(old or {})
        items = [r for r in items if r.get("id") != body.id] + [{**data, **keep, "mode": mode}]
        _save(items)
    return {"ok": True}


@router.post("/api/routines/edit")
def post_edit(body: EditRequest):
    return edit(body.routine.model_dump(), body.instruction)


@router.post("/api/routines/preview")
def post_preview(body: Draft):
    data = body.model_dump()
    try:
        return {"routine": data, "preview": preview(data)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/routines/proposals")
def list_proposals():
    items = _proposals()
    pending = sorted(
        (p for p in items if p.get("status") == "pending"),
        key=lambda p: str(p.get("created_at")),
        reverse=True,
    )
    rest = sorted(
        (p for p in items if p.get("status") != "pending"),
        key=lambda p: str(p.get("created_at")),
        reverse=True,
    )
    return {"proposals": pending + rest[:50]}


@router.post("/api/routines/proposals/{pid}/accept")
def post_accept(pid: str):
    return accept(pid)


@router.post("/api/routines/proposals/{pid}/skip")
def post_skip(pid: str):
    return skip(pid)


@router.post("/api/routines/actions/{action_id}/undo")
def post_undo(action_id: str):
    return undo(action_id)


@router.get("/api/routines/would-handle")
def get_would_handle():
    return would_handle()


@router.post("/api/routines/{rid}/mode")
def set_mode(rid: str, body: ModeBody):
    if body.mode not in MODES:
        raise HTTPException(400, "mode must be ask, tell or quiet")
    with _lock:
        items = _all()
        if not any(r.get("id") == rid for r in items):
            raise HTTPException(404, "unknown routine")
        for r in items:
            if r.get("id") == rid:
                r["mode"] = body.mode
        _save(items)
    return _public(_get(rid))


@router.post("/api/routines/{rid}/toggle")
def toggle_routine(rid: str, body: Toggle):
    with _lock:
        items = _all()
        for r in items:
            if r.get("id") == rid:
                r["enabled"] = body.enabled
        _save(items)
    return _public(_get(rid))


@router.post("/api/routines/{rid}/run")
def run_now(rid: str):
    with _lock:
        items = _all()
        r = next((x for x in items if x.get("id") == rid), None)
        if r is None:
            raise HTTPException(404, "unknown routine")
        rec = run(r)
        r["runs"] = ([rec] + (r.get("runs") or []))[:20]
        r["handled"] = int(r.get("handled") or 0) + _count_handled(rec)
        _save(items)
    return rec


@router.delete("/api/routines/{rid}")
def delete_routine(rid: str):
    with _lock:
        _save([r for r in _all() if r.get("id") != rid])
    return {"ok": True}
