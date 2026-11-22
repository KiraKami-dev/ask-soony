"""
Alert calls with OpenAI's gpt-realtime over SIP.

Flow: Twilio calls the contact (our number) and, once answered, hands the call to OpenAI via
<Dial><Sip>. OpenAI sends `realtime.call.incoming` to /hooks/openai; we accept the call with the
alert as instructions and a `record_decision` function, then follow the call on the sideband
WebSocket: when the model calls the function we record the decision, let it confirm and hang up.
Audio never passes through our server.
"""

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

import httpx

from ask_soony.alerts import Alert, AlertBook, Contact
from ask_soony.store import Store, now_iso

API = "https://api.openai.com/v1/realtime/calls"

DECISION_TOOL = {
    "type": "function",
    "name": "record_decision",
    "description": "Record the person's decision on the alert as soon as they have decided.",
    "parameters": {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["approve", "escalate", "reject"]},
            "note": {
                "type": "string",
                "description": "Short reason or condition the person gave, if any",
            },
        },
        "required": ["decision"],
    },
}


def instructions(alert: Alert, contact_name: str) -> str:
    return (
        "You are the procurement alert assistant of Soony Interactive, calling about something that "
        "threatens the product launch. Speak English, calmly and briefly, like a competent colleague.\n"
        f"You are talking to {contact_name}.\n"
        f"Alert: {alert.title}\nDetails: {alert.message}\n"
        f"Proposed action: {alert.action or 'none'}\nDeadline: {alert.deadline or 'none given'}\n\n"
        "Start by greeting them and stating the alert in two short sentences, then the proposed "
        "action. Answer questions only with the facts above; if you do not know something, say so. "
        "Then ask for a decision: approve the action, escalate to the next person, or reject. As soon "
        "as they decide, call record_decision, confirm the decision in one sentence and say goodbye. "
        "If they cannot decide now, record escalate.\n\n"
        "The call may come from a noisy room. Only respond to what the person you called says to you "
        "about this alert. Ignore background conversations, other languages, fragments and anything "
        "unrelated to the alert: do not answer or comment on them. If you are unsure whether something "
        "was meant for you, say briefly: 'Sorry, was that for me? Please say approve, escalate or "
        "reject.' Never record a decision from background speech."
    )


# --- Twilio leg -----------------------------------------------------------------------------


class OpenAICaller:
    """Places the Twilio leg; OpenAI takes over the audio once the call is answered."""

    name = "openai"

    def __init__(
        self,
        sid: str,
        token: str,
        from_number: str,
        project_id: str,
        http: httpx.Client | None = None,
    ):
        self.sid, self.token, self.from_number, self.project_id = (
            sid,
            token,
            from_number,
            project_id,
        )
        self.http = http or httpx.Client(timeout=20.0)

    @classmethod
    def from_env(cls) -> "OpenAICaller":
        env = os.environ.get
        return cls(
            env("TWILIO_ACCOUNT_SID", ""),
            env("TWILIO_AUTH_TOKEN", ""),
            env("TWILIO_FROM_NUMBER", ""),
            env("OPENAI_PROJECT_ID", ""),
        )

    def twiml(self, alert: Alert) -> str:
        sip = f"sip:{self.project_id}@sip.api.openai.com;transport=tls?x-alert-id={alert.id}"
        return f"<Response><Dial><Sip>{xml_escape(sip)}</Sip></Dial></Response>"

    def call(self, alert: Alert, contact: Contact, public_url: str) -> str:
        if not public_url:
            raise RuntimeError("ASK_PUBLIC_URL is required for OpenAI calls (webhooks)")
        PendingCalls.add(alert.id, contact)
        resp = self.http.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Calls.json",
            auth=(self.sid, self.token),
            data={
                "To": contact.phone,
                "From": self.from_number,
                "Url": f"{public_url}/hooks/openai/twiml/{alert.id}?token={alert.token}",
            },
        )
        if resp.status_code >= 300:
            raise RuntimeError(f"Twilio call failed: HTTP {resp.status_code} {resp.text[:200]}")
        return f"twilio call {resp.json().get('sid', '?')} bridged to OpenAI for {contact.name}"


