"""
Sign-off links: a passed-on decision and an internal message reach the person, and their answer
comes back. Same loop as the questions to the site (floor.py): a one-time link to /ok/<token>,
sent as a push and on Teams when configured, a phone page that needs no dashboard password, only
a valid token (random, stored here, expires at launch).

Two kinds of request:
- "escalation": "Pass it on" (api.escalate). The approver approves (the asker's chosen option,
  else the recommended one, carried out through api.approve: emails to the supplier, duplicate
  guard and rules apply) or declines (the case goes back to the asker as open, with their note).
- "notify": an approved option with channel "notify" to an internal person or role. They answer
  "Got it" or "Done"; the answer is recorded on the request and shown on the card.

Nothing is sent automatically again: a request that reached nobody says so on the card, and
"Send again" (POST /api/cards/{id}/signoff/resend) makes a fresh link. ASK_SIGNOFF_SIMULATE=<s>
(demo only, off by default) approves an unanswered escalation after s seconds in the approver's
name, marked as simulated (via "signoff-simulated"); a real tap before that wins.

State files (data/state, gitignored): signoff_requests.json (requests and answers),
signoff_tokens.json (links).
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import threading
import uuid
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from ask_soony import launch as launch_clock

log = logging.getLogger("ask_soony.signoff")
router = APIRouter()
_lock = threading.Lock()
_busy: set[str] = set()  # requests being carried out right now (double tap guard)
_timers: list[threading.Timer] = []  # demo simulation timers (tests join them)

REQUESTS = "signoff_requests.json"
TOKENS = "signoff_tokens.json"
TOKEN_RE = r"[\w-]{16,64}"


def _api():
    from ask_soony import api

    return api


# ---------------------------------------------------------------- names


def person(to: str | None) -> str:
    """'Dr. Maren Vogt, Head of Procurement' -> 'Dr. Maren Vogt'; roles stay as they are."""
    return _api().person_of(to) or (to or "").strip()


def short(to: str | None) -> str:
    """What the buyer calls them: 'Maren' for 'Dr. Maren Vogt' or 'Maren Vogt, Head of
    Procurement'; a role ('DC site lead', 'Supplier Quality') or a bare name stays whole."""
    p = person(to)
    words = p.split()
    if len(words) >= 3 and words[0].endswith("."):  # a title: 'Dr. Maren Vogt'
        return words[1]
    rest = (to or "").split(",", 1)[1:] or [""]
    named = 2 <= len(words) <= 3 and all(re.fullmatch(r"[A-Z][a-z]+", w) for w in words)
    if named and re.search(r"\b[a-z]", rest[0]):  # 'Maren Vogt, Head of Procurement'
        return words[0]
    return p


def _first(who: str | None) -> str:
    return next((w for w in person(who).split() if not w.endswith(".")), "").lower()


def _same(a: str | None, b: str | None) -> bool:
    return bool(a and b) and _first(a) == _first(b)


def target(card: dict, viewer: str | None, policy: str | None) -> str | None:
    """Who "Pass it on" reaches: the case's approver, unless that is the buyer passing it on (a
    routine case is the buyer's own) or nobody; then whoever the approval policy names for
    exceptions. None when that is the buyer too, or nobody is named."""
    to = card.get("approver")
    if to and not _same(to, viewer):
        return to
    return policy if policy and not _same(policy, viewer) else None


# ---------------------------------------------------------------- state files


def _requests() -> dict:
    data = _api().read_json(REQUESTS, {})
    return data if isinstance(data, dict) else {}


def _tokens() -> dict:
    data = _api().read_json(TOKENS, {})
    return data if isinstance(data, dict) else {}


def _save(req: dict) -> None:
    with _lock:
        reqs = _requests()
        reqs[req["id"]] = req
        _api().write_json(REQUESTS, reqs)


def token_entry(token: str) -> tuple[dict, dict] | None:
    """(link, request) behind a valid, unexpired link; None otherwise."""
    if not token or not re.fullmatch(TOKEN_RE, token):
        return None
    api = _api()
    entry = _tokens().get(token)
    if not entry:
        return None
    exp = api.parse_ts(entry.get("expires_at"))
    if exp is None or api.now() >= exp:
        return None
    req = _requests().get(str(entry.get("request")))
    return (entry, req) if req else None


