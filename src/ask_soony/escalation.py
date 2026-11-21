"""
Delivering alerts and escalating them.

Channels: dashboard (the alert is listed there), email (SMTP), call. Calls go through one of three
providers behind the same interface:
- dry_run: only logs what would be said (default; no credentials, no cost)
- twilio: Twilio Programmable Voice reads the alert and takes a keypad answer (1 approve,
  2 escalate, 3 reject) that Twilio posts back to /hooks/twilio/<alert id>
- elevenlabs: an ElevenLabs voice agent (on a Twilio number imported into ElevenLabs) talks the
  alert through and records the decision through a server tool calling /hooks/elevenlabs/decision

The Escalator walks the policy's contact chain: when nobody decides within `ack_minutes`, the
next contact is notified.
"""

import os
import smtplib
import ssl
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Protocol
from xml.sax.saxutils import escape as xml_escape

import httpx

from ask_soony.alerts import Alert, AlertBook, Contact, Policy

KEYPAD = {"1": "approve", "2": "escalate", "3": "reject"}


class Caller(Protocol):
    name: str

    def call(self, alert: Alert, contact: Contact, public_url: str) -> str: ...


class DryRunCaller:
    name = "dry_run"

    def call(self, alert: Alert, contact: Contact, public_url: str) -> str:
        return f"dry run: would call {contact.name} ({contact.role}) and say: {alert.spoken()}"


class TwilioCaller:
    name = "twilio"

    def __init__(self, sid: str, token: str, from_number: str, http: httpx.Client | None = None):
        self.sid, self.token, self.from_number = sid, token, from_number
        self.http = http or httpx.Client(timeout=20.0)

    def twiml(self, alert: Alert, public_url: str) -> str:
        say = xml_escape(alert.spoken())
        prompt = "Press 1 to approve. Press 2 to escalate to the next person. Press 3 to reject."
        if not public_url:
            return (
                f"<Response><Say>{say}</Say><Say>Please decide in the dashboard.</Say></Response>"
            )
        action = xml_escape(f"{public_url}/hooks/twilio/{alert.id}?token={alert.token}")
        return (
            f'<Response><Gather numDigits="1" timeout="10" method="POST" action="{action}">'
            f"<Say>{say}</Say><Say>{prompt}</Say></Gather>"
            "<Say>No answer received. We will contact the next person.</Say></Response>"
        )

    def call(self, alert: Alert, contact: Contact, public_url: str) -> str:
        resp = self.http.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}/Calls.json",
            auth=(self.sid, self.token),
            data={
                "To": contact.phone,
                "From": self.from_number,
                "Twiml": self.twiml(alert, public_url),
            },
        )
        if resp.status_code >= 300:
            raise RuntimeError(f"Twilio call failed: HTTP {resp.status_code} {resp.text[:200]}")
        return f"twilio call {resp.json().get('sid', '?')} to {contact.name}"


class ElevenLabsCaller:
    name = "elevenlabs"
    URL = "https://api.elevenlabs.io/v1/convai/twilio/outbound-call"

    def __init__(
        self, api_key: str, agent_id: str, phone_number_id: str, http: httpx.Client | None = None
    ):
        self.api_key, self.agent_id, self.phone_number_id = api_key, agent_id, phone_number_id
        self.http = http or httpx.Client(timeout=30.0)

    def payload(self, alert: Alert, contact: Contact, public_url: str) -> dict:
        return {
            "agent_id": self.agent_id,
            "agent_phone_number_id": self.phone_number_id,
            "to_number": contact.phone,
            "conversation_initiation_client_data": {
                "dynamic_variables": {
                    "contact_name": contact.name,
                    "alert_id": alert.id,
                    "alert_title": alert.title,
                    "alert_message": alert.message,
                    "proposed_action": alert.action or "none",
                    "deadline": alert.deadline or "none",
                    "decision_url": f"{public_url}/hooks/elevenlabs/decision" if public_url else "",
                    # secret__ values are never shown to the LLM, only passed to tools
                    "secret__alert_token": alert.token,
                }
            },
        }

    def call(self, alert: Alert, contact: Contact, public_url: str) -> str:
        resp = self.http.post(
            self.URL,
            headers={"xi-api-key": self.api_key},
            json=self.payload(alert, contact, public_url),
        )
        if resp.status_code >= 300:
            raise RuntimeError(f"ElevenLabs call failed: HTTP {resp.status_code} {resp.text[:200]}")
        body = resp.json()
        return f"elevenlabs conversation {body.get('conversation_id', '?')} to {contact.name}"


