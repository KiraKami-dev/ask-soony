"""
Web push for the installed dashboard (PWA): a critical card rings the phone with a notification;
tapping it opens the card with an "Answer" button that starts the voice conversation.

VAPID keys are generated once and kept in data/state/vapid.json (gitignored with data/).
Subscriptions are kept in data/state/push_subscriptions.json.
"""

import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


def _state_dir() -> Path:
    return Path(os.environ.get("ASK_STATE_DIR", "data/state"))


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def vapid() -> dict:
    """Load or create the VAPID key pair: PEM private key path and the public key for browsers."""
    path = _state_dir() / "vapid.json"
    pem = _state_dir() / "vapid_private.pem"
    if path.exists() and pem.exists():
        return json.loads(path.read_text())
    pem.parent.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    pem.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    os.chmod(pem, 0o600)
    public = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    data = {"public_key": _b64url(public), "private_pem": str(pem)}
    path.write_text(json.dumps(data))
    return data


def _subs_path() -> Path:
    return _state_dir() / "push_subscriptions.json"


def subscriptions() -> list[dict]:
    try:
        return json.loads(_subs_path().read_text())
    except (OSError, ValueError):
        return []


def send(title: str, body: str, card_id: str | None = None) -> dict:
    """Push to every subscribed device; drop subscriptions the push service reports as gone."""
    from pywebpush import WebPushException, webpush

    keys = vapid()
    claims_sub = os.environ.get("ASK_PUSH_CONTACT", "https://ask-soony.example.com")
    payload = json.dumps({"title": title, "body": body, "card_id": card_id})
    kept, sent, failed = [], 0, []
    for sub in subscriptions():
        try:
            webpush(
                sub,
                payload,
                vapid_private_key=keys["private_pem"],
                vapid_claims={"sub": claims_sub},
                ttl=600,
            )
            kept.append(sub)
            sent += 1
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status not in (404, 410):  # 404/410: subscription expired, forget it
                kept.append(sub)
            failed.append(str(status or e)[:120])
    _subs_path().write_text(json.dumps(kept))
    return {"sent": sent, "failed": failed}


@router.get("/api/push/key")
def push_key():
    return {"public_key": vapid()["public_key"]}


class Subscription(BaseModel):
    endpoint: str
    keys: dict


@router.post("/api/push/subscribe")
def push_subscribe(sub: Subscription):
    subs = [s for s in subscriptions() if s.get("endpoint") != sub.endpoint]
    subs.append(sub.model_dump())
    _subs_path().parent.mkdir(parents=True, exist_ok=True)
    _subs_path().write_text(json.dumps(subs))
    return {"devices": len(subs)}


class TestPush(BaseModel):
    card_id: str | None = None


@router.post("/api/push/test")
def push_test(body: TestPush):
    from ask_soony import api  # late import: api includes this router

    state = api.build_state()
    cards = state["cards"]
    inbox = [c for c in cards if c["id"] in state["inbox_ids"]]
    card = next((c for c in cards if c.get("id") == body.card_id), None) or (
        inbox[0] if inbox else None
    )
    if not subscriptions():
        raise HTTPException(409, "no device has enabled alerts yet")
    title = "🔴 Launch at risk"
    text = card["headline"] if card else "Test alert"
    return send(title, f"{text}. Tap to talk to the assistant.", card["id"] if card else None)
