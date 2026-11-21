"""
Deterministic "what changed" summaries for file versions, no LLM involved.

CSV files are compared row by row (keyed by their first column when it is unique), Excel files
cell by cell, everything else as text. The code knows nothing about the customer's columns or
values; it only compares two versions of whatever the portal serves.
"""

import csv
import difflib
import io
from pathlib import PurePosixPath

from ask_soony.extract import decode_text, extract

MAX_ITEMS = 12
MAX_DIFF_LINES = 400


def summarize(path: str, old: bytes | None, new: bytes) -> tuple[str, str]:
    """Return (one-paragraph summary, detail diff) for a new or changed file."""
    suffix = PurePosixPath(path).suffix.lower()
    try:
        if suffix in {".csv", ".tsv"}:
            return _csv(old, new)
        if suffix in {".xlsx", ".xlsm"}:
            return _xlsx(old, new)
        if suffix == ".json" and old is None:
            return _json_new(new)
    except Exception as e:  # fall back to a text diff for files that do not parse cleanly
        note = f"(structured comparison failed: {type(e).__name__}) "
        summary, diff = _text(path, old, new)
        return note + summary, diff
    return _text(path, old, new)


# --- CSV -----------------------------------------------------------------------------------


def _parse_csv(data: bytes) -> tuple[list[str], list[list[str]]]:
    text = decode_text(data)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
    if not rows:
        return [], []
    return rows[0], rows[1:]


def _keyed(header: list[str], rows: list[list[str]]) -> tuple[dict, bool]:
    keys = [r[0] if r else "" for r in rows]
    if keys and len(set(keys)) == len(keys):
        return {k: r for k, r in zip(keys, rows)}, True
    return {"|".join(r): r for r in rows}, False


def _csv(old: bytes | None, new: bytes) -> tuple[str, str]:
    h_new, r_new = _parse_csv(new)
    if old is None:
        cols = ", ".join(h_new[:10]) + (" …" if len(h_new) > 10 else "")
        return f"New table: {len(r_new)} rows, {len(h_new)} columns ({cols}).", ""
    h_old, r_old = _parse_csv(old)
    lines, parts = [], []
    added_cols = [c for c in h_new if c not in h_old]
    removed_cols = [c for c in h_old if c not in h_new]
    # A removed and an added column holding the same values (on rows in both) is a rename.
    idx_old = {c: i for i, c in enumerate(h_old)}
    idx_new = {c: i for i, c in enumerate(h_new)}
    rows_old = {r[0]: r for r in r_old if r}
    common = [r for r in r_new if r and r[0] in rows_old]

    def cell(row, i):
        return row[i] if i < len(row) else ""

    renamed = []
    for a in list(added_cols):
        for r in list(removed_cols):
            if common and all(
                cell(row, idx_new[a]) == cell(rows_old[row[0]], idx_old[r]) for row in common
            ):
                renamed.append((r, a))
                added_cols.remove(a)
                removed_cols.remove(r)
                break
    if renamed:
        parts.append("columns renamed: " + ", ".join(f"{r} → {a}" for r, a in renamed))
    if added_cols:
        parts.append(f"columns added: {', '.join(added_cols)}")
    if removed_cols:
        parts.append(f"columns removed: {', '.join(removed_cols)}")
    k_old, by_key_old = _keyed(h_old, r_old)
    k_new, by_key_new = _keyed(h_new, r_new)
    by_key = by_key_old and by_key_new
    added = [k for k in k_new if k not in k_old]
    removed = [k for k in k_old if k not in k_new]
    changed = []
    if by_key:
        shared = [c for c in h_new if c in h_old]
        for k in k_new:
            if k in k_old:
                o = dict(zip(h_old, k_old[k]))
                n = dict(zip(h_new, k_new[k]))
                cells = [
                    (c, o.get(c, ""), n.get(c, "")) for c in shared if o.get(c, "") != n.get(c, "")
                ]
                if cells:
                    changed.append((k, cells))
    if added:
        parts.append(f"{len(added)} rows added")
    if removed:
        parts.append(f"{len(removed)} rows removed")
    if changed:
        parts.append(f"{len(changed)} rows changed")
    if not parts:
        parts.append("no row or column changes (formatting or order only)")
    summary = f"Table {len(r_old)} → {len(r_new)} rows: " + "; ".join(parts) + "."
    if changed:
        examples = []
        for k, cells in changed[:3]:
            c, o, n = cells[0]
            examples.append(f"{k}: {c} {o or '∅'} → {n or '∅'}")
        summary += " E.g. " + "; ".join(examples) + "."
    for k in added[:MAX_ITEMS]:
        lines.append(f"+ {', '.join(k_new[k])}")
    for k in removed[:MAX_ITEMS]:
        lines.append(f"- {', '.join(k_old[k])}")
    for k, cells in changed[:MAX_ITEMS]:
        lines.append(f"~ {k}: " + "; ".join(f"{c}: {o or '∅'} → {n or '∅'}" for c, o, n in cells))
    more = max(0, len(added) + len(removed) + len(changed) - 3 * MAX_ITEMS)
    if more:
        lines.append(
            f"… and more ({len(added)} added, {len(removed)} removed, {len(changed)} changed)"
        )
    return summary, "\n".join(lines)


