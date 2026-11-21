"""
Read-only web dashboard over the version store: every poll, every file version with its
"what changed" summary and diff, findings, and the supplier mail log.

Standard library only. Binds to localhost by default; expose it through a tunnel and set
ASK_DASHBOARD_PASSWORD, since the data is confidential.
"""

import base64
import email
import hmac
import json
import mimetypes
import os
from datetime import datetime
from email import policy
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, quote, urlparse
from zoneinfo import ZoneInfo

from ask_soony.alerts import AlertBook
from ask_soony.escalation import KEYPAD, TwilioCaller
from ask_soony.store import Store

TZ = ZoneInfo("Europe/Berlin")

CSS = """
:root{--bg:#f5f6f8;--card:#fff;--ink:#17202b;--muted:#5d6875;--line:#dde1e7;--accent:#1f5fae;
--new:#1e7a46;--chg:#9a5b00;--del:#b3261e;--code:#f0f2f5}
@media (prefers-color-scheme:dark){:root{--bg:#10151c;--card:#18202a;--ink:#e5eaf1;--muted:#98a4b3;
--line:#2a3542;--accent:#80b0ef;--new:#6fd39a;--chg:#f2b35b;--del:#ff8a80;--code:#121a23}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,sans-serif;
padding:24px max(16px,env(safe-area-inset-left))}
main{max-width:980px;margin:0 auto;display:grid;gap:20px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
h1{font-size:1.5rem;margin:0}h2{font-size:1.1rem;margin:0 0 8px}
.meta{color:var(--muted);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;min-width:0}
.poll{display:grid;gap:8px}.poll+.poll{border-top:1px solid var(--line);padding-top:12px;margin-top:12px}
.row{display:grid;grid-template-columns:76px 1fr;gap:10px;align-items:start}
.tag{font:600 11px/1.8 ui-monospace,Menlo,monospace;text-transform:uppercase;text-align:center;
border-radius:4px;padding:0 6px}
.tag.new{color:var(--new);border:1px solid var(--new)}.tag.changed{color:var(--chg);border:1px solid var(--chg)}
.tag.removed{color:var(--del);border:1px solid var(--del)}
.path{font:13px ui-monospace,Menlo,monospace;word-break:break-all}
.sum{color:var(--ink);font-size:14px}
pre{background:var(--code);border:1px solid var(--line);border-radius:8px;padding:12px;overflow-x:auto;
font:12.5px/1.45 ui-monospace,Menlo,monospace;white-space:pre}
.add{color:var(--new)}.rem{color:var(--del)}
table{border-collapse:collapse;width:100%;font-size:14px}td,th{text-align:left;padding:6px 8px;
border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--muted);font-weight:500;font-size:12px}
.btn{font:inherit;font-size:14px;padding:6px 12px;border-radius:6px;border:1px solid var(--line);
background:var(--card);color:var(--ink);cursor:pointer}.btn.go{background:var(--new);color:#fff;border-color:var(--new)}
.btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}form.inline{display:flex;gap:8px;flex-wrap:wrap}
.sev{font-weight:600}.sev.critical,.sev.high{color:var(--del)}.sev.medium{color:var(--chg)}
nav{display:flex;gap:16px;flex-wrap:wrap;font-size:14px}
"""


def fmt_time(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).astimezone(TZ).strftime("%a %d %b %H:%M")
    except ValueError:
        return iso


def page(title: str, body: str, refresh: bool = True) -> bytes:
    meta = '<meta http-equiv="refresh" content="60">' if refresh else ""
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>"
        f"{meta}<title>{escape(title)}</title><style>{CSS}</style></head><body><main>"
        "<nav><a href='/alerts'>Alerts</a><a href='/'>Changes</a><a href='/findings'>Findings</a><a href='/mail'>Supplier mail</a>"
        "<a href='/files'>All files</a></nav>"
        f"{body}</main></body></html>"
    ).encode()


def diff_html(diff: str) -> str:
    out = []
    for line in diff.splitlines():
        cls = "add" if line.startswith("+") else "rem" if line.startswith("-") else ""
        out.append(f"<span class='{cls}'>{escape(line)}</span>" if cls else escape(line))
    return "<pre>" + "\n".join(out) + "</pre>"


