"""
Phone call webhooks in the dashboard app, so the product runs as one server.

Twilio, OpenAI realtime and ElevenLabs call these from outside, so they are exempt from the
dashboard login and authenticate themselves instead: OpenAI with a signed webhook, Twilio and
ElevenLabs with the per-alert secret token embedded in the URL or body.
"""

import hmac
import json
import os
from html import escape

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from ask_soony.alerts import AlertBook
from ask_soony.config import Settings
from ask_soony.escalation import KEYPAD, TwilioCaller
from ask_soony.store import Store

router = APIRouter()


def _xml(body: str) -> Response:
    return Response(body, media_type="text/xml")


def _book() -> tuple[Store, AlertBook]:
    store = Store(Settings.from_env().data_dir)
    return store, AlertBook(store)


def _alert_for(book: AlertBook, alert_id: int, token: str):
    a = book.get(alert_id)
    return a if a and hmac.compare_digest(token, a.token) else None


@router.post("/hooks/openai")
async def openai_incoming(request: Request):
    from ask_soony.openai_calls import start_incoming, verify_webhook

    raw = await request.body()
    if not verify_webhook(request.headers, raw, os.environ.get("OPENAI_WEBHOOK_SECRET", "")):
        return PlainTextResponse("bad signature", 401)
    event = json.loads(raw or b"{}")
    if event.get("type") == "realtime.call.incoming":
        start_incoming(event, Settings.from_env().data_dir)
    return PlainTextResponse("ok")


@router.post("/hooks/openai/twiml/{alert_id}")
def openai_twiml(alert_id: int, token: str = ""):
    from ask_soony.openai_calls import OpenAICaller

    store, book = _book()
    try:
        a = _alert_for(book, alert_id, token)
        if not a:
            return PlainTextResponse("forbidden", 403)
        return _xml(OpenAICaller.from_env().twiml(a))
    finally:
        store.db.close()


@router.post("/hooks/twilio/twiml/{alert_id}")
def twilio_twiml(alert_id: int, token: str = ""):
    store, book = _book()
    try:
        a = _alert_for(book, alert_id, token)
        if not a:
            return PlainTextResponse("forbidden", 403)
        public = os.environ.get("ASK_PUBLIC_URL", "").rstrip("/")
        return _xml(TwilioCaller("", "", "").twiml(a, public))
    finally:
        store.db.close()


@router.post("/hooks/twilio/{alert_id}")
async def twilio_keypad(alert_id: int, request: Request, token: str = ""):
    from urllib.parse import parse_qs

    form = parse_qs((await request.body()).decode(errors="replace"))
    decision = KEYPAD.get(form.get("Digits", [""])[0])
    store, book = _book()
    try:
        if not decision:
            say = "Sorry, that was not a valid choice. Please decide in the dashboard."
        else:
            try:
                book.decide(
                    alert_id,
                    decision,
                    by="phone …" + form.get("To", [""])[0][-2:],
                    channel="call",
                    token=token,
                )
            except (KeyError, PermissionError):
                return PlainTextResponse("forbidden", 403)
            say = f"Thank you. Your decision, {decision}, is recorded."
        return _xml(f"<Response><Say>{escape(say)}</Say></Response>")
    finally:
        store.db.close()


@router.post("/hooks/elevenlabs/decision")
async def elevenlabs_decision(request: Request):
    store, book = _book()
    try:
        data = json.loads(await request.body() or b"{}")
        a = book.decide(
            int(data["alert_id"]),
            str(data["decision"]).lower(),
            by=str(data.get("by") or "voice agent"),
            channel="call",
            token=str(data.get("token", "")),
        )
    except PermissionError:
        return JSONResponse({"ok": False}, 403)
    except (KeyError, ValueError, TypeError) as e:
        return JSONResponse({"ok": False, "error": str(e)}, 400)
    finally:
        store.db.close()
    return {"ok": True, "status": a.status}
