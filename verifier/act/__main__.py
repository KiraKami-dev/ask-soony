"""
uv run python -m verifier.act        # build decision cards → data/store/decision_cards.html (+ .json)
"""

import argparse
from pathlib import Path

from verifier.act.cards import build_cards
from verifier.act.render import render, to_json
from verifier.impact.shortfall import compute
from verifier.linker import build


def main() -> None:
    parser = argparse.ArgumentParser(prog="verifier.act")
    parser.add_argument("--root", type=Path, default=Path("data/portal"))
    parser.add_argument("--readings", type=Path, default=Path("data/store/image_recognizer"))
    parser.add_argument("--out", type=Path, default=Path("data/store/decision_cards.html"))
    args = parser.parse_args()

    graph = build(args.root, args.readings)
    result = compute(graph.records, graph.issues)
    cards = build_cards(result, graph.issues, graph.records)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(result, cards, graph.built_at))
    args.out.with_suffix(".json").write_text(to_json(result, cards))
    for card in cards:
        print(
            f"{card.at_risk:>8,.0f}  {card.title}  →  {', '.join(m.to_role for m in card.messages)}"
        )
    print(f"\n{len(cards)} cards → {args.out}")


if __name__ == "__main__":
    main()
