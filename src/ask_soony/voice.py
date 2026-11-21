"""
Voice conversation about a decision card, in the browser (OpenAI gpt-realtime over WebRTC).

The browser asks this route for a short-lived OpenAI client secret; the API key never leaves the
server. The secret carries the session: the card as instructions, a voice, noise handling and the
`record_decision` function. The browser then talks to OpenAI directly and records the decision
through the dashboard's normal card action endpoint.
"""

import json
import os

import httpx
from fastapi import APIRouter, HTTPException

from ask_soony import cardtools
from ask_soony.config import load_dotenv

router = APIRouter()

DECISION_TOOL = {
    "type": "function",
    "name": "record_decision",
    "description": "Record the person's decision on this card as soon as they have decided.",
    "parameters": {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["approve", "escalate", "reject"]},
            "note": {"type": "string", "description": "Short reason or condition, if any"},
        },
        "required": ["decision"],
    },
}


def _text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "; ".join(f"{k}: {v}" for k, v in value.items() if v not in (None, "", []))
    return json.dumps(value, ensure_ascii=False)


def instructions(card: dict) -> str:
    options = "\n".join(f"- {_text(o)}" for o in card.get("options") or []) or "- none listed"
    facts = [
        f"Title: {card.get('title', '')}",
        f"Edition: {card.get('edition', '')}, part: {card.get('part', '')}",
        f"Copies at risk: {card.get('at_risk')} (if we act: {card.get('at_risk_if_acted')})",
        f"Act by: {card.get('act_by') or 'not given'}",
        f"Runs dry: {card.get('runs_dry') or 'not given'}",
        f"System says: {card.get('system_says', '')}",
        f"Documents say: {card.get('paper_says', '')}",
        f"Options:\n{options}",
        f"Supplier reply so far: {(card.get('action_status') or {}).get('reply') or 'none yet'}",
    ]
    return (
        "You are the procurement assistant of the dashboard, talking to a buyer about one decision "
        "card that threatens the product launch. Speak English, calmly and briefly.\n\n"
        + "\n".join(facts)
        + "\n\nStart by stating the problem in two short sentences and the recommended option. "
        "Answer questions only with the facts above; if you do not know, say so. Then ask for a "
        "decision: approve the recommended action, escalate to the next person, or reject. "
        "When they decide, call record_decision, confirm in one sentence and say goodbye.\n\n"
        "Tools: use get_card_details when asked about sources, costs or what would be sent. If the "
        "person approves and the card has a drafted supplier message, read out recipient and gist, "
        "ask 'Shall I send it now?' and only after a clear yes call send_supplier_email with "
        "confirmed true; then tell them it was sent. "
        "Use escalate when they want the next person to decide. "
        "Use record_decision for approve or reject without sending anything.\n\n"
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

    card = next((c for c in api.get_state()["cards"] if c.get("id") == card_id), None)
    if card is None:
        raise HTTPException(404, "card not found")
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
        "tools": [DECISION_TOOL, *cardtools.TOOLS],
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