def public_path(path: str) -> bool:
    """True for the sign-off page and its API when the token in the path is valid."""
    m = re.fullmatch(rf"/ok/({TOKEN_RE})/?|/api/ok/({TOKEN_RE})", path)
    return bool(m) and token_entry(m.group(1) or m.group(2)) is not None


def link_for(token: str) -> str:
    from ask_soony import teams

    return f"{teams._app_url()}/ok/{token}"


def _expires():
    api = _api()
    clock = launch_clock.load(api.portal_root(), api.tz())
    return clock.launch or (api.now() + timedelta(hours=24))


# ---------------------------------------------------------------- sending


def _headline(card: dict, n: int = 70) -> str:
    h = re.sub(r"\s+", " ", str(card.get("headline") or "")).strip().rstrip(".")
    if len(h) > n:  # 'The 8,000 art books arrive around 20:33; the line runs dry ...': first clause
        h = re.split(r";\s|:\s(?=[a-z])", h)[0]
    return h if len(h) <= n else h[: n - 1].rsplit(" ", 1)[0] + "…"


def _teams_payload(req: dict, url: str) -> dict:
    esc = req["kind"] == "escalation"
    rec = req.get("recommendation") or {}
    body = [
        {
            "type": "TextBlock",
            "text": f"{'Needs your OK' if esc else 'For'} {req['to_name']}",
            "isSubtle": True,
            "size": "Small",
        },
        {
            "type": "TextBlock",
            "text": req.get("headline") or "",
            "weight": "Bolder",
            "size": "Large",
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "text": (
                f"{req['asked_by_short'] or 'The buyer'} asks: {rec.get('action') or ''}"
                + (f" ({rec['cost']})" if rec.get("cost") else "")
                if esc
                else (req.get("message") or "")[:600]
            ),
            "wrap": True,
        },
    ]
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": body,
                    "actions": [
                        {
                            "type": "Action.OpenUrl",
                            "title": "Open and decide" if esc else "Open",
                            "url": url,
                        }
                    ],
                },
            }
        ],
    }


def send(req_id: str) -> dict:
    """A fresh link for the request, as a push and on Teams. Never raises: a request that reached
    nobody records channels [] and the card says so."""
    from ask_soony import push, teams

    api = _api()
    req = _requests().get(req_id)
    if req is None:
        raise HTTPException(404, "unknown request")
    token = secrets.token_urlsafe(18)
    cur = api.now()
    with _lock:
        tokens = {
            k: v for k, v in _tokens().items() if (api.parse_ts(v.get("expires_at")) or cur) > cur
        }
        tokens[token] = {
            "request": req_id,
            "created_at": cur.isoformat(),
            "expires_at": _expires().isoformat(),
        }
        api.write_json(TOKENS, tokens)
    url = link_for(token)
    if req["kind"] == "escalation":
        title = f"Needs your OK: {req['headline_short']}"
        rec = req.get("recommendation") or {}
        text = (
            f"{req['asked_by_short'] or 'The buyer'} asks: {rec.get('action') or 'your decision'}"
        )
    else:
        title = f"For {req['to_name']}: {req['headline_short']}"
        # The first line that says something: greetings ('Hello,') end in a comma.
        lines = [x.strip() for x in (req.get("message") or "").splitlines() if x.strip()]
        text = next((x for x in lines if not x.endswith(",")), "")[:180]
    channels = []
    try:
        if teams.post_payload(_teams_payload(req, url)):
            channels.append("teams")
    except Exception as exc:  # one of two channels
        log.warning("teams sign-off failed: %s", exc)
    try:
        if push.subscriptions() and push.send(title, text, req["card"], url=url).get("sent"):
            channels.append("push")
    except Exception as exc:
        log.warning("push sign-off failed: %s", exc)
    sent = {"at": cur.isoformat(), "channels": channels}
    with _lock:
        reqs = _requests()
        r = reqs.get(req_id, req)
        r.setdefault("sends", []).append(sent)
        reqs[req_id] = r
        api.write_json(REQUESTS, reqs)
    return {"request": req_id, "channels": channels, "at": sent["at"], "link": url}


def _option_index(card: dict, option: Any, note: str) -> int | None:
    """The option the asker wants approved: given, else named in 'Please approve: <action>',
    else the recommended one."""
    opts = card.get("options") or []
    if isinstance(option, int) or str(option).isdigit():
        i = int(option)
        if 0 <= i < len(opts):
            return i
    for i, o in enumerate(opts):
        if o.get("action") and f"Please approve: {o['action']}" in note:
            return i
    if not opts:
        return None
    return next((i for i, o in enumerate(opts) if o.get("recommended")), 0)


