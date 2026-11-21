"""
Link rules and structural checks.

A rule joins two kinds of records on key columns. It can also say which records must find a
partner; those that don't become 'orphan' issues. Checks look at the linked graph for problems a
single join can't see, such as two versions of the same record that disagree.

Link methods say how certain a link is:
- exact: every key matches (PO + line, batch = receipt number, shipment id)
- po_level: only PO number and material match; the line is unknown until a document says so
"""

from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

from verifier.linker.records import HUB_KINDS

Predicate = Callable[[dict], bool]

# live: the record still affects something open; due: its nearest relevant date
ISSUE_COLUMNS = ["rule", "type", "record_id", "related", "detail", "context", "live", "due"]
LINK_COLUMNS = ["src", "dst", "rule", "method", "key"]


def always(_: dict) -> bool:
    return True


def delivered(record: dict) -> bool:
    """PO line whose facts say it was delivered."""
    return record["attrs"].get("state") == "delivered"


def not_cancellation(record: dict) -> bool:
    return record["attrs"].get("state") != "cancellation"


@dataclass(frozen=True)
class Rule:
    name: str
    src: str
    dst: str
    on: tuple[tuple[str, str], ...]  # (src column, dst column); list columns are exploded
    method: str = "exact"
    expect_src: Predicate | None = None  # src records that must link, else orphan
    src_orphan: str = ""
    expect_dst: Predicate | None = None
    dst_orphan: str = ""
    context: tuple[str, ...] = field(default=())  # attrs shown with each orphan


def _part_rule(kind: str, column: str = "material_id") -> Rule:
    return Rule(
        f"{kind}_to_part",
        kind,
        "part",
        ((column, "material_id"),),
        expect_src=always,
        src_orphan="Material is not in the parts master",
    )


RULES: list[Rule] = [
    Rule(
        "receipt_to_po_line",
        "goods_receipt",
        "po_line",
        (("po_number", "po_number"), ("po_line", "po_line")),
        expect_src=always,
        src_orphan="Goods receipt without a matching PO line",
        expect_dst=delivered,
        dst_orphan="PO line marked delivered, but no goods receipt",
        context=("gr_date", "qty_received", "remark"),
    ),
    Rule(
        "stock_to_receipt",
        "stock_batch",
        "goods_receipt",
        (("batch", "gr_number"),),
        expect_src=always,
        src_orphan="Stock batch without a goods receipt",
        context=("quantity",),
    ),
    Rule(
        "shipment_to_po_line",
        "shipment",
        "po_line",
        (("po_numbers", "po_number"), ("material_ids", "material_id")),
        method="po_level",  # the feed has no line numbers: candidates, not certainties
        expect_src=always,
        src_orphan="Shipment with no PO line for any of its materials",
        context=("status", "eta"),
    ),
    Rule(
        "shipment_to_tracker",
        "shipment",
        "tracker_row",
        (("shipment_id", "shipment_id"),),
        expect_src=always,
        src_orphan="Shipment missing from the Excel tracker",
        expect_dst=always,
        dst_orphan="Tracker row with no shipment in the ERP feed",
        context=("status", "mode", "eta"),
    ),
    # --- Documents (M2): keys from the filename ---
    Rule(
        "confirmation_to_po_line",
        "confirmation",
        "po_line",
        (("po_number", "po_number"),),
        method="po_level",  # which lines it confirms needs the document's contents (M3)
        expect_src=always,
        src_orphan="Supplier confirmation for a PO that is not in SAP",
        # Lines without one: check_lines_without_confirmation (only since the archive starts)
    ),
    Rule(
        "shipping_doc_to_shipment",
        "shipping_doc",
        "shipment",
        (("reference", "reference"),),
        expect_src=always,
        src_orphan="Supplier shipping document with no shipment in the ERP feed",
        context=("file",),
    ),
    Rule(
        "shipping_doc_to_po_line",
        "shipping_doc",
        "po_line",
        (("po_number", "po_number"),),
        method="po_level",
        expect_src=always,
        src_orphan="Supplier shipping document for a PO that is not in SAP",
        context=("file",),
    ),
    Rule(
        "receipt_to_scan",
        "goods_receipt",
        "delivery_note_scan",
        (("delivery_note", "delivery_note"),),
        expect_dst=always,
        dst_orphan="Delivery-note scan with no goods receipt",
        context=("file",),
    ),
    Rule(
        "inspection_to_receipt",
        "inspection_report",
        "goods_receipt",
        (("batch", "gr_number"),),
        expect_src=always,
        src_orphan="Inspection report for a batch that was never received",
        context=("file",),
    ),
    _part_rule("po_line"),
    _part_rule("goods_receipt"),
    _part_rule("stock_batch"),
    _part_rule("shipment", "material_ids"),
]