class Dashboard:
    def __init__(self, data_dir: Path, password: str = ""):
        self.data_dir = data_dir
        self.password = password

    # --- pages -----------------------------------------------------------------------------

    def home(self, store: Store) -> bytes:
        polls = store.polls(500)
        last = polls[0] if polls else None
        current = store.current()
        parts = [
            "<header><h1>Portal changes</h1>"
            f"<div class='meta'>{len(current)} files · last check {fmt_time(last and last['at'])}"
            f" · {len(polls)} checks · page refreshes every minute</div></header>"
        ]
        quiet = 0
        blocks = []
        for p in polls:
            versions = store.poll_versions(p["id"])
            removed = store.poll_removals(p)
            if not versions and not removed:
                quiet += 1
                continue
            rows = []
            for v in versions:
                rows.append(
                    f"<div class='row'><span class='tag {v['change']}'>{v['change']}</span><div>"
                    f"<a class='path' href='/v/{v['id']}'>{escape(v['path'])}</a>"
                    f"<div class='sum'>{escape(v['summary'] or 'summary pending')}</div></div></div>"
                )
            for path in removed:
                rows.append(
                    "<div class='row'><span class='tag removed'>removed</span>"
                    f"<a class='path' href='/f?path={quote(path)}'>{escape(path)}</a></div>"
                )
            blocks.append(
                f"<div class='poll'><div class='meta'>{fmt_time(p['at'])} · {p['n_new']} new,"
                f" {p['n_changed']} changed, {p['n_removed']} removed</div>{''.join(rows)}</div>"
            )
        if blocks:
            parts.append("<section class='card'>" + "".join(blocks) + "</section>")
        else:
            parts.append("<section class='card'>No changes since the baseline yet.</section>")
        if polls:
            parts.append(
                f"<div class='meta'>{quiet} checks without changes. Baseline: "
                f"{fmt_time(polls[-1]['at'])}, {polls[-1]['n_files']} files.</div>"
            )
        return page("Portal changes", "".join(parts))

    def version(self, store: Store, vid: int) -> bytes | None:
        v = store.version_detail(vid)
        if not v:
            return None
        prev = store.previous_version_dict(vid)
        body = [
            f"<header><h1 class='path'>{escape(v['path'])}</h1><div class='meta'>"
            f"{escape(v['change'])} · first seen {fmt_time(v['first_seen'])} · {v['size']} bytes · "
            f"sha256 {v['sha256'][:12]}… · <a href='/raw/{vid}'>download</a> · "
            f"<a href='/f?path={quote(v['path'])}'>all versions</a></div></header>"
        ]
        if prev:
            body.append(
                f"<div class='meta'>Previous version: <a href='/v/{prev['id']}'>first seen "
                f"{fmt_time(prev['first_seen'])}</a></div>"
            )
        body.append(
            f"<section class='card'><h2>What changed</h2><div class='sum'>"
            f"{escape(v['summary'] or 'Baseline version, nothing to compare.')}</div>"
            + (diff_html(v["diff"]) if v.get("diff") else "")
            + "</section>"
        )
        analysis = store.analysis_for(vid)
        if analysis:
            body.append(
                f"<section class='card'><h2>Agent analysis</h2><div class='sum'>"
                f"{escape(analysis['summary'])}</div><div class='meta'>{escape(analysis['model'])}"
                f" · {fmt_time(analysis['at'])}</div></section>"
            )
        return page(v["path"], "".join(body), refresh=False)

    def history(self, store: Store, path: str) -> bytes:
        rows = "".join(
            f"<tr><td>{fmt_time(v['first_seen'])}</td><td><span class='tag {v['change']}'>{v['change']}"
            f"</span></td><td><a href='/v/{v['id']}'>{escape(v['summary'] or 'baseline')}</a></td></tr>"
            for v in store.path_history(path)
        )
        return page(
            path,
            f"<h1 class='path'>{escape(path)}</h1><section class='card'><table>"
            f"<tr><th>Seen</th><th>Type</th><th>Summary</th></tr>{rows}</table></section>",
        )

    def files(self, store: Store) -> bytes:
        rows = "".join(
            f"<tr><td><a class='path' href='/v/{v.id}'>{escape(p)}</a></td><td>{v.change}</td>"
            f"<td>{fmt_time(v.first_seen)}</td></tr>"
            for p, v in sorted(store.current().items())
        )
        return page(
            "All files",
            f"<h1>All current files</h1><section class='card'><table><tr><th>Path"
            f"</th><th>Version</th><th>Since</th></tr>{rows}</table></section>",
        )

    def findings(self, store: Store) -> bytes:
        items = store.findings(None)
        if not items:
            return page(
                "Findings",
                "<h1>Findings</h1><section class='card'>No agent findings yet. "
                "They appear once the analysis agent runs with an LLM key.</section>",
            )
        rows = "".join(
            f"<tr><td class='sev {f['severity']}'>{f['severity']}</td><td><b>{escape(f['title'])}</b>"
            f"<div>{escape(f['detail'])}</div><div class='meta'>Action: {escape(f['suggested_action'])}</div>"
            f"</td><td>{f['status']}</td><td><a class='path' href='/v/{f['version_id']}'>{escape(f['path'])}"
            "</a></td></tr>"
            for f in reversed(items)
        )
        return page(
            "Findings",
            f"<h1>Findings</h1><section class='card'><table><tr><th>Severity</th>"
            f"<th>Finding</th><th>Status</th><th>Source</th></tr>{rows}</table></section>",
        )

    def mail(self) -> bytes:
        entries = []
        root = self.data_dir.parent
        for f in sorted((root / "outbox").glob("*.json")):
            rec = json.loads(f.read_text())
            entries.append(
                (
                    rec.get("date", ""),
                    "sent",
                    rec.get("to", ""),
                    rec.get("subject", ""),
                    rec.get("body", ""),
                )
            )
        for f in sorted((root / "inbox").glob("*.eml")):
            m = email.message_from_bytes(f.read_bytes(), policy=policy.default)
            body = m.get_body(preferencelist=("plain",))
            entries.append(
                (
                    str(m["Date"]),
                    "received",
                    str(m["From"]),
                    str(m["Subject"]),
                    body.get_content() if body else "",
                )
            )

        def key(e):
            try:
                return email.utils.parsedate_to_datetime(e[0])
            except (TypeError, ValueError):
                return datetime.min.replace(tzinfo=TZ)

        cards = "".join(
            f"<section class='card'><div class='meta'>{escape(kind)} · {escape(date)} · {escape(who)}</div>"
            f"<h2>{escape(subj)}</h2><pre>{escape(body)}</pre></section>"
            for date, kind, who, subj, body in sorted(entries, key=key, reverse=True)
        )
        return page("Supplier mail", "<h1>Supplier mail</h1>" + (cards or "<p>No mail yet.</p>"))

    def alerts(self, store: Store) -> bytes:
        book = AlertBook(store)
        rows = "".join(
            f"<tr><td class='sev {a.severity}'>{a.severity}</td><td><a href='/alerts/{a.id}'><b>"
            f"{escape(a.title)}</b></a><div class='meta'>{escape(a.message[:160])}</div></td>"
            f"<td>{a.status}</td><td>{fmt_time(a.created_at)}</td></tr>"
            for a in book.list()
        )
        body = (
            "<h1>Alerts</h1><section class='card'><table><tr><th>Severity</th><th>Alert</th><th>Status</th>"
            f"<th>Raised</th></tr>{rows}</table></section>"
            if rows
            else "<h1>Alerts</h1><section class='card'>No alerts.</section>"
        )
        return page("Alerts", body)

    def alert(self, store: Store, alert_id: int) -> bytes | None:
        book = AlertBook(store)
        a = book.get(alert_id)
        if not a:
            return None
        events = "".join(
            f"<tr><td>{fmt_time(e['at'])}</td><td>{escape(e['kind'])}</td><td>{escape(e['channel'])}</td>"
            f"<td>{escape(e['contact'])}</td><td>{escape(e['detail'])}</td></tr>"
            for e in book.events(alert_id)
        )
        open_ = a.status in {"open", "notified", "escalated"}
        buttons = (
            f"<form class='inline' method='post' action='/alerts/{a.id}/decide'>"
            "<button class='btn go' name='decision' value='approve'>Approve action</button>"
            "<button class='btn' name='decision' value='escalate'>Escalate</button>"
            "<button class='btn' name='decision' value='reject'>Reject</button>"
            "<button class='btn' name='decision' value='resolve'>Mark resolved</button></form>"
            if open_
            else f"<div class='meta'>Decided: {a.status} by {escape(a.decision_by or '')} "
            f"{fmt_time(a.decision_at)}</div>"
        )
        body = (
            f"<header><h1>{escape(a.title)}</h1><div class='meta'><span class='sev {a.severity}'>{a.severity}"
            f"</span> · {a.status} · from {escape(a.source)} · impact {escape(a.impact)}"
            f"{' · goes to a supplier' if a.external else ''}"
            f"{' · deadline ' + fmt_time(a.deadline) if a.deadline else ''}</div></header>"
            f"<section class='card'><div class='sum'>{escape(a.message)}</div>"
            f"<h2 style='margin-top:12px'>Proposed action</h2><div class='sum'>{escape(a.action or 'none')}</div>"
            f"<div style='margin-top:12px'>{buttons}</div></section>"
            f"<section class='card'><h2>History</h2><table><tr><th>When</th><th>What</th><th>Channel</th>"
            f"<th>Contact</th><th>Detail</th></tr>{events}</table></section>"
        )
        return page(a.title, body)

    # --- server ----------------------------------------------------------------------------

    def handler(self):
        dash = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _authorized(self) -> bool:
                if not dash.password:
                    return True
                header = self.headers.get("Authorization", "")
                if header.startswith("Basic "):
                    try:
                        _, _, pw = base64.b64decode(header[6:]).decode().partition(":")
                    except ValueError:
                        return False
                    return hmac.compare_digest(pw, dash.password)
                return False

            def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if not self._authorized():
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="ask-soony"')
                    self.end_headers()
                    return
                url = urlparse(self.path)
                store = Store(dash.data_dir)
                try:
                    if url.path == "/":
                        return self._send(200, dash.home(store))
                    if url.path.startswith("/v/") and url.path[3:].isdigit():
                        body = dash.version(store, int(url.path[3:]))
                        return self._send(200 if body else 404, body or b"not found")
                    if url.path.startswith("/raw/") and url.path[5:].isdigit():
                        v = store.version_detail(int(url.path[5:]))
                        if not v:
                            return self._send(404, b"not found")
                        ctype = mimetypes.guess_type(PurePosixPath(v["path"]).name)[0]
                        return self._send(
                            200, store.read_blob(v["sha256"]), ctype or "application/octet-stream"
                        )
                    if url.path == "/f":
                        path = parse_qs(url.query).get("path", [""])[0]
                        return self._send(200, dash.history(store, path))
                    if url.path == "/files":
                        return self._send(200, dash.files(store))
                    if url.path == "/findings":
                        return self._send(200, dash.findings(store))
                    if url.path == "/mail":
                        return self._send(200, dash.mail())
                    if url.path == "/alerts":
                        return self._send(200, dash.alerts(store))
                    if url.path.startswith("/alerts/") and url.path[8:].isdigit():
                        body = dash.alert(store, int(url.path[8:]))
                        return self._send(200 if body else 404, body or b"not found")
                    return self._send(404, b"not found")
                finally:
                    store.db.close()

            def _body(self) -> bytes:
                length = min(int(self.headers.get("Content-Length") or 0), 64_000)
                return self.rfile.read(length)

            def do_POST(self):
                url = urlparse(self.path)
                store = Store(dash.data_dir)
                book = AlertBook(store)
                try:
                    # OpenAI realtime: incoming SIP call webhook (signature-checked).
                    if url.path == "/hooks/openai":
                        from ask_soony.openai_calls import start_incoming, verify_webhook

                        raw = self._body()
                        if not verify_webhook(
                            self.headers, raw, os.environ.get("OPENAI_WEBHOOK_SECRET", "")
                        ):
                            return self._send(401, b"bad signature")
                        event = json.loads(raw or b"{}")
                        if event.get("type") == "realtime.call.incoming":
                            start_incoming(event, dash.data_dir)
                        return self._send(200, b"ok")
                    # Twilio script that hands the answered call over to OpenAI.
                    if url.path.startswith("/hooks/openai/twiml/") and url.path[20:].isdigit():
                        from ask_soony.openai_calls import OpenAICaller

                        a = book.get(int(url.path[20:]))
                        token = parse_qs(url.query).get("token", [""])[0]
                        if not a or not hmac.compare_digest(token, a.token):
                            return self._send(403, b"forbidden")
                        return self._send(
                            200, OpenAICaller.from_env().twiml(a).encode(), "text/xml"
                        )
                    # Call script Twilio fetches when the call connects (token-protected).
                    if url.path.startswith("/hooks/twilio/twiml/") and url.path[20:].isdigit():
                        a = book.get(int(url.path[20:]))
                        token = parse_qs(url.query).get("token", [""])[0]
                        if not a or not hmac.compare_digest(token, a.token):
                            return self._send(403, b"forbidden")
                        public = os.environ.get("ASK_PUBLIC_URL", "").rstrip("/")
                        twiml = TwilioCaller("", "", "").twiml(a, public).encode()
                        return self._send(200, twiml, "text/xml")
                    # Phone keypad answer from Twilio: authenticated by the alert's secret token.
                    if url.path.startswith("/hooks/twilio/") and url.path[14:].isdigit():
                        form = parse_qs(self._body().decode())
                        token = parse_qs(url.query).get("token", [""])[0]
                        digit = form.get("Digits", [""])[0]
                        decision = KEYPAD.get(digit)
                        if not decision:
                            say = "Sorry, that was not a valid choice. Please decide in the dashboard."
                        else:
                            try:
                                book.decide(
                                    int(url.path[14:]),
                                    decision,
                                    by="phone …" + form.get("To", [""])[0][-2:],
                                    channel="call",
                                    token=token,
                                )
                                say = f"Thank you. Your decision, {decision}, is recorded."
                            except (KeyError, PermissionError):
                                return self._send(403, b"forbidden")
                        twiml = f"<Response><Say>{escape(say)}</Say></Response>".encode()
                        return self._send(200, twiml, "text/xml")
                    # Decision from the ElevenLabs voice agent's server tool.
                    if url.path == "/hooks/elevenlabs/decision":
                        try:
                            data = json.loads(self._body() or b"{}")
                            a = book.decide(
                                int(data["alert_id"]),
                                str(data["decision"]).lower(),
                                by=str(data.get("by") or "voice agent"),
                                channel="call",
                                token=str(data.get("token", "")),
                            )
                        except PermissionError:
                            return self._send(403, b'{"ok":false}', "application/json")
                        except (KeyError, ValueError, TypeError) as e:
                            return self._send(
                                400,
                                json.dumps({"ok": False, "error": str(e)}).encode(),
                                "application/json",
                            )
                        return self._send(
                            200,
                            json.dumps({"ok": True, "status": a.status}).encode(),
                            "application/json",
                        )
                    if not self._authorized():
                        self.send_response(401)
                        self.send_header("WWW-Authenticate", 'Basic realm="ask-soony"')
                        self.end_headers()
                        return
                    if url.path.startswith("/alerts/") and url.path.endswith("/decide"):
                        alert_id = url.path[8:-7]
                        decision = parse_qs(self._body().decode()).get("decision", [""])[0]
                        if not alert_id.isdigit():
                            return self._send(404, b"not found")
                        try:
                            book.decide(
                                int(alert_id), decision, by="dashboard", channel="dashboard"
                            )
                        except (KeyError, ValueError):
                            return self._send(400, b"invalid decision")
                        self.send_response(303)
                        self.send_header("Location", f"/alerts/{alert_id}")
                        self.end_headers()
                        return
                    return self._send(404, b"not found")
                finally:
                    store.db.close()

        return Handler

    def serve(self, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
        return ThreadingHTTPServer((host, port), self.handler())