def _own_note(note: str, opt: dict | None) -> str:
    """The asker's own words: without the 'Please approve: <action> (<cost>).' the inbox adds."""
    if opt and note.startswith(f"Please approve: {opt.get('action')}"):
        rest = note[len(f"Please approve: {opt.get('action')}") :]
        if opt.get("cost") and rest.startswith(f" ({opt['cost']})"):
            rest = rest[len(f" ({opt['cost']})") :]
        return rest.lstrip(".").strip()
    return note.strip()


def _new(card: dict, kind: str, to: str, asked_by: str, **kw) -> dict:
    api = _api()
    return {
        "id": uuid.uuid4().hex[:10],
        "card": card["id"],
        "kind": kind,
        "to": to,
        "to_name": person(to),
        "to_short": short(to),
        "asked_by": asked_by,
        "asked_by_short": next((w for w in person(asked_by).split() if not w.endswith(".")), ""),
        "asked_at": api.now().isoformat(),
        "headline": card.get("headline"),
        "headline_short": _headline(card),
        "sends": [],
        "answer": None,
        "acks": [],
        **kw,
    }


def escalation(
    card: dict, option: Any, note: str, asked_by: str, asked_at: str | None = None
) -> dict | None:
    """Create and send the request for a passed-on card. None when there is nobody to ask."""
    to = card.get("escalate_to")
    if not to:
        return None
    idx = _option_index(card, option, note or "")
    opt = card["options"][idx] if idx is not None else None
    req = _new(
        card,
        "escalation",
        to,
        asked_by,
        option=idx,
        recommendation=(
            {"action": opt.get("action"), "cost": opt.get("cost"), "effect": opt.get("effect")}
            if opt
            else None
        ),
        note=_own_note(note or "", opt),
    )
    if asked_at:  # passed on before this link existed: keep when it was asked
        req["asked_at"] = asked_at
    _save(req)
    return send(req["id"])


def notify(card: dict, opt: dict, text: str | None, asked_by: str) -> dict | None:
    """Create and send the request for an approved internal message. None when it goes to the
    person who approved it (nothing to deliver)."""
    to = str(opt.get("to_role") or opt.get("to") or "").strip()
    if not to or _same(to, asked_by):
        return None
    req = _new(
        card,
        "notify",
        to,
        asked_by,
        option=opt.get("index"),
        subject=opt.get("action"),
        message=(text or opt.get("draft") or "").strip(),
    )
    _save(req)
    return send(req["id"])


def latest_for(card_id: str, kind: str | None = None) -> dict | None:
    reqs = [
        r
        for r in _requests().values()
        if r.get("card") == card_id and (kind is None or r.get("kind") == kind)
    ]
    return max(reqs, key=lambda r: str(r.get("asked_at"))) if reqs else None


def view(req: dict) -> dict:
    """What a card shows about its request (no tokens)."""
    last = (req.get("sends") or [{}])[-1]
    return {
        "id": req["id"],
        "kind": req["kind"],
        "to": req.get("to_name"),
        "who": req.get("to_short"),
        "asked_at": req.get("asked_at"),
        "sent_at": last.get("at"),
        "channels": last.get("channels") or [],
        "delivered": bool(last.get("channels")),
        "answer": req.get("answer"),
        "acks": req.get("acks") or [],
    }


def attach(cards: list[dict]) -> None:
    """Each card gets its newest request (`signoff_request`), or None."""
    by_card: dict[str, dict] = {}
    for r in _requests().values():
        cid = str(r.get("card"))
        if cid not in by_card or str(r.get("asked_at")) > str(by_card[cid].get("asked_at")):
            by_card[cid] = r
    for c in cards:
        r = by_card.get(c["id"])
        c["signoff_request"] = view(r) if r else None


# ---------------------------------------------------------------- answering


def _answer(req_id: str, answer: dict) -> None:
    with _lock:
        reqs = _requests()
        reqs[req_id]["answer"] = answer
        _api().write_json(REQUESTS, reqs)


