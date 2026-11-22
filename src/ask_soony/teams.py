"""
Launch alerts in Microsoft Teams, where buyers already work (Lio's own channel).

Posts an Adaptive Card to a Teams channel through a "Workflows" incoming webhook
(ASK_TEAMS_WEBHOOK; in Teams: Workflows -> "Post to a channel when a webhook request is
received"). The card states the problem and what does not match; its buttons open the decision in
the app (approve, talk it through, evidence), so the decision itself stays in one place with its
audit trail.

- New open inbox cards (red or amber) are posted once by a background loop (ASK_TEAMS=on)
- Supplier replies tied back to a card are posted as a follow-up
- POST /api/teams/test posts one card on demand
"""

import json
import logging
import os
import threading
import time
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ask_soony.config import load_dotenv

log = logging.getLogger("ask_soony.teams")
router = APIRouter()
LEVEL_COLOR = {"red": "Attention", "amber": "Warning", "grey": "Default", "blue": "Accent"}


def _webhook() -> str:
    load_dotenv()
    return os.environ.get("ASK_TEAMS_WEBHOOK", "").strip()


def _app_url() -> str:
    return (
        os.environ.get("ASK_APP_URL")
        or os.environ.get("ASK_PUSH_CONTACT")
        or os.environ.get("ASK_PUBLIC_URL")
        or ""
    ).rstrip("/")


def _num(n) -> str:
    try:
        return f"{float(n):,.0f}"
    except (TypeError, ValueError):
        return str(n or "")


def card_payload(card: dict, reply: dict | None = None) -> dict:
    """The Teams message for one inbox card (Adaptive Card 1.4)."""
    link = f"{_app_url()}/d/{quote(card['id'])}"
    level = card.get("level") or "amber"
    evidence = card.get("evidence") or []
    system = next((e for e in evidence if "system" in str(e.get("source", "")).lower()), None)
    paper = next(
        (e for e in evidence if e is not system and "paper" in str(e.get("source", "")).lower()),
        None,
    )
    body: list[dict] = [
        {
            "type": "TextBlock",
            "text": (
                "Supplier replied"
                if reply
                else "Launch at risk"
                if level == "red"
                else "Needs a decision"
            ),
            "weight": "Bolder",
            "color": "Good" if reply else LEVEL_COLOR.get(level, "Default"),
            "size": "Small",
        },
        {
            "type": "TextBlock",
            "text": card.get("headline", ""),
            "weight": "Bolder",
            "size": "Large",
            "wrap": True,
        },
    ]
    if reply:
        body.append(
            {
                "type": "TextBlock",
                "wrap": True,
                "text": f"**{reply.get('from', 'Supplier')}:** {reply.get('summary', '')}",
            }
        )
    else:
        facts = []
        if card.get("number"):
            facts.append(
                {"title": card.get("number_label") or "At risk", "value": _num(card["number"])}
            )
        if card.get("deadline"):
            facts.append(
                {"title": "Decide by", "value": str(card["deadline"])[:16].replace("T", " ")}
            )
        if card.get("supplier_name"):
            facts.append({"title": "Supplier", "value": card["supplier_name"]})
        if facts:
            body.append({"type": "FactSet", "facts": facts})
        if system and paper:
            body.append(
                {
                    "type": "ColumnSet",
                    "columns": [
                        {
                            "type": "Column",
                            "width": "stretch",
                            "items": [
                                {
                                    "type": "TextBlock",
                                    "text": "THE SYSTEM SAYS",
                                    "size": "Small",
                                    "isSubtle": True,
                                },
                                {
                                    "type": "TextBlock",
                                    "text": str(system.get("value", "")),
                                    "weight": "Bolder",
                                    "wrap": True,
                                },
                            ],
                        },
                        {
                            "type": "Column",
                            "width": "stretch",
                            "items": [
                                {
                                    "type": "TextBlock",
                                    "text": "THE DOCUMENT SAYS",
                                    "size": "Small",
                                    "isSubtle": True,
                                    "color": "Attention",
                                },
                                {
                                    "type": "TextBlock",
                                    "text": str(paper.get("value", "")),
                                    "weight": "Bolder",
                                    "wrap": True,
                                    "color": "Attention",
                                },
                            ],
                        },
                    ],
                }
            )
        rec = next((o for o in card.get("options") or [] if o.get("recommended")), None)
        if rec:
            body.append(
                {
                    "type": "TextBlock",
                    "wrap": True,
                    "spacing": "Medium",
                    "text": f"**Recommended:** {rec.get('action', '')}"
                    + (f" · {rec['cost']}" if rec.get("cost") else ""),
                }
            )
    actions = [
        {"type": "Action.OpenUrl", "title": "Review and approve", "url": link},
        {"type": "Action.OpenUrl", "title": "Talk it through", "url": f"{link}?talk=1"},
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
                    "msteams": {"width": "Full"},
                    "body": body,
                    "actions": actions,
                },
            }
        ],
    }


