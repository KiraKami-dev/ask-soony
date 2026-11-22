"""Turn dispatched launch decisions into internal, policy-routed alerts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ask_soony.alerts import AlertBook, Policy
from ask_soony.escalation import Escalator

_REQUIRED = ("id", "title", "part", "at_risk", "paper_says", "system_says", "messages")


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeError(f"dispatch output is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"dispatch output is invalid JSON: {path}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("decisions"), list):
        raise RuntimeError("dispatch output must contain a decisions list")
    return value


def _decision_text(card: dict) -> tuple[str, str, str]:
    missing = [name for name in _REQUIRED if card.get(name) in (None, "")]
    if missing:
        raise RuntimeError(f"dispatch decision {card.get('id')!r} is missing: {', '.join(missing)}")
    if not isinstance(card["messages"], list) or not card["messages"]:
        raise RuntimeError(f"dispatch decision {card['id']!r} has no routed message")
    first = card["messages"][0]
    if not isinstance(first, dict) or not first.get("subject"):
        raise RuntimeError(f"dispatch decision {card['id']!r} has no message subject")

    deadline = card.get("act_by") or card.get("runs_dry")
    message = (
        f"{card['at_risk']:,.0f} copies are at risk because of {card['part']}. "
        f"System: {card['system_says']}. Evidence: {card['paper_says']}"
    )
    if deadline:
        message += f" Decide by {deadline}."
    action = card.get("decision") or first["subject"]
    return card["title"], message, action


def _severity(card: dict) -> str:
    if card["at_risk"] and (card.get("act_by") or card.get("runs_dry")):
        return "critical"
    if card["at_risk"]:
        return "high"
    return "medium"


def _validate_route(policy: Policy, severity: str) -> None:
    route = policy.route(severity)
    ids = route.get("chain") or [contact.id for contact in policy.contacts]
    contacts = [policy.contact(contact_id) for contact_id in ids]
    if not ids or any(contact is None for contact in contacts):
        raise RuntimeError(f"alert policy has no valid contact chain for {severity} alerts")
    if not route.get("channels"):
        raise RuntimeError(f"alert policy has no channels for {severity} alerts")


def _fingerprint(card: dict) -> str:
    relevant = {key: card.get(key) for key in (*_REQUIRED, "decision", "act_by", "runs_dry")}
    return hashlib.sha256(json.dumps(relevant, sort_keys=True, default=str).encode()).hexdigest()


def sync(
    dispatch_file: Path,
    ledger_file: Path,
    book: AlertBook,
    policy: Policy,
    escalator: Escalator,
) -> list[int]:
    """Raise each new or changed decision once, then notify through its policy route.

    This is deliberately an internal alert. A supplier email stays a dashboard draft until a
    person approves it, satisfying the trust gate before anything leaves Soony.
    """
    payload = _load(dispatch_file)
    try:
        ledger = json.loads(ledger_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        ledger = {}
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"dispatch alert ledger is invalid JSON: {ledger_file}") from exc
    if not isinstance(ledger, dict):
        raise RuntimeError("dispatch alert ledger must be an object")

    raised: list[int] = []
    for card in payload["decisions"]:
        if not isinstance(card, dict):
            raise RuntimeError("dispatch decisions must be objects")
        title, message, action = _decision_text(card)
        fingerprint = _fingerprint(card)
        if ledger.get(str(card["id"]), {}).get("fingerprint") == fingerprint:
            continue
        severity = _severity(card)
        _validate_route(policy, severity)
        alert = book.raise_alert(
            title=title,
            message=message,
            action=action,
            severity=severity,
            deadline=None,
            impact="launch" if card["at_risk"] else "data",
            external=False,
            source="dispatch",
            policy=policy,
        )
        escalator.notify(alert)
        ledger[str(card["id"])] = {"fingerprint": fingerprint, "alert_id": alert.id}
        raised.append(alert.id)

    ledger_file.parent.mkdir(parents=True, exist_ok=True)
    ledger_file.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    return raised