def carry_out(req_id: str, via: str = "signoff", force: bool = False) -> dict:
    """Approve an escalation: the chosen option through api.approve, in the approver's name.
    Exactly once per request (409 when it was answered or the case moved on)."""
    api = _api()
    with _lock:
        req = _requests().get(req_id)
        if req is None or req["kind"] != "escalation":
            raise HTTPException(404, "unknown request")
        if req.get("answer") or req_id in _busy:
            raise HTTPException(409, "This was answered already.")
        _busy.add(req_id)
    try:
        card = api._card(req["card"])
        if card["status"] != "escalated":
            raise HTTPException(409, "This case is not waiting any more.")
        if req.get("option") is None:
            raise HTTPException(409, "There is no option to approve on this case.")
        out = api.approve(
            card["id"],
            {"option": req["option"], "via": via, "by": req["to_name"], "force": force},
        )
        action = out.get("action") or {}
        _answer(
            req_id,
            {
                "decision": "approved",
                "by": req["to_name"],
                "at": api.now().isoformat(),
                "simulated": via == "signoff-simulated",
                "status": action.get("status"),
                "action_id": action.get("id"),
            },
        )
        return action
    finally:
        with _lock:
            _busy.discard(req_id)


def decline(req_id: str, note: str) -> dict:
    """The case goes back to the asker as open, with the decliner's note."""
    api = _api()
    with _lock:
        req = _requests().get(req_id)
        if req is None or req["kind"] != "escalation":
            raise HTTPException(404, "unknown request")
        if req.get("answer") or req_id in _busy:
            raise HTTPException(409, "This was answered already.")
        _busy.add(req_id)
    try:
        card = api._card(req["card"])
        if card["status"] != "escalated":
            raise HTTPException(409, "This case is not waiting any more.")
        action = api._record(
            card["id"],
            "open",
            {
                "kind": "decline",
                "by": req["to_name"],
                "note": note or None,
                "status": "declined",
                "via": "signoff",
            },
        )
        _answer(
            req_id,
            {
                "decision": "declined",
                "by": req["to_name"],
                "at": api.now().isoformat(),
                "note": note or None,
                "action_id": action["id"],
            },
        )
        return action
    finally:
        with _lock:
            _busy.discard(req_id)


def acknowledge(req_id: str, answer: str) -> dict:
    """'got_it' or 'done' on an internal message; 'done' is final."""
    api = _api()
    with _lock:
        reqs = _requests()
        req = reqs.get(req_id)
        if req is None or req["kind"] != "notify":
            raise HTTPException(404, "unknown request")
        if req.get("answer"):
            raise HTTPException(409, "This is marked done already.")
        stamp = {"answer": answer, "by": req["to_name"], "at": api.now().isoformat()}
        req.setdefault("acks", []).append(stamp)
        if answer == "done":
            req["answer"] = {"decision": "done", "by": req["to_name"], "at": stamp["at"]}
        api.write_json(REQUESTS, reqs)
    return stamp


# ---------------------------------------------------------------- demo simulation


def simulate_seconds() -> float | None:
    raw = os.environ.get("ASK_SIGNOFF_SIMULATE", "").strip()
    try:
        return max(0.0, float(raw)) if raw else None
    except ValueError:
        return None


def _simulate(req_id: str) -> None:
    req = _requests().get(req_id)
    if not req or req.get("answer"):
        return  # a real answer came first
    try:
        carry_out(req_id, via="signoff-simulated")
    except HTTPException as exc:
        log.info("simulated sign-off skipped: %s", exc.detail)
    except Exception as exc:
        log.warning("simulated sign-off failed: %s", exc)


def start_simulation(req_id: str) -> None:
    """Demo only (ASK_SIGNOFF_SIMULATE): approve in the approver's name after a while."""
    secs = simulate_seconds()
    if secs is None:
        return
    t = threading.Timer(secs, _simulate, args=(req_id,))
    t.daemon = True
    _timers.append(t)
    del _timers[:-20]
    t.start()


# ---------------------------------------------------------------- the page and its API


def _request_for(token: str) -> dict:
    found = token_entry(token)
    if found is None:
        raise HTTPException(404, "This link is not valid any more.")
    return found[1]