class PendingCalls:
    """Which alert an incoming OpenAI call belongs to, kept in the store's database."""

    path = Path(os.environ.get("ASK_DATA_DIR", "data/store")) / "ask-soony.db"

    @classmethod
    def _db(cls) -> sqlite3.Connection:
        db = sqlite3.connect(cls.path)
        db.execute(
            "CREATE TABLE IF NOT EXISTS pending_calls (alert_id INTEGER, contact TEXT, at REAL)"
        )
        return db

    @classmethod
    def add(cls, alert_id: int, contact: Contact) -> None:
        with cls._db() as db:
            db.execute(
                "INSERT INTO pending_calls VALUES (?, ?, ?)", (alert_id, contact.name, time.time())
            )

    @classmethod
    def match(cls, sip_headers: list[dict]) -> tuple[int, str] | None:
        """Prefer the alert id passed in the SIP URI; else the newest call placed in the last 3 minutes."""
        db = cls._db()
        wanted = None
        for h in sip_headers or []:
            if h.get("name", "").lower() in {"x-alert-id", "to"} and "x-alert-id=" in h.get(
                "value", ""
            ):
                wanted = (
                    h["value"].split("x-alert-id=")[1].split(";")[0].split("&")[0].split(">")[0]
                )
            elif h.get("name", "").lower() == "x-alert-id":
                wanted = h.get("value")
        row = None
        if wanted and wanted.isdigit():
            row = db.execute(
                "SELECT alert_id, contact FROM pending_calls WHERE alert_id = ? ORDER BY at DESC",
                (int(wanted),),
            ).fetchone()
        if row is None:
            row = db.execute(
                "SELECT alert_id, contact FROM pending_calls WHERE at > ? ORDER BY at DESC",
                (time.time() - 180,),
            ).fetchone()
        return (row[0], row[1]) if row else None


# --- webhook --------------------------------------------------------------------------------


def verify_webhook(headers, body: bytes, secret: str, tolerance: int = 300) -> bool:
    """Standard Webhooks signature check (webhook-id, webhook-timestamp, webhook-signature)."""
    wid, ts, sig = (
        headers.get("webhook-id"),
        headers.get("webhook-timestamp"),
        headers.get("webhook-signature"),
    )
    if not (wid and ts and sig and secret):
        return False
    try:
        if abs(time.time() - int(ts)) > tolerance:
            return False
    except ValueError:
        return False
    key = base64.b64decode(secret.removeprefix("whsec_"))
    expected = base64.b64encode(
        hmac.new(key, f"{wid}.{ts}.".encode() + body, hashlib.sha256).digest()
    ).decode()
    return any(hmac.compare_digest(expected, part.split(",", 1)[-1]) for part in sig.split())


def turn_detection() -> dict:
    """
    How the model decides that the caller spoke. `server` (default) ignores sound below a volume
    threshold, which filters quieter background voices; `semantic` judges by meaning instead.
    """
    # Default: semantic, so fragments of background talk ("Please.", a name) do not start a turn.
    if os.environ.get("OPENAI_TURN_DETECTION", "semantic") == "semantic":
        return {
            "type": "semantic_vad",
            "eagerness": "low",
            "create_response": True,
            "interrupt_response": False,
        }
    return {
        "type": "server_vad",
        "threshold": float(os.environ.get("OPENAI_VAD_THRESHOLD", "0.8")),
        "prefix_padding_ms": 300,
        "silence_duration_ms": 700,
        "create_response": True,
        "interrupt_response": False,
    }


def handle_incoming(event: dict, data_dir: Path) -> None:
    """Accept the call for its alert and follow it on the sideband (runs in a background thread)."""
    data = event.get("data", {})
    call_id = data.get("call_id")
    if not call_id:
        return
    key = os.environ.get("OPENAI_API_KEY", "")
    auth = {"Authorization": f"Bearer {key}"}
    store = Store(data_dir)
    book = AlertBook(store)
    match = PendingCalls.match(data.get("sip_headers", []))
    if not match:
        httpx.post(f"{API}/{call_id}/reject", headers=auth, json={"status_code": 486}, timeout=10)
        return
    alert_id, contact_name = match
    alert = book.get(alert_id)
    accept = httpx.post(
        f"{API}/{call_id}/accept",
        headers=auth,
        json={
            "type": "realtime",
            "model": os.environ.get("OPENAI_REALTIME_MODEL", "gpt-realtime-2.1"),
            "instructions": instructions(alert, contact_name),
            "audio": {
                "output": {"voice": os.environ.get("OPENAI_REALTIME_VOICE", "marin")},
                "input": {
                    "transcription": {"model": "gpt-4o-mini-transcribe"},
                    # Phone lines and stages are noisy: judge turns by meaning, filter noise and
                    # let the assistant finish its sentence instead of cancelling on every sound.
                    "noise_reduction": {"type": "near_field"},
                    "turn_detection": turn_detection(),
                },
            },
            "tools": [DECISION_TOOL],
            "tool_choice": "auto",
        },
        timeout=15,
    )
    book.log(
        alert_id,
        "call",
        "call",
        contact_name,
        f"openai accept HTTP {accept.status_code} {accept.text[:120]}",
    )
    if accept.status_code >= 300:
        return
    sideband(call_id, alert_id, contact_name, data_dir, auth)