def post(card: dict, reply: dict | None = None) -> bool:
    url = _webhook()
    if not url:
        return False
    r = httpx.post(url, json=card_payload(card, reply), timeout=20)
    if r.status_code >= 300:
        log.warning("teams post failed: %s %s", r.status_code, r.text[:200])
        return False
    return True


def notify_reply(card_id: str, who: str, summary: str) -> None:
    """Follow-up when a supplier answered (called by the reply poller)."""
    if not _webhook():
        return
    from ask_soony import api

    try:
        post(api._card(card_id), {"from": who, "summary": summary})
    except Exception as exc:  # Teams is a convenience; the reply is on the card anyway
        log.warning("teams reply post failed: %s", exc)


def check_once() -> list[str]:
    """Post open inbox cards (red or amber) that were not posted yet."""
    if not _webhook():
        return []
    from ask_soony import api

    state = api.build_state()
    seen = set(api.read_json("teams_posted.json", []))
    first_run = not (api.state_dir() / "teams_posted.json").exists()
    posted = []
    for card in state["cards"]:
        if card["id"] in seen or card["id"] not in state["inbox_ids"]:
            continue
        if card.get("level") not in ("red", "amber"):
            continue
        # The first run only remembers what is already there (no flood when switching it on),
        # unless ASK_TEAMS_BACKFILL=on.
        if first_run and os.environ.get("ASK_TEAMS_BACKFILL", "").lower() not in ("1", "on"):
            seen.add(card["id"])
            continue
        if post(card):
            seen.add(card["id"])
            posted.append(card["id"])
    api.write_json("teams_posted.json", sorted(seen))
    return posted


def _loop(interval: int) -> None:
    while True:
        try:
            for cid in check_once():
                log.info("posted %s to Teams", cid)
        except Exception as exc:
            log.warning("teams check failed: %s", exc)
        time.sleep(interval)


def start_background() -> None:
    # Process environment only, like ASK_BACKGROUND: loading .env at import time would pull its
    # secrets into every process that imports the app, tests included.
    if os.environ.get("ASK_TEAMS", "").lower() not in ("1", "on", "true", "yes"):
        return
    interval = int(os.environ.get("ASK_TEAMS_POLL_SECONDS", "60"))
    threading.Thread(target=_loop, args=(interval,), daemon=True, name="teams").start()


class TestPost(BaseModel):
    card_id: str | None = None


@router.post("/api/teams/test")
def teams_test(body: TestPost):
    from ask_soony import api

    if not _webhook():
        raise HTTPException(409, "ASK_TEAMS_WEBHOOK is not set")
    state = api.build_state()
    card = next((c for c in state["cards"] if c["id"] == body.card_id), None) or next(
        (c for c in state["cards"] if c["id"] in state["inbox_ids"]), None
    )
    if card is None:
        raise HTTPException(404, "no card to post")
    if not post(card):
        raise HTTPException(502, "Teams refused the post (see the server log)")
    return {"posted": card["id"]}


def preview(card: dict) -> str:
    """The card JSON, for pasting into the Adaptive Card designer."""
    return json.dumps(card_payload(card)["attachments"][0]["content"], indent=2)