def page(req: dict) -> dict:
    """What /ok/<token> shows: the case as the asker saw it, and the answer if there is one."""
    api = _api()
    card = next((c for c in api.build_state()["cards"] if c["id"] == req["card"]), None)
    keep = ("headline", "summary", "part_name", "supplier_name", "evidence", "level", "deadline")
    return {
        "kind": req["kind"],
        "to": req.get("to_name"),
        "who": req.get("to_short"),
        "asked_by": req.get("asked_by"),
        "asked_by_short": req.get("asked_by_short"),
        "asked_at": req.get("asked_at"),
        "headline": req.get("headline"),
        "card": {k: card.get(k) for k in keep} if card else None,
        "status": card.get("status") if card else None,
        "option": req.get("option"),
        "recommendation": req.get("recommendation"),
        "note": req.get("note"),
        "subject": req.get("subject"),
        "message": req.get("message"),
        "answer": req.get("answer"),
        "acks": req.get("acks") or [],
        "now": api.now().isoformat(),
    }


@router.get("/api/ok/{token}")
def get_page(token: str):
    return page(_request_for(token))


@router.post("/api/ok/{token}")
def post_answer(token: str, body: dict = Body(...)):
    req = _request_for(token)
    answer = str(body.get("answer") or "")
    note = str(body.get("note") or "").strip()[:300]
    if req["kind"] == "escalation" and answer == "approve":
        try:
            action = carry_out(req["id"], force=bool(body.get("force")))
        except HTTPException as exc:
            if exc.status_code == 409 and isinstance(exc.detail, dict):
                d = exc.detail.get("duplicate") or {}
                raise HTTPException(
                    409,
                    {
                        "duplicate": True,
                        "text": "This already went to the supplier"
                        + (f" ({d.get('subject')})" if d.get("subject") else "")
                        + ". Send it again anyway?",
                    },
                ) from None
            raise
        sent = action.get("status") == "sent"
        card = next((c for c in _api().build_state()["cards"] if c["id"] == req["card"]), {})
        text = (
            f"Approved. The email to {card.get('supplier_name') or action.get('to')} went out."
            if sent
            else "Approved and recorded."
        )
        return {"ok": True, "text": text + f" {req['asked_by_short'] or 'The buyer'} sees it now."}
    if req["kind"] == "escalation" and answer == "decline":
        decline(req["id"], note)
        return {
            "ok": True,
            "text": f"Declined. The case is back with {req['asked_by_short'] or 'the buyer'}.",
        }
    if req["kind"] == "notify" and answer in ("got_it", "done"):
        acknowledge(req["id"], answer)
        return {"ok": True, "text": "Thanks, marked done." if answer == "done" else "Thanks."}
    raise HTTPException(400, "unknown answer")


@router.get("/api/cards/{card_id}/signoff/open")
def open_in_app(card_id: str):
    """
    The sign-off page for someone signed in to the app: a valid link to the newest unanswered
    request of the case, without sending anything (the push went out when it was passed on).
    """
    api = _api()
    req = latest_for(card_id)
    if req is None or req.get("answer"):
        raise HTTPException(409, "Nothing is waiting on anyone here.")
    cur = api.now()
    with _lock:
        tokens = {
            k: v for k, v in _tokens().items() if (api.parse_ts(v.get("expires_at")) or cur) > cur
        }
        token = next((k for k, v in tokens.items() if v.get("request") == req["id"]), None)
        if token is None:
            token = secrets.token_urlsafe(18)
            tokens[token] = {
                "request": req["id"],
                "created_at": cur.isoformat(),
                "expires_at": _expires().isoformat(),
            }
            api.write_json(TOKENS, tokens)
    return {"path": f"/ok/{token}"}


@router.post("/api/cards/{card_id}/signoff/resend")
def resend(card_id: str):
    """Send the newest request of a case again (fresh link); for an escalation passed on before
    links existed, create its request first."""
    api = _api()
    card = api._card(card_id)
    req = latest_for(card_id)
    if req and req.get("answer"):
        raise HTTPException(409, "This was answered already.")
    if req is None:
        if card["status"] != "escalated":
            raise HTTPException(409, "Nothing is waiting on anyone here.")
        act = next(
            (
                a
                for a in reversed(api.read_list("actions.json"))
                if a.get("decision_id") == card_id and a.get("kind") == "escalate"
            ),
            {},
        )
        out = escalation(
            card,
            act.get("option"),
            str(act.get("note") or ""),
            api.viewer_name(api.portal_root()),
            act.get("at"),
        )
        if out is None:
            raise HTTPException(409, "There is nobody named to approve this.")
        start_simulation(out["request"])
        return out
    out = send(req["id"])
    if req["kind"] == "escalation":
        start_simulation(req["id"])
    return out
