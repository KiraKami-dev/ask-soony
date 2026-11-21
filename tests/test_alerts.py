import base64
import json
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx

from ask_soony.alerts import AlertBook, Contact, Policy, classify
from ask_soony.dashboard import Dashboard
from ask_soony.escalation import DryRunCaller, ElevenLabsCaller, Escalator, Mailer, TwilioCaller
from ask_soony.store import Store

NOW = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)


def policy() -> Policy:
    return Policy(
        dashboard_url="https://dash.example.com",
        contacts=[
            Contact(
                "buyer", "Alex Buyer", "buyer", phone="+15550000001", email="buyer@example.com"
            ),
            Contact("head", "Sam Head", "head_of_procurement", phone="+15550000002"),
        ],
        routes={
            "critical": {
                "channels": ["dashboard", "call"],
                "chain": ["buyer", "head"],
                "ack_minutes": 10,
            },
            "high": {"channels": ["dashboard"], "chain": ["buyer"], "ack_minutes": 0},
            "medium": {"channels": ["dashboard"], "chain": ["buyer"], "ack_minutes": 0},
            "low": {"channels": ["dashboard"], "chain": [], "ack_minutes": 0},
            "info": {"channels": ["dashboard"], "chain": [], "ack_minutes": 0},
        },
    )


def test_classify_raises_severity_close_to_a_launch_deadline():
    soon = (NOW + timedelta(hours=6)).isoformat()
    later = (NOW + timedelta(hours=48)).isoformat()
    assert classify("medium", soon, "launch", 24, now=NOW) == "critical"
    assert classify("medium", later, "launch", 24, now=NOW) == "high"
    assert classify("medium", soon, "cost", 24, now=NOW) == "medium"
    assert classify("high", None, "launch", 24, now=NOW) == "high"


def test_critical_alert_calls_chain_and_escalates_on_timeout(tmp_path):
    book = AlertBook(Store(tmp_path))
    pol = policy()
    esc = Escalator(book, pol, DryRunCaller(), Mailer("", "", "", dry_run=True))
    alert = book.raise_alert(
        title="Part short",
        message="5,900 missing",
        action="Order 8,000",
        severity="critical",
        policy=pol,
    )
    esc.notify(alert)
    calls = [e for e in book.events(alert.id) if e["kind"] == "call"]
    assert calls and calls[0]["contact"] == "buyer"

    late = datetime.now(UTC) + timedelta(minutes=11)
    assert esc.tick(now=late) == 1
    calls = [e for e in book.events(alert.id) if e["kind"] == "call"]
    assert [c["contact"] for c in calls] == ["buyer", "head"]

    book.decide(alert.id, "approve", by="head", channel="call")
    assert esc.tick(now=late + timedelta(hours=1)) == 0
    assert book.get(alert.id).status == "approved"


def test_twilio_call_reads_alert_and_points_keypad_to_webhook():
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(parse_qs(request.content.decode()))
        return httpx.Response(201, json={"sid": "CA123"})

    caller = TwilioCaller(
        "AC1", "tok", "+15550009999", http=httpx.Client(transport=httpx.MockTransport(handler))
    )
    book_alert = type("A", (), {"id": 7, "token": "secret", "spoken": lambda self: "Part short."})()
    out = caller.call(
        book_alert, Contact("buyer", "Alex", "buyer", phone="+15550000001"), "https://x.example.com"
    )
    assert "CA123" in out and sent[0]["To"] == ["+15550000001"]
    assert sent[0]["Url"] == ["https://x.example.com/hooks/twilio/twiml/7?token=secret"]
    twiml = caller.twiml(book_alert, "https://x.example.com")
    assert "Part short." in twiml and "/hooks/twilio/7?token=secret" in twiml and "<Gather" in twiml


def test_elevenlabs_payload_passes_alert_as_dynamic_variables(tmp_path):
    book = AlertBook(Store(tmp_path))
    alert = book.raise_alert(title="T", message="M", action="A", severity="high", policy=policy())
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers["xi-api-key"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"success": True, "conversation_id": "conv1"})

    caller = ElevenLabsCaller(
        "k", "agent", "phone", http=httpx.Client(transport=httpx.MockTransport(handler))
    )
    caller.call(
        alert, Contact("buyer", "Alex", "buyer", phone="+15550000001"), "https://x.example.com"
    )
    variables = seen["body"]["conversation_initiation_client_data"]["dynamic_variables"]
    assert seen["key"] == "k" and seen["body"]["to_number"] == "+15550000001"
    assert variables["secret__alert_token"] == alert.token
    assert variables["decision_url"] == "https://x.example.com/hooks/elevenlabs/decision"


def _post(url: str, data: bytes, ctype: str, auth: str | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": ctype})
    if auth:
        req.add_header("Authorization", "Basic " + base64.b64encode(f"x:{auth}".encode()).decode())
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_webhooks_record_decisions_only_with_the_right_token(tmp_path):
    book = AlertBook(Store(tmp_path))
    a1 = book.raise_alert(title="T1", message="M", severity="critical", policy=policy())
    a2 = book.raise_alert(title="T2", message="M", severity="critical", policy=policy())
    server = Dashboard(tmp_path, password="pw").serve(port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        code, _ = _post(
            f"{base}/hooks/twilio/{a1.id}?token=wrong",
            b"Digits=1",
            "application/x-www-form-urlencoded",
        )
        assert code == 403
        code, body = _post(
            f"{base}/hooks/twilio/{a1.id}?token={a1.token}",
            b"Digits=1",
            "application/x-www-form-urlencoded",
        )
        assert code == 200 and b"approve" in body
        payload = json.dumps(
            {"alert_id": a2.id, "decision": "escalate", "token": a2.token}
        ).encode()
        code, _ = _post(f"{base}/hooks/elevenlabs/decision", payload, "application/json")
        assert code == 200
        code, _ = _post(
            f"{base}/alerts/{a2.id}/decide",
            b"decision=resolve",
            "application/x-www-form-urlencoded",
        )
        assert code == 401
    finally:
        server.shutdown()
    book = AlertBook(Store(tmp_path))
    assert book.get(a1.id).status == "approved"
    assert book.get(a2.id).status == "escalated"
