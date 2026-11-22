"""Structured, approval-gated decision payloads."""

from dataclasses import asdict

from ask_soony.dispatch.decisions.cards import Card
from ask_soony.verifier.impact.shortfall import Shortfall


def to_payload(result: Shortfall, decisions: list[Card]) -> dict:
    return {
        "editions": [
            asdict(edition) | {"remaining": edition.remaining, "at_risk": result.at_risk(edition.product)[0]}
            for edition in result.editions
        ],
        "parts": [asdict(part) | {"usable": part.usable, "gap": part.gap} for part in result.parts],
        "decisions": [asdict(decision) for decision in decisions],
    }