# Links between parts of one chain; supplier and material must agree across them.
CHAIN_RULES = {
    "receipt_to_po_line",
    "stock_to_receipt",
    "shipment_to_po_line",
    "confirmation_to_po_line",
    "shipping_doc_to_po_line",
    "shipping_doc_to_shipment",
}


def _context(record: dict, keys: tuple[str, ...]) -> str | None:
    shown = [f"{k}={record['attrs'][k]}" for k in keys if record["attrs"].get(k) is not None]
    return ", ".join(shown) or None


def _issue(rule, type_, record_id, detail, related=None, context=None) -> dict:
    return {
        "rule": rule,
        "type": type_,
        "record_id": record_id,
        "related": related,
        "detail": detail,
        "context": context,
    }


def _key_frame(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    keys = [f"k{i}" for i in range(len(columns))]
    frame = df[["record_id", *columns]].copy()
    frame.columns = ["record_id", *keys]
    for key in keys:
        if frame[key].map(lambda v: isinstance(v, list)).any():
            frame = frame.explode(key)
    return frame.dropna(subset=keys).astype({k: str for k in keys})


def apply_rule(records: pd.DataFrame, rule: Rule) -> tuple[pd.DataFrame, list[dict], list[dict]]:
    """Return (links, issues, coverage rows) for one rule."""
    src = records[records.kind == rule.src]
    dst = records[records.kind == rule.dst]
    src_keys = _key_frame(src, [s for s, _ in rule.on])
    dst_keys = _key_frame(dst, [d for _, d in rule.on])
    keys = [c for c in src_keys.columns if c != "record_id"]
    merged = src_keys.merge(dst_keys, on=keys, suffixes=("_src", "_dst"))
    links = pd.DataFrame(
        {
            "src": merged["record_id_src"],
            "dst": merged["record_id_dst"],
            "rule": rule.name,
            "method": rule.method,
            "key": merged[keys].astype(str).agg("/".join, axis=1) if len(merged) else [],
        },
        columns=LINK_COLUMNS,
    ).drop_duplicates(subset=["src", "dst"])

    issues, coverage = [], []
    for side, frame, predicate, message, linked in (
        ("src", src, rule.expect_src, rule.src_orphan, set(links.src)),
        ("dst", dst, rule.expect_dst, rule.dst_orphan, set(links.dst)),
    ):
        if predicate is None:
            continue
        expected = [r for r in frame.to_dict("records") if predicate(r)]
        missing = [r for r in expected if r["record_id"] not in linked]
        coverage.append(
            {
                "rule": rule.name,
                "side": side,
                "kind": rule.src if side == "src" else rule.dst,
                "method": rule.method,
                "expected": len(expected),
                "linked": len(expected) - len(missing),
            }
        )
        issues += [
            _issue(rule.name, "orphan", r["record_id"], message, context=_context(r, rule.context))
            for r in missing
        ]
    return links, issues, coverage


# --- Versions of the same entity -------------------------------------------------------------


def _short(path: str) -> str:
    return path.rsplit("/", 1)[-1]


MAX_VALUE_CHARS = 80


def _shorten(value) -> str:
    text = str(value)
    return text if len(text) <= MAX_VALUE_CHARS else text[: MAX_VALUE_CHARS - 1] + "…"


def _version_diff(rows: list[dict]) -> tuple[list[str], list[str]]:
    """Fields the versions disagree on, and fields only some versions have."""
    present = [{k for k, v in r["attrs"].items() if v is not None} for r in rows]
    shared = set.intersection(*present)
    diffs = []
    for key in sorted(shared):
        if len({str(r["attrs"][key]) for r in rows}) > 1:
            shown = " vs ".join(
                f"{_shorten(r['attrs'][key])} ({_short(r['source'])})" for r in rows
            )
            diffs.append(f"{key}: {shown}")
    one_sided = []
    for row, keys in zip(rows, present):
        only = sorted(keys - shared)
        if only:
            fields = ", ".join(f"{k}={_shorten(row['attrs'][k])}" for k in only)
            one_sided.append(f"only in {_short(row['source'])}: {fields}")
    return diffs, one_sided


def version_links(records: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """
    Link every version of the same entity, and report the fields they disagree on.

    The linker does not pick a winner: which version to believe is the judge's decision.
    """
    links, issues = [], []
    versions = records[records.entity.duplicated(keep=False)]
    for entity, group in versions.groupby("entity"):
        rows = group.to_dict("records")
        for a, b in zip(rows, rows[1:]):
            links.append(
                {
                    "src": a["record_id"],
                    "dst": b["record_id"],
                    "rule": "same_entity",
                    "method": "exact",
                    "key": entity,
                }
            )
        diffs, one_sided = _version_diff(rows)
        ids = [r["record_id"] for r in rows]
        # as_of lets the judge apply 'newer beats older'; "unknown" where a source has no date
        as_of = "as_of: " + " vs ".join(
            f"{r['as_of'] or 'unknown'} ({_short(r['source'])})" for r in rows
        )
        context = "; ".join([*diffs, *one_sided, as_of])
        if diffs:
            detail = f"{len(rows)} versions disagree"
            issues.append(
                _issue("versions", "conflict", ids[0], detail, ", ".join(ids[1:]), context)
            )
        else:
            detail = f"{len(rows)} identical versions"
            issues.append(
                _issue("versions", "duplicate", ids[0], detail, ", ".join(ids[1:]), context)
            )
    return pd.DataFrame(links, columns=LINK_COLUMNS), issues


# --- Structural checks -----------------------------------------------------------------------


def check_link_agreement(records: pd.DataFrame, links: pd.DataFrame) -> list[dict]:
    """Records linked into one chain must name the same supplier and material."""
    by_id = records.set_index("record_id")
    issues = []
    for link in links[links.rule.isin(CHAIN_RULES)].itertuples():
        for column in ("supplier_id", "material_id"):
            a, b = by_id.at[link.src, column], by_id.at[link.dst, column]
            if a is not None and b is not None and a != b:
                issues.append(
                    _issue(
                        "link_agreement",
                        "conflict",
                        link.src,
                        f"{column} differs across a {link.rule} link: {a} vs {b}",
                        link.dst,
                    )
                )
    return issues


def check_delivery_note_suppliers(records: pd.DataFrame, links: pd.DataFrame) -> list[dict]:
    receipts = records[(records.kind == "goods_receipt") & records.delivery_note.notna()]
    issues = []
    for note, group in receipts.groupby("delivery_note"):
        suppliers = sorted(set(group.supplier_id.dropna()))
        if len(suppliers) > 1:
            issues += [
                _issue(
                    "delivery_note_suppliers",
                    "conflict",
                    rid,
                    f"Delivery note {note} used by suppliers {', '.join(suppliers)}",
                )
                for rid in group.record_id
            ]
    return issues


def check_open_line_received(records: pd.DataFrame, links: pd.DataFrame) -> list[dict]:
    received = set(links[links.rule == "receipt_to_po_line"].dst)
    lines = records[(records.kind == "po_line") & records.record_id.isin(received)]
    return [
        _issue(
            "open_line_received",
            "conflict",
            r["record_id"],
            "Goods were received, but the PO line has no delivery date",
            context=_context(r, ("status",)),
        )
        for r in lines.to_dict("records")
        if r["attrs"].get("state") == "open"
    ]


def check_shipment_pos_known(records: pd.DataFrame, links: pd.DataFrame) -> list[dict]:
    known = set(records[records.kind == "po_line"].po_number)
    issues = []
    for r in records[records.kind.isin(["shipment", "tracker_row"])].to_dict("records"):
        for po in r["po_numbers"] or []:
            if po not in known:
                issues.append(
                    _issue(
                        "shipment_po_known",
                        "orphan",
                        r["record_id"],
                        f"References PO {po}, which is not in SAP",
                    )
                )
    return issues


def check_tracker_pos_match(records: pd.DataFrame, links: pd.DataFrame) -> list[dict]:
    by_id = records.set_index("record_id")
    issues = []
    for link in links[links.rule == "shipment_to_tracker"].itertuples():
        feed = set(by_id.at[link.src, "po_numbers"] or [])
        tracker = set(by_id.at[link.dst, "po_numbers"] or [])
        if feed != tracker:
            issues.append(
                _issue(
                    "tracker_pos_match",
                    "conflict",
                    link.src,
                    f"Feed lists POs {sorted(feed)}, tracker lists {sorted(tracker)}",
                    link.dst,
                )
            )
    return issues


def check_unresolved_suppliers(records: pd.DataFrame, links: pd.DataFrame) -> list[dict]:
    return [
        _issue(
            "unresolved_supplier",
            "orphan",
            r["record_id"],
            f"Unknown supplier '{r['attrs']['supplier_raw']}'",
        )
        for r in records.to_dict("records")
        if r["attrs"].get("supplier_raw")
    ]


def _missing_since(
    records: pd.DataFrame,
    linked: set[str],
    kind: str,
    date_attr: str,
    since: str | None,
    rule: str,
    message: str,
    context: tuple[str, ...],
    predicate: Predicate = always,
) -> list[dict]:
    """
    Records of `kind` dated on or after `since` that have no link. Document archives start at
    some date (scans in September, confirmations mid-2025); records older than the archive can't
    have a document and are not flagged. `since` comes from the data, never from code.
    """
    if since is None:
        return []
    return [
        _issue(
            rule,
            "orphan",
            r["record_id"],
            f"{message} (archive starts {since})",
            context=_context(r, context),
        )
        for r in records[records.kind == kind].to_dict("records")
        if (r["attrs"].get(date_attr) or "") >= since
        and r["record_id"] not in linked
        and predicate(r)
    ]


def check_receipts_without_scan(records: pd.DataFrame, links: pd.DataFrame) -> list[dict]:
    """Since the earliest scan: receipts without a delivery-note scan."""
    scans = records[records.kind == "delivery_note_scan"]
    dates = [a.get("scan_date") for a in scans["attrs"] if a.get("scan_date")]
    return _missing_since(
        records,
        set(links[links.rule == "receipt_to_scan"].src),
        "goods_receipt",
        "gr_date",
        min(dates) if dates else None,
        "receipt_to_scan",
        "Goods receipt without a delivery-note scan",
        ("gr_date", "qty_received"),
    )


def check_lines_without_confirmation(records: pd.DataFrame, links: pd.DataFrame) -> list[dict]:
    """Since the earliest confirmed order: PO lines without any supplier confirmation."""
    confirmed = set(links[links.rule == "confirmation_to_po_line"].dst)
    lines = records[records.record_id.isin(confirmed)]
    dates = [a.get("order_date") for a in lines["attrs"] if a.get("order_date")]
    return _missing_since(
        records,
        confirmed,
        "po_line",
        "order_date",
        min(dates) if dates else None,
        "confirmation_to_po_line",
        "PO line without any supplier confirmation",
        ("status", "order_date", "promised_date"),
        predicate=not_cancellation,
    )


CHECKS = [
    check_receipts_without_scan,
    check_lines_without_confirmation,
    check_link_agreement,
    check_delivery_note_suppliers,
    check_open_line_received,
    check_shipment_pos_known,
    check_tracker_pos_match,
    check_unresolved_suppliers,
]


def apply_all(records: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    all_links, issues, coverage = [], [], []
    for rule in RULES:
        links, rule_issues, rule_coverage = apply_rule(records, rule)
        all_links.append(links)
        issues += rule_issues
        coverage += rule_coverage
    links, version_issues = version_links(records)
    all_links.append(links)
    issues += version_issues
    links = (
        pd.concat([lk for lk in all_links if len(lk)], ignore_index=True)
        if any(len(lk) for lk in all_links)
        else pd.DataFrame(columns=LINK_COLUMNS)
    )
    for check in CHECKS:
        issues += check(records, links)
    issues_df = pd.DataFrame(issues, columns=ISSUE_COLUMNS, dtype=object)  # keep None, not NaN
    return links, prioritise(records, links, issues_df), pd.DataFrame(coverage)


# --- Priority ----------------------------------------------------------------------------------

# Kinds whose own facts say whether they still matter. Everything else inherits from its links.
LIVE_BY_KIND: dict[str, Predicate] = {
    "po_line": lambda r: r["attrs"].get("state") == "open",
    "shipment": always,  # the feed lists open shipments
    "tracker_row": always,
    "stock_batch": lambda r: (r["attrs"].get("quantity") or 0) > 0,
    "supplier": always,
}
DUE_BY_KIND: dict[str, tuple[str, ...]] = {
    "po_line": ("promised_date", "requested_date"),
    "shipment": ("eta",),
    "tracker_row": ("eta",),
}
INHERIT_HOPS = 2
HUBS = HUB_KINDS  # parts and suppliers connect everything; liveness never flows through them


def _own(record: dict, receipt_stock: dict[str, list[float]]) -> tuple[bool | None, str | None]:
    """(live, due) from the record's own facts; live is None when its kind has no rule."""
    due = next(
        (record["attrs"][k] for k in DUE_BY_KIND.get(record["kind"], ()) if record["attrs"].get(k)),
        None,
    )
    if record["kind"] == "goods_receipt":
        # A receipt matters while its batch is in stock. Only stock decides: its PO line is
        # always delivered, which says nothing. No stock row at all is unknown, so it stays live.
        batches = receipt_stock.get(record["record_id"])
        return (any(q > 0 for q in batches) if batches else True), due
    predicate = LIVE_BY_KIND.get(record["kind"])
    return (predicate(record) if predicate else None), due


def prioritise(records: pd.DataFrame, links: pd.DataFrame, issues: pd.DataFrame) -> pd.DataFrame:
    """
    Mark each issue live or closed and give it a due date, then sort: live first, soonest first.

    Receipts are judged by their stock batch only. A record without its own rule (a document) is
    live if a record within two links is live. With nothing to judge by, a record stays live:
    unknown is not hidden.
    """
    if issues.empty:
        return issues
    by_id = {r["record_id"]: r for r in records.to_dict("records")}
    neighbours: dict[str, set[str]] = {}
    for link in links.itertuples():
        if (
            by_id.get(link.src, {}).get("kind") in HUBS
            or by_id.get(link.dst, {}).get("kind") in HUBS
        ):
            continue
        neighbours.setdefault(link.src, set()).add(link.dst)
        neighbours.setdefault(link.dst, set()).add(link.src)
    receipt_stock: dict[str, list[float]] = {}
    for link in links[links.rule == "stock_to_receipt"].itertuples():
        quantity = by_id[link.src]["attrs"].get("quantity") or 0
        receipt_stock.setdefault(link.dst, []).append(quantity)

    def judge(record_id: str) -> tuple[bool, str | None]:
        record = by_id.get(record_id)
        if record is None:
            return True, None
        live, due = _own(record, receipt_stock)
        if live is not None:
            return live, due
        # Walk outwards, but stop at records with their own rule: their verdict is the answer,
        # and walking past them would borrow liveness from unrelated records behind them.
        frontier, seen, verdicts = {record_id}, {record_id}, []
        for _ in range(INHERIT_HOPS):
            frontier = {n for f in frontier for n in neighbours.get(f, ())} - seen
            seen |= frontier
            own = {n: _own(by_id[n], receipt_stock) for n in frontier if n in by_id}
            verdicts += [v for v in own.values() if v[0] is not None]
            frontier = {n for n, v in own.items() if v[0] is None}
        judged = [(lv, d) for lv, d in verdicts if lv is not None]
        if not judged:
            return True, None
        dues = sorted(d for lv, d in judged if lv and d)
        return any(lv for lv, _ in judged), (dues[0] if dues else None)

    verdict = issues.record_id.map(judge)
    issues = issues.copy()
    issues["live"] = pd.Series([v[0] for v in verdict], index=issues.index, dtype=bool)
    # object dtype: a missing date stays None (pandas would make it NaN)
    issues["due"] = pd.Series([v[1] for v in verdict], index=issues.index, dtype=object)
    order = issues.assign(_closed=~issues.live.astype(bool), _due=issues.due.fillna("9999"))
    return issues.loc[order.sort_values(["_closed", "_due", "record_id"]).index].reset_index(
        drop=True
    )