def sideband(
    call_id: str,
    alert_id: int,
    contact_name: str,
    data_dir: Path,
    auth: dict,
    greet: bool = True,
    attempt: int = 0,
) -> None:
    from websockets.sync.client import connect

    book = AlertBook(Store(data_dir))
    decided_at = None
    active = False  # a response is being generated: never start a second one
    pending = False  # our own response.create waits for the running one to finish
    with connect(
        f"wss://api.openai.com/v1/realtime?call_id={call_id}", additional_headers=auth
    ) as ws:
        if greet:
            ws.send(json.dumps({"type": "response.create"}))  # greet first
        while True:
            try:
                msg = json.loads(ws.recv(timeout=600))
            except TimeoutError:
                book.log(alert_id, "call", "call", "openai", "sideband idle for 10 minutes")
                break
            except Exception as e:  # call ended or socket closed: record why
                book.log(
                    alert_id,
                    "call",
                    "call",
                    "openai",
                    f"sideband closed: {type(e).__name__} {e}"[:300],
                )
                # A dropped control connection is not the end of the call: reconnect (twice).
                if decided_at is None and attempt < 2 and "ConnectionClosed" in type(e).__name__:
                    book.log(alert_id, "call", "call", "openai", "reconnecting the sideband")
                    return sideband(
                        call_id, alert_id, contact_name, data_dir, auth, False, attempt + 1
                    )
                break
            kind = msg.get("type")
            if kind == "response.created":
                active = True
            elif kind == "response.done":
                active = False
                if pending:
                    pending = False
                    ws.send(json.dumps({"type": "response.create"}))
                    active = True
            # Readable conversation log and errors in the alert history.
            if kind in {"response.output_audio_transcript.done", "response.audio_transcript.done"}:
                book.log(
                    alert_id, "transcript", "call", "assistant", (msg.get("transcript") or "")[:500]
                )
            elif kind == "conversation.item.input_audio_transcription.completed":
                book.log(
                    alert_id,
                    "transcript",
                    "call",
                    contact_name,
                    (msg.get("transcript") or "")[:500],
                )
            elif kind == "error":
                book.log(alert_id, "error", "call", "openai", json.dumps(msg.get("error"))[:300])
            elif kind == "response.done":
                status = (msg.get("response") or {}).get("status")
                if status and status != "completed":
                    details = json.dumps((msg.get("response") or {}).get("status_details"))[:250]
                    book.log(alert_id, "error", "call", "openai", f"response {status}: {details}")
            if (
                kind == "response.function_call_arguments.done"
                and msg.get("name") == "record_decision"
            ):
                args = json.loads(msg.get("arguments") or "{}")
                decision = str(args.get("decision", "escalate"))
                try:
                    book.decide(alert_id, decision, by=f"voice ({contact_name})", channel="call")
                    if args.get("note"):
                        book.log(
                            alert_id,
                            "decision",
                            "call",
                            contact_name,
                            f"note: {args['note'][:200]}",
                        )
                    output = {"ok": True, "recorded": decision}
                except (KeyError, ValueError) as e:
                    output = {"ok": False, "error": str(e)}
                ws.send(
                    json.dumps(
                        {
                            "type": "conversation.item.create",
                            "item": {
                                "type": "function_call_output",
                                "call_id": msg.get("call_id"),
                                "output": json.dumps(output),
                            },
                        }
                    )
                )
                if active:
                    pending = True  # answer once the running response has finished
                else:
                    ws.send(json.dumps({"type": "response.create"}))
                    active = True
                decided_at = time.time()
            elif kind == "response.done" and decided_at and time.time() - decided_at > 1:
                time.sleep(4)  # let the goodbye play out
                httpx.post(f"{API}/{call_id}/hangup", headers=auth, timeout=10)
                break
    book.log(
        alert_id, "call", "call", contact_name, f"openai call {call_id[:16]} ended at {now_iso()}"
    )


def start_incoming(event: dict, data_dir: Path) -> None:
    threading.Thread(target=handle_incoming, args=(event, data_dir), daemon=True).start()