def make_caller(provider: str) -> Caller:
    env = os.environ.get
    if provider == "twilio":
        return TwilioCaller(
            env("TWILIO_ACCOUNT_SID", ""),
            env("TWILIO_AUTH_TOKEN", ""),
            env("TWILIO_FROM_NUMBER", ""),
        )
    if provider == "elevenlabs":
        return ElevenLabsCaller(
            env("ELEVENLABS_API_KEY", ""),
            env("ELEVENLABS_AGENT_ID", ""),
            env("ELEVENLABS_PHONE_NUMBER_ID", ""),
        )
    return DryRunCaller()


class Mailer:
    """Internal alert emails through the same SMTP account as the supplier mail."""

    def __init__(self, address: str, password: str, host: str, dry_run: bool = False):
        self.address, self.password, self.host, self.dry_run = (
            address,
            password.replace(" ", ""),
            host,
            dry_run,
        )

    @classmethod
    def from_env(cls) -> "Mailer":
        env = os.environ.get
        address = env("ASK_MAIL_ADDRESS", "")
        # Real alert emails only when switched on explicitly, like real calls.
        enabled = env("ASK_ALERT_EMAIL", "off").lower() in {"on", "1", "true", "yes"}
        password, host = env("ASK_MAIL_PASSWORD", ""), env("ASK_SMTP_HOST", "")
        return cls(address, password, host, dry_run=not (address and enabled))

    def send(self, alert: Alert, contact: Contact, dashboard_url: str) -> str:
        if self.dry_run or not contact.email:
            return f"dry run: would email {contact.email or contact.name}"
        msg = EmailMessage()
        msg["From"], msg["To"] = self.address, contact.email
        msg["Subject"] = f"[{alert.severity.upper()}] {alert.title}"
        link = f"{dashboard_url}/alerts/{alert.id}" if dashboard_url else ""
        body = [alert.message, "", f"Proposed action: {alert.action or 'none'}"]
        if alert.deadline:
            body.append(f"Deadline: {alert.deadline}")
        if link:
            body += ["", f"Decide here: {link}"]
        msg.set_content("\n".join(body))
        with smtplib.SMTP(self.host, 587, timeout=30) as s:
            s.starttls(context=ssl.create_default_context())
            s.login(self.address, self.password)
            s.send_message(msg)
        return f"email to {contact.email}"


class Escalator:
    def __init__(
        self, book: AlertBook, policy: Policy, caller: Caller, mailer: Mailer, public_url: str = ""
    ):
        self.book, self.policy, self.caller, self.mailer = book, policy, caller, mailer
        self.public_url = public_url.rstrip("/")

    def _chain(self, alert: Alert) -> list[Contact]:
        ids = self.policy.route(alert.severity).get("chain") or [c.id for c in self.policy.contacts]
        return [c for c in (self.policy.contact(i) for i in ids) if c]

    def notify(self, alert: Alert) -> None:
        """Send the alert to the current contact in its chain through every channel of its route."""
        route = self.policy.route(alert.severity)
        chain = self._chain(alert)
        if alert.chain_index >= len(chain):
            self.book.log(alert.id, "error", "-", "-", "escalation chain exhausted")
            self.book.update(alert.id, next_escalation_at=None)
            return
        contact = chain[alert.chain_index]
        for channel in route.get("channels", ["dashboard"]):
            try:
                if channel == "dashboard":
                    detail = "listed on the dashboard"
                elif channel == "email":
                    detail = self.mailer.send(alert, contact, self.policy.dashboard_url)
                elif channel == "call":
                    if not contact.phone:
                        detail = "no phone number"
                    else:
                        detail = self.caller.call(alert, contact, self.public_url)
                else:
                    detail = f"unknown channel {channel}"
                self.book.log(
                    alert.id,
                    "call" if channel == "call" else "notified",
                    channel,
                    contact.id,
                    detail,
                )
            except Exception as e:  # one failing channel must not stop the others
                self.book.log(alert.id, "error", channel, contact.id, str(e)[:300])
        ack = float(route.get("ack_minutes", 0) or 0)
        nxt = None
        if ack and alert.chain_index + 1 < len(chain):
            nxt = (datetime.now(UTC) + timedelta(minutes=ack)).isoformat(timespec="seconds")
        # Back to "notified" after an escalation, so the next timeout moves further down the chain.
        status = "notified" if alert.status in {"open", "escalated"} else alert.status
        self.book.update(alert.id, status=status, next_escalation_at=nxt)

    def tick(self, now: datetime | None = None) -> int:
        """Escalate every undecided alert whose answer time has run out. Returns the count."""
        now = now or datetime.now(UTC)
        done = 0
        for alert in self.book.list(open_only=True):
            if (
                not alert.next_escalation_at
                or datetime.fromisoformat(alert.next_escalation_at) > now
            ):
                continue
            if alert.status != "escalated":  # timeout: nobody decided, move on in the chain
                self.book.update(alert.id, chain_index=alert.chain_index + 1, status="escalated")
                self.book.log(alert.id, "escalated", "-", "-", "no decision in time")
            self.notify(self.book.get(alert.id))
            done += 1
        return done
