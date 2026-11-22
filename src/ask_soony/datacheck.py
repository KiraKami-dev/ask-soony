"""
Data check: say out loud when the customer's files change shape.

Spreadsheets are recognised by their columns, so a renamed column silently drops a file from the
picture ("the system says we're fine"). After every portal change this compares each CSV's header
with the last run and with what the readers need, and lists what is no longer read, what lost
columns, and new files nobody reads yet. Other formats (sheets, JSON, Word, PDFs, scans) are
checked for the last one: a new file no reader picks up is listed instead of ignored. It warns;
it never guesses a rename on its own.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()  # before importing api, which includes this router

from ask_soony import api  # noqa: E402

STATE = "data_shape.json"
FOLDERS = ("soony/erp", "soony/master")


def _header(f: Path) -> list[str]:
    rows = api.read_csv(f)
    # A row with more fields than the header adds a column without a name: not a column.
    return [k for k in rows[0].keys() if isinstance(k, str) and k] if rows else []


def _verifier_reads(f: Path) -> bool:
    """The verifier recognises spreadsheets by their columns too (its own layouts)."""
    try:
        from ask_soony.verifier.linker.discover import classify

        return classify(f)[0] != "unknown"
    except Exception:
        return False


def _mapped(f: Path, seen: dict) -> dict | None:
    """How a CSV is read through the column mapping (columns.py), when its names are not ours."""
    info = seen.get("mapped")
    if info is None and seen.get("read"):
        try:
            from ask_soony import columns

            info = columns.describe(f)
        except Exception:
            info = None
    return info if info and info.get("renamed") else None


def mapped_text(name: str, info: dict) -> str:
    from ask_soony import columns

    pairs = list(info["renamed"].items())
    shown = ", ".join(f"{o} → {c}" for o, c in pairs[:4]) + (", …" if len(pairs) > 4 else "")
    n = len(info.get("model") or [])
    by_model = f"; {n} column{'s' if n != 1 else ''} mapped by the model" if n else ""
    label = columns.LABELS.get(info.get("kind") or "", "a known file")
    return f"{name} is read as {label} via column mapping ({shown}{by_model})."


def _other_reader(rel: str, f: Path) -> str | None:
    """Which part of the product reads a non-CSV portal file, or None."""
    from ask_soony import papercheck
    from ask_soony.predictor import is_event_path

    suffix = f.suffix.lower()
    if f.name.lower().startswith("profile.") and rel.startswith("suppliers/"):
        return "supplier master"
    if suffix in papercheck.DOC_SUFFIXES and not rel.startswith(papercheck.SYSTEM_FOLDERS):
        return "paper check"
    if is_event_path(rel):
        return "event impact"
    if suffix in (".xlsx", ".xlsm") and rel.startswith("soony/") and "/master/" not in rel:
        return "tracking"  # every sheet under soony/ is matched against the shipments
    if suffix == ".docx" and rel.startswith("soony/context/"):
        try:
            from ask_soony import memo

            return "memo" if memo.READS_DOCX else None
        except ImportError:
            return None
    if suffix in (".xlsx", ".xlsm", ".json") and _verifier_reads(f):
        return "verifier"
    return None


def check(root: Path | None = None, now: datetime | None = None) -> dict:
    """Compare CSV headers with the readers' needs and the last run; save the result."""
    from ask_soony import papercheck

    root = root or api.portal_root()
    if root is None:
        return {"checked_at": None, "issues": []}
    papercheck.READS.clear()
    papercheck.load_erp(root)
    reads = dict(papercheck.READS)
    before = api.read_json(STATE, {}) or {}
    old = before.get("headers") or {}
    old_mapped = before.get("mapped") or {}
    mapped: dict[str, dict] = {}
    at = (now or datetime.now().astimezone()).isoformat()
    headers: dict[str, list[str]] = {}
    issues: list[dict] = []
    for folder in FOLDERS:
        base = root / folder
        for f in sorted(base.rglob("*.csv")) if base.is_dir() else []:
            rel = str(f.relative_to(root))
            head = headers[rel] = _header(f)
            if not head:
                continue
            seen = dict(reads.get(rel) or {"read": False, "wanted": []})
            if not seen["read"] and _verifier_reads(f):
                seen["read"] = True
            # A "column" that was a whole header read with the wrong separator is not lost when
            # its parts are all there now (the file was read right this time).
            lost = [
                c
                for c in old.get(rel, [])
                if isinstance(c, str)
                and c not in head
                and not set(filter(None, re.split(r"[;,\t|]", c))) <= set(head)
            ]
            info = _mapped(f, seen)
            if info:
                mapped[rel] = info["renamed"]
            if not seen["read"] and seen["wanted"]:
                missing = sorted({c for w in seen["wanted"] for c in w["missing"]})
                issues.append(
                    {
                        "kind": "not_read",
                        "file": rel,
                        "missing": missing,
                        "text": f"{f.name} is not read: it has no column "
                        f"{', '.join(missing)}. Its rows are left out until fixed.",
                    }
                )
            elif lost:
                issues.append(
                    {
                        "kind": "changed",
                        "file": rel,
                        "missing": lost,
                        "text": f"{f.name} lost column {', '.join(lost)} since the last check.",
                    }
                )
            elif info and old_mapped.get(rel) != info["renamed"]:  # read, under other names
                issues.append(
                    {
                        "kind": "mapped",
                        "file": rel,
                        "missing": [],
                        "columns": info["renamed"],
                        "model": info.get("model") or [],
                        "text": mapped_text(f.name, info),
                    }
                )
            elif not seen["read"] and old and rel not in old:  # first run is the baseline
                issues.append(
                    {
                        "kind": "unused",
                        "file": rel,
                        "missing": [],
                        "text": f"{f.name} is new and nobody reads it yet.",
                    }
                )
    # Supplier spreadsheets the readers picked up by their columns (a revised schedule, say).
    for rel, seen in sorted(reads.items()):
        info = seen.get("mapped") if seen.get("read") else None
        if rel.startswith(FOLDERS) or not info or not info.get("renamed"):
            continue
        mapped[rel] = info["renamed"]
        if old_mapped.get(rel) != info["renamed"]:
            issues.append(
                {
                    "kind": "mapped",
                    "file": rel,
                    "missing": [],
                    "columns": info["renamed"],
                    "model": info.get("model") or [],
                    "text": mapped_text(Path(rel).name, info),
                }
            )
    # Files known at the last run; a state from before this check existed is a baseline too.
    baseline = before.get("files") is None
    known = set(before.get("files") or [])
    files: list[str] = []
    for f in sorted(root.rglob("*")):
        if not f.is_file() or f.suffix.lower() == ".csv" or f.name.startswith("."):
            continue
        rel = str(f.relative_to(root))
        files.append(rel)
        if not baseline and rel not in known and _other_reader(rel, f) is None:
            kind = f.suffix.lower().lstrip(".").upper() or "file"
            issues.append(
                {
                    "kind": "unused",
                    "file": rel,
                    "missing": [],
                    "text": f"{f.name} is new and nobody reads it yet ({kind}).",
                }
            )
    out = {
        "checked_at": at,
        "headers": headers,
        "files": files,
        "mapped": mapped,
        "issues": issues,
    }
    api.write_json(STATE, out)
    return out


@router.get("/api/data-health")
def data_health():
    """The last data check: issues only (headers stay on the server). Files read through the
    column mapping are no gap in the picture, so they come separately as `mapped`."""
    last = api.read_json(STATE, {}) or {}
    issues = last.get("issues") or []
    return {
        "checked_at": last.get("checked_at"),
        "issues": [i for i in issues if i.get("kind") != "mapped"],
        "mapped": [i for i in issues if i.get("kind") == "mapped"],
    }
