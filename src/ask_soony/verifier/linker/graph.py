"""
Build the link graph from the portal data, save it, and trace one order through it.
"""

import json
import sqlite3
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from ask_soony.verifier.linker.adapters import ADAPTERS, PROFILE_KINDS, Context
from ask_soony.verifier.linker.adapters.readings import load_readings, map_supplier_part_numbers
from ask_soony.verifier.linker.discover import Source, discover
from ask_soony.verifier.linker.normalize import SupplierResolver
from ask_soony.verifier.linker.records import HUB_KINDS, RECORD_COLUMNS, disambiguate
from ask_soony.verifier.linker.rules import apply_all


@dataclass
class Graph:
    sources: pd.DataFrame  # path, kind, sha256, adapter, records, error
    records: pd.DataFrame
    links: pd.DataFrame
    issues: pd.DataFrame
    coverage: pd.DataFrame
    built_at: str


def _run(source: Source, ctx: Context) -> tuple[list[dict], dict]:
    adapter = ADAPTERS.get(source.kind)
    row = {
        "path": source.path,
        "kind": source.kind,
        "sha256": source.sha256,
        "adapter": adapter is not None,
        "records": 0,
        "error": source.meta.get("error"),
    }
    produced = []
    if adapter:
        try:
            produced = adapter(source, ctx)
            row["records"] = len(produced)
        except Exception as error:  # one broken file must not stop the build
            row["error"] = f"{type(error).__name__}: {error}"
    return produced, row


def _resolver(profile_records: list[dict]) -> SupplierResolver:
    return SupplierResolver(
        [{"supplier_id": r["supplier_id"], **r["attrs"]} for r in profile_records]
    )


def build(root: Path, readings: Path | None = None) -> Graph:
    """Link the portal data in `root`; add document readings from `readings` when given."""
    sources = discover(root)
    profiles = [s for s in sources if s.kind in PROFILE_KINDS]
    others = [s for s in sources if s.kind not in PROFILE_KINDS]

    all_records, source_rows = [], []
    for source in profiles:  # first: they teach the resolver every supplier name
        produced, row = _run(source, Context(suppliers=None))
        all_records += produced
        source_rows.append(row)
    ctx = Context(suppliers=_resolver(all_records))
    for source in others:
        produced, row = _run(source, ctx)
        all_records += produced
        source_rows.append(row)

    if readings is not None and readings.is_dir():
        current_sha = {s.path: s.sha256 for s in sources}
        read_records, read_rows = load_readings(readings, current_sha)
        known_parts = {r["material_id"] for r in all_records if r["kind"] == "part"}
        map_supplier_part_numbers(read_records, known_parts)
        all_records += read_records
        source_rows += read_rows

    # object dtype keeps None as None (pandas would turn it into NaN in string columns)
    records = disambiguate(pd.DataFrame(all_records, columns=RECORD_COLUMNS, dtype=object))
    links, issues, coverage = apply_all(records)
    return Graph(
        sources=pd.DataFrame(source_rows),
        records=records,
        links=links,
        issues=issues,
        coverage=coverage,
        built_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def _json_cells(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for column in out.columns:
        if out[column].map(lambda v: isinstance(v, (list, dict))).any():
            out[column] = out[column].map(lambda v: json.dumps(v, ensure_ascii=False))
    return out


def save(graph: Graph, db_path: Path) -> None:
    """Replace the linker tables in `db_path` with this build."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        for name in ("sources", "records", "links", "issues", "coverage"):
            _json_cells(getattr(graph, name)).to_sql(
                f"linker_{name}", conn, if_exists="replace", index=False
            )
        pd.DataFrame([{"built_at": graph.built_at}]).to_sql(
            "linker_meta", conn, if_exists="replace", index=False
        )


def find(graph: Graph, term: str) -> list[str]:
    """Record ids matching a term: an id, an entity, a key, or a PO number. All versions."""
    r = graph.records
    hits = r[(r.record_id == term) | (r.entity == term) | (r.key == term) | (r.po_number == term)]
    return list(dict.fromkeys(hits.record_id))


def trace(graph: Graph, term: str, max_depth: int = 4) -> list[tuple[int, str, str | None]]:
    """
    Breadth-first walk from the records matching `term`.

    Returns (depth, record id, reached via). 'via' shows hops into other orders, e.g. through a
    shipment that carries several POs.
    """
    r = graph.records
    kinds = dict(zip(r.record_id, r.kind))
    po_of = dict(zip(r.record_id, r.po_number))
    neighbours: dict[str, set[str]] = {}
    for link in graph.links.itertuples():
        neighbours.setdefault(link.src, set()).add(link.dst)
        neighbours.setdefault(link.dst, set()).add(link.src)

    start = find(graph, term)
    starts = r[r.record_id.isin(start)]
    own_pos = set(starts.po_number.dropna()) | {
        po for pos in starts.po_numbers if isinstance(pos, list) for po in pos
    }

    def other_order(rid: str) -> bool:
        return bool(po_of.get(rid)) and po_of[rid] not in own_pos

    seen: dict[str, tuple[int, str | None]] = {rid: (0, None) for rid in start}
    queue = deque(start)
    while queue:
        current = queue.popleft()
        depth = seen[current][0]
        if depth >= max_depth or (kinds.get(current) in HUB_KINDS and depth > 0):
            continue
        if depth > 0 and other_order(current):  # shown, but not expanded
            continue
        for nxt in sorted(neighbours.get(current, ())):
            if nxt not in seen:
                seen[nxt] = (depth + 1, current)
                queue.append(nxt)
    return [(depth, rid, via) for rid, (depth, via) in seen.items()]
