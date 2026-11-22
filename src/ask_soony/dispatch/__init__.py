"""Turn verifier and predictor outputs into one approval-gated decision payload."""

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from ask_soony.dispatch.decisions import to_payload
from ask_soony.dispatch.decisions.cards import build_cards
from ask_soony.predictor import ImpactAgent, is_predictor_path
from ask_soony.verifier.impact.shortfall import compute
from ask_soony.verifier.linker import build


@dataclass(frozen=True)
class Routes:
    verifier: bool
    predictor: bool


def routes_for(paths: list[str]) -> Routes:
    """Verification checks every changed datum; prediction only gets event/logistics changes."""
    return Routes(
        verifier=bool(paths),
        predictor=any(is_predictor_path(path) for path in paths),
    )


def refresh(
    paths: list[str],
    root: Path,
    readings: Path,
    out: Path,
    predictor: ImpactAgent | None = None,
) -> Routes:
    """Refresh affected systems and write their combined decision JSON to ``out``."""
    routes = routes_for(paths)
    predictions = []
    if routes.predictor and predictor:
        try:
            predictions = predictor.run(predictor.events_for_paths(paths), force=True)
        except Exception as e:  # a failed model call must not stop the verifier refresh
            print(f"dispatch: predictor failed: {e!r}", file=sys.stderr)
    if routes.verifier:
        graph = build(root, readings)
        result = compute(graph.records, graph.issues)
        decisions = build_cards(result, graph.issues, graph.records)
        payload = to_payload(result, decisions)
        for decision in payload["decisions"]:
            has_recipient = any(message["to_email"] for message in decision["messages"])
            decision["action"] = {
                "channel": "email" if has_recipient else "notify",
                "status": "pending_approval",
            }
        payload.update(changed_paths=paths, routes=asdict(routes), predictor=predictions)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                payload,
                indent=2,
                ensure_ascii=False,
                default=lambda value: (
                    value.isoformat() if hasattr(value, "isoformat") else str(value)
                ),
            )
        )
    return routes
