"""
uv run python -m verifier.linker build            # link everything, save, print a summary
uv run python -m verifier.linker trace 4500318768 # one order's chain
uv run python -m verifier.linker issues [--rule R]
"""

import argparse
from pathlib import Path

from verifier.linker.graph import Graph, build, save, trace

ATTRS_SHOWN = (
    "quantity",
    "qty_ordered",
    "qty_received",
    "status",
    "eta",
    "delivered_date",
    "remark",
    "state",
)
MAX_EXAMPLES = 12


def _summary(graph: Graph) -> None:
    s = graph.sources
    print(
        f"Built {graph.built_at}: {len(s)} files, {len(graph.records)} records, "
        f"{len(graph.links)} links, {len(graph.issues)} issues\n"
    )

    print("Files by kind (✓ = has an adapter)")
    for kind, group in s.groupby("kind"):
        mark = "✓" if group.adapter.any() else " "
        print(f"  {mark} {kind:<20} {len(group):>4} files  {int(group.records.sum()):>5} records")
    errors = s[s.error.notna()]
    for row in errors.itertuples():
        print(f"  ! {row.path}: {row.error}")

    print("\nLink coverage")
    for row in graph.coverage.itertuples():
        pct = 100 * row.linked / row.expected if row.expected else 100
        method = "" if row.method == "exact" else f"  ({row.method} only)"
        counts = f"{row.linked:>5}/{row.expected:<5} {pct:5.1f} %"
        print(f"  {row.rule:<26} {row.kind:<14} {counts}{method}")

    print("\nIssues")
    if graph.issues.empty:
        print("  none")
    # Issues arrive sorted (live first, soonest first); groups keep that order
    for (_, detail), group in graph.issues.groupby(["rule", "detail"], sort=False):
        live, closed = group[group.live.astype(bool)], group[~group.live.astype(bool)]
        print(f"  {len(live):>4} live  {detail}")
        for row in live.head(MAX_EXAMPLES).itertuples():
            context = f"  ({row.context})" if row.context else ""
            due = f"  due {row.due}" if row.due else ""
            print(f"        {row.record_id}{context}{due}")
        if len(live) > MAX_EXAMPLES:
            print(f"        … +{len(live) - MAX_EXAMPLES} more")
        if len(closed):
            print(f"        + {len(closed)} closed (no longer affect anything open)")


def _trace(graph: Graph, term: str) -> None:
    hits = trace(graph, term)
    if not hits:
        print(f"Nothing matches '{term}'")
        return
    records = graph.records.set_index("record_id")
    issues: dict[str, list[str]] = {}
    for row in graph.issues.itertuples():
        text = row.detail + (f" ({row.context})" if row.context else "")
        issues.setdefault(row.record_id, []).append(text)
    start_pos = set(records.loc[[rid for d, rid, _ in hits if d == 0]].po_number.dropna())
    for depth, rid, via in sorted(hits, key=lambda h: (h[0], h[1])):
        rec = records.loc[rid]
        # rec["attrs"], not rec.attrs: a pandas Series has its own .attrs
        shown = {k: v for k, v in rec["attrs"].items() if k in ATTRS_SHOWN and v is not None}
        hop = (
            f"  ↪ other order, via {via}"
            if rec.po_number and rec.po_number not in start_pos
            else ""
        )
        print(f"{'  ' * depth}{rid}  {shown}  [{rec.source}]{hop}")
        for text in issues.get(rid, []):
            print(f"{'  ' * depth}  ⚠ {text}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="verifier.linker")
    parser.add_argument("--root", type=Path, default=Path("data/portal"), help="portal data folder")
    parser.add_argument("--db", type=Path, default=Path("data/store/verifier.db"))
    parser.add_argument(
        "--readings",
        type=Path,
        default=Path("data/store/image_recognizer"),
        help="image recognizer output; skipped if the folder does not exist",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build")
    trace_cmd = sub.add_parser("trace")
    trace_cmd.add_argument("term", help="record id, key or PO number")
    issues_cmd = sub.add_parser("issues")
    issues_cmd.add_argument("--rule")
    issues_cmd.add_argument("--live", action="store_true", help="only live issues")
    args = parser.parse_args()

    graph = build(args.root, args.readings)
    if args.command == "build":
        save(graph, args.db)
        _summary(graph)
        print(f"\nSaved to {args.db}")
    elif args.command == "trace":
        _trace(graph, args.term)
    elif args.command == "issues":
        issues = graph.issues
        if args.rule:
            issues = issues[issues.rule == args.rule]
        if args.live:
            issues = issues[issues.live.astype(bool)]
        for row in issues.itertuples():
            state = "live  " if row.live else "closed"
            due = row.due or "-"
            context = f"  ({row.context})" if row.context else ""
            print(f"{state} {due:<10} {row.rule:<26} {row.record_id:<32} {row.detail}{context}")


if __name__ == "__main__":
    main()
