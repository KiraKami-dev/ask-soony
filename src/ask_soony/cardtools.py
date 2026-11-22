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
        "name": "prepare_decision",
        "description": "Step 1 of every decision. Returns exactly what would happen (who gets "
        "what, the gist of any email, the cost) and a one-time token. Read the summary out, ask "
        "the person, and wait for their answer before calling decide.",
        "parameters": {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": ["approve", "escalate", "reject"]},
                "option_index": {"type": "integer", "description": "Which option, 0 = recommended"},
                "note": {"type": "string", "description": "Short reason or condition, if any"},
            },
            "required": ["decision"],
        },
    },
    {
        "type": "function",
        "name": "decide",
        "description": "Step 2: carry out a decision that prepare_decision summarised and the "
        "person then clearly said yes to. Needs the token from prepare_decision; approve carries "
        "out the option (an email to a supplier is sent).",
        "parameters": {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": ["approve", "escalate", "reject"]},
                "option_index": {"type": "integer", "description": "Which option, 0 = recommended"},
                "note": {"type": "string", "description": "Short reason or condition, if any"},
                "token": {"type": "string", "description": "The token from prepare_decision"},
                "confirmed": {
                    "type": "boolean",
                    "description": "True only if the person explicitly said yes after the summary",
                },
            },
            "required": ["decision", "token", "confirmed"],
        },
    },
    {
        "type": "function",
        "name": "show_on_screen",
        "description": "On the website, show the person what you are talking about: the "
        "document side with its highlighted line, the system side (SAP row), the other options, "
        "or the email that would be sent. Has no effect on a phone call.",
        "parameters": {
            "type": "object",
            "properties": {
                "what": {"type": "string", "enum": ["document", "system", "options", "email"]}
            },
            "required": ["what"],
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


def _send_mail_in_thread(
    to: str, subject: str, body: str, in_reply_to: str, references: str
) -> str:
    """Like _send_mail, with In-Reply-To and References so the mail lands in the same thread."""
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
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.set_content(body)
    with smtplib.SMTP(host, 587, timeout=30) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(user, password)
        s.send_message(msg)
    return mid


def send_followup(
    to: str, subject: str, body: str, in_reply_to: str, references: list[str] | None = None
) -> dict:
    """
    Send a follow-up in the same email thread ("Re: <subject>", In-Reply-To/References) to a
    supplier address from the portal's profiles. Returns {message_id, to, subject, sent_body}.
    """
    to = (to or "").strip().lower()
    body = (body or "").strip()
    if not body or to not in supplier_addresses():
        raise HTTPException(400, "follow-ups go only to known supplier addresses")
    subject = subject if re.match(r"^\s*re\s*:", subject or "", re.I) else f"Re: {subject}"
    refs = " ".join(r for r in (references or [in_reply_to]) if r)
    mid = _send_mail_in_thread(to, subject, body, in_reply_to, refs)
    return {"message_id": mid, "to": to, "subject": subject, "sent_body": body}


def deliver(card: dict, option: dict, text: str | None) -> dict | None:
    """Send the approved option if it is an email to a known supplier, or a question to the site
    (channel "floor": Teams and push, see floor.ask); None if nothing was sent."""
    if option.get("channel") == "floor" and card.get("site_question"):
        from ask_soony import floor

        return floor.ask(card)
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


TOKEN_TTL_S = 600


def _summary(card: dict, decision: str, idx: int, note: str) -> str:
    """What a decision would do, in words the assistant reads out before asking."""
    if decision == "escalate":
        return f"Pass this to {card.get('approver') or 'the approver'} with a short brief."
    if decision == "reject":
        return "Take this off the list. Nothing is sent."
    opt = (card.get("options") or [])[idx]
    to = opt.get("to") or ""
    cost = opt.get("cost")
    cost_txt = "" if not cost or str(cost).lower() in ("none", "no cost") else f" Cost: {cost}."
    if opt.get("channel") == "email" and "@" in to:
        gist = " ".join((opt.get("draft") or "").split())[:220]
        return f"Send an email to {card.get('supplier_name') or to}: {gist}{cost_txt}"
    return f"{opt.get('action') or 'Approve the recommendation'}.{cost_txt}"


def _issue_token(card_id: str, decision: str, idx: int) -> str:
    import time
    import uuid

    from ask_soony import api

    tokens = {
        k: v
        for k, v in api.read_json("voice_tokens.json", {}).items()
        if time.time() - v.get("t", 0) < TOKEN_TTL_S
    }
    tok = uuid.uuid4().hex[:12]
    tokens[tok] = {"card": card_id, "decision": decision, "option": idx, "t": time.time()}
    api.write_json("voice_tokens.json", tokens)
    return tok


def _use_token(tok: str, card_id: str, decision: str, idx: int) -> bool:
    """One-time: valid only for the prepared card, decision and option, within the TTL."""
    import time

    from ask_soony import api

    tokens = api.read_json("voice_tokens.json", {})
    entry = tokens.pop(tok, None) if tok else None
    api.write_json("voice_tokens.json", tokens)
    return bool(
        entry
        and time.time() - entry.get("t", 0) < TOKEN_TTL_S
        and entry.get("card") == card_id
        and entry.get("decision") == decision
        and (decision != "approve" or entry.get("option") == idx)
    )


@router.post("/api/cards/{card_id}/tool")
def run_tool(card_id: str, call: ToolCall):
    from ask_soony import api

    card = api._card(card_id)
    args = call.arguments or {}
    if call.name == "get_card_details":
        return _details(card)
    if call.name == "teach_routine":
        return teach_routine(card, str(args.get("instruction") or "").strip()[:300])
    if call.name == "show_on_screen":  # handled by the browser; harmless if relayed here
        return {"ok": True, "shown": str(args.get("what") or "")}
    if call.name not in ("prepare_decision", "decide"):
        raise HTTPException(400, f"unknown tool {call.name}")

    decision = str(args.get("decision") or "")
    note = str(args.get("note") or "").strip()[:300]
    idx = int(args.get("option_index") or 0)
    if card.get("status") not in ("open", None):
        return {"ok": False, "error": f"Already {card.get('status')}; nothing changed."}
    if decision not in ("approve", "escalate", "reject"):
        return {"ok": False, "error": f"unknown decision {decision}"}
    if decision == "approve" and not 0 <= idx < len(card.get("options") or []):
        return {"ok": False, "error": f"No option {idx}."}
    if call.name == "prepare_decision":
        return {
            "ok": True,
            "summary": _summary(card, decision, idx, note),
            "token": _issue_token(card_id, decision, idx),
            "next": "Read the summary out, ask 'Shall I go ahead?', and wait for a clear yes.",
        }
    # decide: only with a token from prepare_decision for this exact decision, after a yes.
    if not args.get("confirmed"):
        return {"ok": False, "error": "Not done: ask the person to confirm first."}
    if not _use_token(str(args.get("token") or ""), card_id, decision, idx):
        return {
            "ok": False,
            "error": "Not done: call prepare_decision first, read the summary out and wait for "
            "a clear yes.",
        }
    if decision == "approve":
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

            push.send("Escalated", f"{card.get('headline', '')}", card_id)
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
