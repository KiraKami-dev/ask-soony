"""
Outbox: what left the shared mailbox, whoever sent it.

The team shares one mailbox. The app only knew the emails it sent itself, so an order typed by
hand (or sent by another tool) was invisible: the app could send it again, and replies to it were
matched by guesswork. This reads the mailbox's Sent folder, ties every email to a supplier to its
card (thread reference first, else supplier plus order or part number) and records it as
"sent outside the app". Before a new send, `recent_sends` tells the review sheet what already went
to that supplier about the same case.
"""

from __future__ import annotations

import email
import imaplib
import os
import re
import ssl
import threading
from datetime import UTC, datetime, timedelta
from email.header import decode_header, make_header
from email.utils import getaddresses, parsedate_to_datetime

from ask_soony.config import load_dotenv

_lock = threading.Lock()
PO = re.compile(r"\b(45\d{8})\b")
PART = re.compile(r"\b([A-Z]{3}-[A-Z]{3}-\d{3})\b")
SIGNED = re.compile(r"(?:Buyer|Einkäufer|Kupiec)\s*:\s*([^\n]+)", re.I)


def _h(value: str | None) -> str:
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return value or ""


def _text(msg: email.message.Message) -> str:
    from ask_soony.replies import _plain_text

    return _plain_text(msg)


def _sent_folder(box: imaplib.IMAP4_SSL) -> str | None:
    _, boxes = box.list()
    for raw in boxes or []:
        line = raw.decode(errors="replace") if isinstance(raw, bytes) else str(raw)
        if "\\Sent" in line:
            m = re.search(r'"([^"]+)"\s*$', line) or re.search(r"(\S+)\s*$", line)
            return m.group(1) if m else None
    return None


def match_card(
    to: str,
    subject: str,
    text: str,
    refs: list[str],
    cards: list[dict],
    actions: list[dict],
    supplier_of: dict[str, str],
) -> str | None:
    """The card an outgoing email belongs to, or None when it is not clearly one card's."""
    by_mid = {a.get("message_id"): a.get("decision_id") for a in actions if a.get("message_id")}
    for ref in reversed(refs):
        if by_mid.get(ref):
            return by_mid[ref]
    sid = supplier_of.get(to)
    if not sid:
        return None
    blob = f"{subject}\n{text}"
    pos, parts = set(PO.findall(blob)), set(PART.findall(blob))
    scored = []
    for c in cards:
        if str(c.get("supplier_id") or "") != sid:
            continue
        cid = str(c.get("id") or "")
        s = 2 * bool(pos and any(p in cid for p in pos)) + bool(c.get("part") in parts)
        if s:
            scored.append((s, cid))
    if not scored:
        return None
    top = max(s for s, _ in scored)
    best = [cid for s, cid in scored if s == top]
    # An order number decides; a part number alone only when it names exactly one card.
    return best[0] if len(best) == 1 and (top >= 2 or top == 1) else None


def sync(days: int = 3) -> list[dict]:
    """Record emails sent from the shared mailbox outside the app; returns the new records."""
    load_dotenv()
    host = os.environ.get("ASK_IMAP_HOST", "")
    user = os.environ.get("ASK_MAIL_ADDRESS", "")
    password = os.environ.get("ASK_MAIL_PASSWORD", "").replace(" ", "")
    if not (host and user and password):
        return []
    from ask_soony import api, cardtools

    known = cardtools.supplier_addresses()
    supplier_of = _supplier_of()
    since = (datetime.now(UTC) - timedelta(days=days)).strftime("%d-%b-%Y")
    new: list[dict] = []
    with _lock:
        actions = api.read_list("actions.json")
        have = {a.get("message_id") for a in actions if a.get("message_id")}
        cards = api.build_state()["cards"]
        box = imaplib.IMAP4_SSL(host, 993, ssl_context=ssl.create_default_context())
        try:
            box.login(user, password)
            folder = _sent_folder(box)
            if not folder:
                return []
            box.select(f'"{folder}"', readonly=True)
            _, data = box.search(None, f"(SINCE {since})")
            for num in (data[0] or b"").split():
                _, parts = box.fetch(num, "(BODY.PEEK[])")
                raw = next((p[1] for p in parts if isinstance(p, tuple)), b"")
                msg = email.message_from_bytes(raw)
                mid = (msg.get("Message-ID") or "").strip()
                if not mid or mid in have:
                    continue
                heads = msg.get_all("To", []) + msg.get_all("Cc", [])
                rcpts = [a.lower() for _, a in getaddresses(heads)]
                to = next((r for r in rcpts if r in known), None)
                if not to:
                    continue  # not to a supplier: not ours to track
                subject, text = _h(msg.get("Subject")), _text(msg)
                thread = f"{msg.get('In-Reply-To', '')} {msg.get('References', '')}"
                refs = re.findall(r"<[^>]+>", thread)
                card = match_card(to, subject, text, refs, cards, actions, supplier_of)
                try:
                    at = parsedate_to_datetime(msg.get("Date")).astimezone(UTC).isoformat()
                except (TypeError, ValueError):
                    at = api.now().isoformat()
                signed = SIGNED.search(text)
                rec = {
                    "id": mid.strip("<>")[:10],
                    "decision_id": card,
                    "at": at,
                    "kind": "sent_outside",
                    "status": "sent",
                    "channel": "email",
                    "to": to,
                    "subject": subject,
                    "message_id": mid,
                    "signed": signed.group(1).strip()[:80] if signed else None,
                    "action": f"Sent outside the app: {subject[:120]}",
                    "via": "mailbox",
                }
                actions.append(rec)
                have.add(mid)
                new.append(rec)
        finally:
            try:
                box.logout()
            except Exception:
                pass
        if new:
            api.write_json("actions.json", actions)
    return new


def _supplier_of() -> dict[str, str]:
    """Supplier contact address -> supplier id, from the portal's profiles."""
    from ask_soony import api

    root = api.portal_root()
    out: dict[str, str] = {}
    if root is None:
        return out
    for d in (root / "suppliers").iterdir() if (root / "suppliers").is_dir() else []:
        sid = d.name.split("_")[0]
        for f in d.glob("profile.*"):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text):
                out[m.lower()] = sid
    return out


def recent_sends(card: dict, hours: float | None = None) -> list[dict]:
    """Emails that went to this card's supplier about this card (by anyone) in the last hours."""
    from ask_soony import api

    window = hours if hours is not None else float(os.environ.get("ASK_DUP_HOURS", "12"))
    since = api.now() - timedelta(hours=window)
    out = []
    for a in api.read_list("actions.json"):
        if a.get("decision_id") != card.get("id") or not a.get("message_id"):
            continue
        if a.get("kind") not in ("approve", "followup", "sent_outside"):
            continue
        at = api.parse_ts(a.get("at"))
        if at and at >= since:
            out.append({k: a.get(k) for k in ("at", "kind", "to", "subject", "signed", "via")})
    return sorted(out, key=lambda a: str(a["at"]))


def _loop(interval: int) -> None:
    import time

    while True:
        try:
            sync()
        except Exception as exc:  # keep polling
            print(f"outbox sync failed: {exc}")
        time.sleep(interval)


def start_background() -> None:
    interval = int(os.environ.get("ASK_OUTBOX_POLL_SECONDS", "120"))
    threading.Thread(target=_loop, args=(interval,), daemon=True, name="outbox").start()
