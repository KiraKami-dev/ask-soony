"""
uv run python -m ask_soony.dispatch.decisions  # build structured decisions → data/state/dispatch.json
"""

import argparse
import json
from pathlib import Path

from ask_soony.dispatch.decisions import to_payload
from ask_soony.dispatch.decisions.cards import build_cards
from ask_soony.verifier.impact.shortfall import compute
from ask_soony.verifier.linker import build


def main() -> None:
    parser = argparse.ArgumentParser(prog="ask_soony.dispatch.decisions")
    parser.add_argument("--root", type=Path, default=Path("data/portal"))
    parser.add_argument("--readings", type=Path, default=Path("data/store/image_recognizer"))
    parser.add_argument("--out", type=Path, default=Path("data/state/dispatch.json"))
    args = parser.parse_args()

    graph = build(args.root, args.readings)
    result = compute(graph.records, graph.issues)
    cards = build_cards(result, graph.issues, graph.records)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            to_payload(result, cards),
            indent=2,
            ensure_ascii=False,
            default=lambda value: (
                value.isoformat() if hasattr(value, "isoformat") else str(value)
            ),
        )
    )
    for card in cards:
        print(
            f"{card.at_risk:>8,.0f}  {card.title}  →  {', '.join(m.to_role for m in card.messages)}"
        )
    print(f"\n{len(cards)} decisions → {args.out}")


if __name__ == "__main__":
    main()
