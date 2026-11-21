"""Route portal changes through verification, prediction and decision-card refreshes."""

from dataclasses import dataclass
from pathlib import Path

from ask_soony.predictor import ImpactAgent, is_event_path
from ask_soony.verifier.act.cards import build_cards
from ask_soony.verifier.act.render import render, to_json
from ask_soony.verifier.impact.shortfall import compute
from ask_soony.verifier.linker import build


@dataclass(frozen=True)
class Routes:
    verifier: bool
    predictor: bool


def routes_for(paths: list[str]) -> Routes:
    """Verification checks every changed datum; prediction only gets event/logistics changes."""
    parts = [set(Path(path).parts[:-1]) for path in paths]
    return Routes(
        verifier=bool(paths),
        predictor=any(
            is_event_path(path) or {"logistics", "shipments"} & item
            for path, item in zip(paths, parts)
        ),
    )


def refresh(
    paths: list[str],
    root: Path,
    readings: Path,
    out: Path,
    predictor: ImpactAgent | None = None,
) -> Routes:
    """Refresh the affected systems and leave approval-gated action cards for the UI."""
    routes = routes_for(paths)
    if routes.predictor and predictor:
        predictor.run(predictor.events_from_portal(), force=False)
    if routes.verifier:
        graph = build(root, readings)
        result = compute(graph.records, graph.issues)
        cards = build_cards(result, graph.issues, graph.records)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render(result, cards, graph.built_at))
        out.with_suffix(".json").write_text(to_json(result, cards))
    return routes