# --- Excel ---------------------------------------------------------------------------------


def _cells(data: bytes) -> dict[str, dict[str, str]]:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    out = {}
    for ws in wb.worksheets:
        out[ws.title] = {
            c.coordinate: str(c.value) for row in ws.iter_rows() for c in row if c.value is not None
        }
    return out


def _xlsx(old: bytes | None, new: bytes) -> tuple[str, str]:
    s_new = _cells(new)
    if old is None:
        sheets = ", ".join(f"{name} ({len(cells)} cells)" for name, cells in s_new.items())
        return f"New workbook: {len(s_new)} sheet(s): {sheets}.", ""
    s_old = _cells(old)
    parts, lines = [], []
    for name in s_new:
        if name not in s_old:
            parts.append(f"sheet added: {name}")
    for name in s_old:
        if name not in s_new:
            parts.append(f"sheet removed: {name}")
    for name, cells in s_new.items():
        before = s_old.get(name, {})
        diffs = [
            (ref, before.get(ref, ""), val)
            for ref, val in cells.items()
            if before.get(ref, "") != val
        ] + [(ref, val, "") for ref, val in before.items() if ref not in cells]
        if diffs and name in s_old:
            ex = "; ".join(f"{r}: {o or '∅'} → {n or '∅'}" for r, o, n in diffs[:3])
            parts.append(f"{name}: {len(diffs)} cells changed (e.g. {ex})")
            lines += [f"{name}!{r}: {o or '∅'} → {n or '∅'}" for r, o, n in diffs[:MAX_DIFF_LINES]]
    return (
        "Workbook: " + "; ".join(parts) + "."
    ) if parts else "Workbook: no value changes.", "\n".join(lines)


# --- JSON ----------------------------------------------------------------------------------


def _json_new(new: bytes) -> tuple[str, str]:
    import json

    data = json.loads(decode_text(new))
    texts: list[str] = []

    def walk(node):
        if len(texts) >= 3:
            return
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str) and len(node) > 25 and not node[:4].isdigit():
            texts.append(node)

    walk(data)
    if isinstance(data, dict):
        shape = "keys: " + ", ".join(list(data)[:8])
        lists = [f"{k} ({len(v)} items)" for k, v in data.items() if isinstance(v, list)]
        if lists:
            shape += "; lists: " + ", ".join(lists)
    else:
        shape = f"list of {len(data)} items" if isinstance(data, list) else type(data).__name__
    summary = f"New JSON, {shape}."
    if texts:
        summary += " " + " / ".join(f"“{t[:110]}”" for t in texts)
    return summary, ""


# --- everything else -----------------------------------------------------------------------


def _text(path: str, old: bytes | None, new: bytes) -> tuple[str, str]:
    t_new = extract(path, new, render_images=False).text
    if old is None:
        if not t_new.strip():
            return "New file without a text layer (scan or image).", ""
        first = next(
            (
                ln.strip()
                for ln in t_new.splitlines()
                if ln.strip() and not ln.startswith("--- page")
            ),
            "",
        )
        return f"New document, {len(t_new.splitlines())} lines. Starts: “{first[:120]}”.", ""
    t_old = extract(path, old, render_images=False).text
    if not t_new.strip() and not t_old.strip():
        return "Scan or image replaced (no text layer to compare).", ""
    diff = list(
        difflib.unified_diff(
            t_old.splitlines(), t_new.splitlines(), "before", "after", lineterm="", n=1
        )
    )
    plus = sum(1 for ln in diff if ln.startswith("+") and not ln.startswith("+++"))
    minus = sum(1 for ln in diff if ln.startswith("-") and not ln.startswith("---"))
    if not plus and not minus:
        return "Content identical as text (only binary or formatting changes).", ""
    first_new = next(
        (
            ln[1:].strip()
            for ln in diff
            if ln.startswith("+") and not ln.startswith("+++") and ln[1:].strip()
        ),
        "",
    )
    summary = f"Text changed: +{plus} / −{minus} lines."
    if first_new:
        summary += f" First new line: “{first_new[:120]}”."
    return summary, "\n".join(diff[:MAX_DIFF_LINES])
