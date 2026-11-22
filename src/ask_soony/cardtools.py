"""
What the alert agent does with an inbox card, for the voice assistant and the Approve button alike.

- deliver(): the one send path. If the approved option is an email to an address listed in the
  portal's supplier profiles, send it from the agent mailbox and return the message id. Anything
  else (internal roles, no draft) is only recorded, never sent.
- POST /api/cards/{id}/tool: the voice assistant's tools. They call the inbox's own approve /
  escalate / dismiss, so a spoken "yes" and a click behave the same and land in the same
  actions.json and decision_status.json. teach_routine only drafts a routine from a decided card
  (routine_drafts.json, GET /api/routines/drafts); turning it on stays a click under Routines.
"""

import os
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ask_soony.config import load_dotenv

router = APIRouter()

TOOLS = [
    {
        "type": "function",
        "name": "get_card_details",
        "description": "Look up this card's options (with recipient and draft), evidence and "
        "status. Use it before answering questions about sources, costs or what would be sent.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "decide",
        "description": "Record the person's decision on this card. approve carries out the chosen "
        "option: if it is an email to a supplier, it is sent. Only approve after reading out what "
        "will happen (recipient and gist for an email) and the person clearly said yes.",
        "parameters": {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": ["approve", "escalate", "reject"]},
                "option_index": {"type": "integer", "description": "Which option, 0 = recommended"},
                "note": {"type": "string", "description": "Short reason or condition, if any"},
                "confirmed": {
                    "type": "boolean",
                    "description": "True only if the person explicitly confirmed",
                },
            },
            "required": ["decision", "confirmed"],
        },
    },
    {
        "type": "function",
        "name": "teach_routine",
        "description": "The person wants cases like this handled automatically next time (for "
        "example 'next time just do that'). Drafts a routine from the decision on this card for "
        "them to check on screen; it does not switch anything on. Only after the card is decided.",
        "parameters": {
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": "What the person said they want automated, in their words",
                }
            },
            "required": ["instruction"],
        },
    },
]


def supplier_addresses() -> set[str]:
    """Every email address in the portal's supplier profiles (CSV or JSON, any layout)."""
    from ask_soony import api

    root = api.portal_root()
    found: set[str] = set()
    if root is None:
        return found
    for path in (root / "suppliers").glob("*/profile.*"):
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


def deliver(card: dict, option: dict, text: str | None) -> dict | None:
    """Send the approved option if it is an email to a known supplier; None if nothing was sent."""
    to = (option.get("to") or "").strip().lower()
    body = (text or option.get("draft") or "").strip()
    if option.get("channel") != "email" or not body or to not in supplier_addresses():
        return None
    subject = option.get("subject") or card.get("headline") or "Soony procurement"
    mid = _send_mail(to, subject, body)
    return {"message_id": mid, "to": to, "subject": subject, "sent_body": body}


class ToolCall(BaseModel):
    name: str
    arguments: dict = {}


def _details(card: dict) -> dict:
    ui = card.get("ui") or {}
    return {
        "headline": card.get("headline"),
        "summary": card.get("summary"),
        "status": card.get("status"),
        "approver": card.get("approver"),
        "options": [
            {
                "index": o.get("index", i),
                "action": o.get("action"),
                "cost": o.get("cost"),
                "effect": o.get("effect"),
                "channel": o.get("channel"),
                "to": o.get("to"),
                "draft": (o.get("draft") or "")[:600],
            }
            for i, o in enumerate(card.get("options") or [])
        ],
        "evidence": (card.get("evidence") or [])[:8],
        "supplier_reply": ui.get("reply"),
    }


@router.post("/api/cards/{card_id}/tool")
def run_tool(card_id: str, call: ToolCall):
    from ask_soony import api

    card = api._card(card_id)
    args = call.arguments or {}
    if call.name == "get_card_details":
        return _details(card)
    if call.name == "teach_routine":
        return teach_routine(card, str(args.get("instruction") or "").strip()[:300])
    if call.name != "decide":
        raise HTTPException(400, f"unknown tool {call.name}")

    decision = str(args.get("decision") or "")
    note = str(args.get("note") or "").strip()[:300]
    if card.get("status") not in ("open", None):
        return {"ok": False, "error": f"Already {card.get('status')}; nothing changed."}
    if decision == "approve":
        if not args.get("confirmed"):
            return {"ok": False, "error": "Not done: ask the person to confirm first."}
        idx = int(args.get("option_index") or 0)
        if not 0 <= idx < len(card.get("options") or []):
            return {"ok": False, "error": f"No option {idx}."}
        out = api.approve(card_id, {"option": idx, "via": "voice"})
        action = out["action"]
        return {
            "ok": True,
            "status": action.get("status"),
            "sent_to": action.get("to") if action.get("status") == "sent" else None,
            "offer_trust": out.get("offer_trust"),
        }
    if decision == "escalate":
        action = api.escalate(card_id, {"note": note or "escalated by voice", "via": "voice"})
        try:
            from ask_soony import push

            push.send("⬆️ Escalated launch alert", f"{card.get('headline', '')}", card_id)
        except Exception:  # no devices: the escalation is recorded anyway
            pass
        return {"ok": True, "status": "escalated", "to": action.get("to")}
    if decision == "reject":
        api.dismiss(card_id, {"note": note or "rejected by voice", "via": "voice"})
        return {"ok": True, "status": "dismissed"}
    return {"ok": False, "error": f"unknown decision {decision}"}


DRAFTS = "routine_drafts.json"


def teach_routine(card: dict, instruction: str) -> dict:
    """
    Draft a routine from the decision on this card, for the buyer to review under Routines.
    Never saves or enables a routine: that stays a click on screen.
    """
    from ask_soony import api, routines

    if card.get("status") in ("open", None):
        return {"ok": False, "error": "decide first"}
    try:
        out = routines.suggest(card["id"])
    except HTTPException as exc:
        return {"ok": False, "error": str(exc.detail)}
    routine = {**out.get("routine", {}), "enabled": False}
    drafts = api.read_json(DRAFTS, {})
    drafts = drafts if isinstance(drafts, dict) else {}
    drafts[card["id"]] = {
        "card_id": card["id"],
        "instruction": instruction,
        "at": api.now().isoformat(),
        "via": "voice",
        "routine": routine,
        "preview": out.get("preview"),
    }
    api.write_json(DRAFTS, drafts)
    name = routine.get("name") or "New routine"
    try:
        from ask_soony import push

        push.send("Routine draft ready", name, card["id"])
    except Exception:  # no devices: the draft is on screen anyway
        pass
    return {"ok": True, "name": name, "description": routine.get("description")}


@router.get("/api/routines/drafts")
def routine_drafts():
    """Routine drafts the voice assistant made, newest first, for the Routines screen."""
    from ask_soony import api

    drafts = api.read_json(DRAFTS, {})
    items = list(drafts.values()) if isinstance(drafts, dict) else []
    return {"drafts": sorted(items, key=lambda d: str(d.get("at")), reverse=True)}
