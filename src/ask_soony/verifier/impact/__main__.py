"""
uv run python -m ask_soony.verifier.impact          # parts, gaps and copies at risk
"""

import argparse
from pathlib import Path

from ask_soony.verifier.impact.shortfall import compute
from ask_soony.verifier.linker import build


def _n(value) -> str:
    return f"{value:,.0f}"


def main() -> None:
    parser = argparse.ArgumentParser(prog="ask_soony.verifier.impact")
    parser.add_argument("--root", type=Path, default=Path("data/portal"))
    parser.add_argument("--readings", type=Path, default=Path("data/store/image_recognizer"))
    args = parser.parse_args()

    graph = build(args.root, args.readings)
    result = compute(graph.records, graph.issues)

    print("Editions")
    for e in result.editions:
        at_risk, part = result.at_risk(e.product)
        risk = f"  at risk {_n(at_risk)} (binding part {part})" if at_risk else ""
        print(
            f"  {e.product:<12} day one {_n(e.day_one):>8}  packed {_n(e.packed):>8}  "
            f"remaining {_n(e.remaining):>7}{risk}"
        )
    print("\nParts (system view → evidence view)")
    for p in result.parts:
        flag = "SHORT" if p.gap else "ok"
        counts = f"need {_n(p.needed):>7}  book {_n(p.book_stock):>8}  usable {_n(p.usable):>8}"
        used_by = ", ".join(p.editions)
        print(
            f"  {p.part:<12} {counts}  gap {_n(p.gap):>7}  {flag:<5}  "
            f"plan said: {p.plan_status or '-'}  [{used_by}]"
        )
        for d in p.deductions:
            print(f"      - {_n(d.units)} {d.reason} ({d.issue})")
        for s in p.supply:
            where = s.get("reference") or s.get("document", "")
            print(f"      + option: {s['kind']} {_n(s['quantity'] or 0)} {where}")
    for x in result.excluded:
        print(f"\n  not counted: {x['part']} for {x['edition']}: fitted inside {x['inside']}")
        print(f"    evidence: {x['evidence']}")
    if result.shared_gaps:
        print("\nShared parts short (who gets them is a decision):")
        for p in result.shared_gaps:
            print(f"  {p.part}: {_n(p.gap)} short across {', '.join(p.editions)}")


if __name__ == "__main__":
    main()
