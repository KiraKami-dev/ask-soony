"""
Voice conversation about a decision card, in the browser (OpenAI gpt-realtime over WebRTC).

The browser asks this route for a short-lived OpenAI client secret; the API key never leaves the
server. The secret carries the session: the card as instructions, a voice, noise handling and the
card tools (cardtools.TOOLS). The browser talks to OpenAI directly and relays tool calls to
/api/cards/{id}/tool, which decides through the inbox's own approve / escalate / dismiss.
"""

import json
import os

import httpx
from fastapi import APIRouter, HTTPException

from ask_soony import cardtools
from ask_soony.config import load_dotenv

router = APIRouter()


def _text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "; ".join(f"{k}: {v}" for k, v in value.items() if v not in (None, "", []))
    return json.dumps(value, ensure_ascii=False)


def instructions(card: dict) -> str:
    options = (
        "\n".join(
            f"- option {o.get('index', i)}: {o.get('action')}"
            + (f" (cost: {o['cost']})" if o.get("cost") else "")
            + (f" (effect: {o['effect']})" if o.get("effect") else "")
            + (
                f" [sends an email to {o['to']}]"
                if o.get("channel") == "email" and o.get("to")
                else ""
            )
            for i, o in enumerate(card.get("options") or [])
        )
        or "- none listed"
    )
    evidence = "\n".join(
        f"- {e.get('source') or ''}: "
        + str(e.get("value") or e.get("translation") or e.get("quote") or "")
        for e in (card.get("evidence") or [])[:6]
    )
    facts = [
        f"Headline: {card.get('headline', '')}",
        f"Summary: {card.get('summary') or ''}",
        f"Supplier: {card.get('supplier_name') or 'n/a'}, part: {card.get('part') or 'n/a'}",
        f"{card.get('number_label') or 'At risk'}: {card.get('number')}",
        f"Decide by: {card.get('deadline') or 'not given'}",
        f"Runs dry: {card.get('runs_dry') or 'n/a'}",
        f"Status: {card.get('status')}; approver: {card.get('approver') or 'the buyer'}",
        f"Evidence:\n{evidence or '- none'}",
        f"Options:\n{options}",
        f"Supplier reply so far: {(card.get('ui') or {}).get('reply') or 'none yet'}",
    ]
    return (
        "You are the procurement assistant of the dashboard, talking to a buyer about one decision "
        "card that threatens the product launch. Speak English, calmly and briefly.\n\n"
        + "\n".join(facts)
        + "\n\nStart by stating the problem in two short sentences and the recommended option. "
        "Answer questions only with the facts above; if you do not know, say so. Then ask for a "
        "decision: approve the recommended action, escalate to the next person, or reject. "
        "Tools: use get_card_details when asked about sources, costs or what would be sent. "
        "Every decision takes two steps. First call prepare_decision with the decision and "
        "option; read its summary out in your own words (who gets what, the gist of any email, "
        "the cost) and ask 'Shall I go ahead?'. Then STOP and wait for the person to answer. "
        "Only after they clearly say yes, call decide with the same decision and option, the "
        "token and confirmed true. Never call decide in the same turn as prepare_decision. "
        "Then confirm in one sentence (say it was sent if sent_to is set) and say goodbye.\n"
        "When you refer to a source or to the email, call show_on_screen so the person sees "
        "it (document, system, options or email).\n\n"
        "If the person says they want cases like this handled automatically next time (for "
        "example 'next time just do that'), make sure the card is decided first, then call "
        "teach_routine with their words. It only drafts a routine; you can never switch one on. "
        "When it returns ok, say: 'I've drafted it as a routine. Check it on screen before it "
        "starts.' and name it in plain words. If it says to decide first, ask for the decision "
        "on this card first.\n\n"
        "The person may be in a noisy room. Only respond to what they say to you about this card. "
        "Ignore background conversations, other languages and fragments; if unsure, ask: 'Sorry, "
        "was that for me?' Never record a decision from background speech."
    )


@router.post("/api/cards/{card_id}/voice")
def voice_session(card_id: str):
    load_dotenv()
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise HTTPException(503, "OPENAI_API_KEY is not set")
    from ask_soony import api  # late import: api includes this router

    card = api._card(card_id)
    model = os.environ.get("OPENAI_REALTIME_MODEL", "gpt-realtime-2.1")
    session = {
        "type": "realtime",
        "model": model,
        "instructions": instructions(card),
        "audio": {
            "output": {"voice": os.environ.get("OPENAI_REALTIME_VOICE", "marin")},
            "input": {
                "noise_reduction": {"type": "near_field"},
                "transcription": {
                    "model": "gpt-4o-mini-transcribe",
                    "language": "en",
                    "prompt": "A buyer talking about a supply chain decision card.",
                },
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": float(os.environ.get("OPENAI_VAD_THRESHOLD", "0.8")),
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": 700,
                    "create_response": True,
                    "interrupt_response": False,
                },
            },
        },
        "tools": cardtools.TOOLS,
        "tool_choice": "auto",
    }
    resp = httpx.post(
        "https://api.openai.com/v1/realtime/client_secrets",
        headers={"Authorization": f"Bearer {key}"},
        json={"expires_after": {"anchor": "created_at", "seconds": 600}, "session": session},
        timeout=20,
    )
    if resp.status_code >= 300:
        raise HTTPException(502, f"OpenAI refused the session: {resp.text[:300]}")
    return {"client_secret": resp.json().get("value"), "model": model}
