"""
Tools the voice assistant can use on a decision card. They run on the server; the browser only
relays the model's tool calls. Every action is recorded through the dashboard contract
(decisions.json, actions.json, feed.json), so it shows on the card and in the activity feed.

- get_card_details: evidence, options and drafted messages of the card (read-only)
- send_supplier_email: send one of the card's drafted messages, only after explicit confirmation
  and only to an address listed in the portal's supplier profiles
- escalate: hand the card to the next person (decision note + push to subscribed devices)
"""

import json
import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ask_soony.config import load_dotenv

router = APIRouter()
ROOT = Path(__file__).resolve().parents[2]

TOOLS = [
    {
        "type": "function",
        "name": "get_card_details",
        "description": "Look up the evidence, options and drafted supplier messages of this card. "
        "Use it before answering questions about sources, costs or what would be sent.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "send_supplier_email",
        "description": "Send one of the card's drafted messages to the supplier. Only call this "
        "after reading out recipient and gist and the person explicitly said yes to sending it.",
        "parameters": {
            "type": "object",
            "properties": {
                "message_index": {
                    "type": "integer",
                    "description": "Which drafted message, 0 for the first",
                },
                "confirmed": {
                    "type": "boolean",
                    "description": "True only if the person explicitly confirmed sending",
                },
            },
            "required": ["message_index", "confirmed"],
        },
    },
    {
        "type": "function",
        "name": "escalate",
        "description": "Hand this card to the next person, e.g. when the decision is above the "
        "person's authority or they cannot decide now.",
        "parameters": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
        },
    },
]


def _card(card_id: str) -> dict:
    from ask_soony import api  # late import: api includes this router

    card = next((c for c in api.get_state()["cards"] if c.get("id") == card_id), None)
    if card is None:
        raise HTTPException(404, "card not found")
    return card


def supplier_addresses() -> set[str]:
    """Every email address found in the portal's supplier profiles (CSV or JSON, any layout)."""
    found: set[str] = set()
    for path in (ROOT / "data" / "portal" / "suppliers").glob("*/profile.*"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found |= {m.lower() for m in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text)}
    return found


def _send_mail(to: str, subject: str, body: str) -> str:
    load_dotenv()
    user = os.environ.get("ASK_MAIL_ADDRESS", "")
    password = os.environ.get("ASK_MAIL_PASSWORD", "").replace(" ", "")
    host = os.environ.get("ASK_SMTP_HOST", "")
    if not (user and password and host):
        raise HTTPException(503, "mail account not configured")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = user, to, subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = mid = make_msgid(domain=user.split("@")[-1])
    msg.set_content(body)
    with smtplib.SMTP(host, 587, timeout=30) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(user, password)
        s.send_message(msg)
    return mid


def _record(card_id: str, action: str, note: str | None, actor: str, text: str) -> None:
    from ask_soony import api

    state = api._state_dir()
    decisions = api._read(state / "decisions.json", {})
    decisions[card_id] = {"action": action, "note": note, "ts": api._now()}
    api._write(state / "decisions.json", decisions)
    api._add_event(actor, text, card_id)


class ToolCall(BaseModel):
    name: str
    arguments: dict = {}


@router.post("/api/cards/{card_id}/tool")
def run_tool(card_id: str, call: ToolCall):
    card = _card(card_id)
    args = call.arguments or {}

    if call.name == "get_card_details":
        messages = [
            {
                "index": i,
                "to_role": m.get("to_role"),
                "to": m.get("to_email"),
                "subject": m.get("subject"),
                "body": (m.get("body") or "")[:600],
            }
            for i, m in enumerate(card.get("messages") or [])
        ]
        return {
            "evidence": (card.get("evidence") or [])[:6],
            "options": card.get("options") or [],
            "messages": messages,
            "status": {
                k: v for k, v in (card.get("action_status") or {}).items() if k != "reply_text"
            },
        }

    if call.name == "send_supplier_email":
        if not args.get("confirmed"):
            return {"ok": False, "error": "Not sent: ask the person to confirm first."}
        pending = card.get("human_decision") or {}
        if pending.get("action") == "edit":
            return {
                "ok": False,
                "error": "Not sent: the draft was edited in the dashboard and must be "
                "re-issued by its module first.",
            }
        msgs = card.get("messages") or []
        i = int(args.get("message_index", 0))
        if not 0 <= i < len(msgs):
            return {"ok": False, "error": f"No drafted message {i}; the card has {len(msgs)}."}
        m = msgs[i]
        to = (m.get("to_email") or "").strip().lower()
        if to not in supplier_addresses():
            return {
                "ok": False,
                "error": f"{to or 'empty address'} is not a known supplier address; not sent.",
            }
        mid = _send_mail(to, m.get("subject") or card.get("title", ""), m.get("body") or "")
        from ask_soony import api

        state = api._state_dir()
        actions = api._read(state / "actions.json", {})
        if isinstance(actions, list):
            actions = {r["card_id"]: r for r in actions if isinstance(r, dict) and "card_id" in r}
        actions[card_id] = {
            "card_id": card_id,
            "status": "sent",
            "at": api._now(),
            "message_id": mid,
        }
        api._write(state / "actions.json", actions)
        outbox = ROOT / "data" / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        (outbox / f"{mid.strip('<>').split('@')[0]}.json").write_text(
            json.dumps(
                {
                    "message_id": mid,
                    "to": to,
                    "subject": m.get("subject"),
                    "body": m.get("body"),
                    "card_id": card_id,
                    "date": api._now(),
                    "via": "voice assistant",
                },
                indent=2,
            )
        )
        _record(
            card_id,
            "allow_once",
            f"sent by voice: {m.get('subject')}",
            "voice assistant",
            f"sent '{m.get('subject')}' to {to}",
        )
        return {"ok": True, "sent_to": to, "subject": m.get("subject")}

    if call.name == "escalate":
        reason = str(args.get("reason") or "escalated by voice")[:300]
        _record(card_id, "edit", f"escalate: {reason}", "voice assistant", f"escalated: {reason}")
        try:
            from ask_soony import push

            push.send("⬆️ Escalated launch alert", f"{card.get('title', '')}: {reason}", card_id)
        except Exception:  # no devices or push unavailable: the decision is recorded anyway
            pass
        return {"ok": True, "escalated": True}

    raise HTTPException(400, f"unknown tool {call.name}")
