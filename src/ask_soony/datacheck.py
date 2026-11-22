"""
Data check: say out loud when the customer's files change shape.

Spreadsheets are recognised by their columns, so a renamed column silently drops a file from the
picture ("the system says we're fine"). After every portal change this compares each CSV's header
with the last run and with what the readers need, and lists what is no longer read, what lost
columns, and new files nobody reads yet. It warns; it never guesses a rename on its own.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()  # before importing api, which includes this router

from ask_soony import api  # noqa: E402

STATE = "data_shape.json"
FOLDERS = ("soony/erp", "soony/master")


def _header(f: Path) -> list[str]:
    rows = api.read_csv(f)
    return list(rows[0].keys()) if rows else []


def _verifier_reads(f: Path) -> bool:
    """The verifier recognises spreadsheets by their columns too (its own layouts)."""
    try:
        from ask_soony.verifier.linker.discover import classify

        return classify(f)[0] != "unknown"
    except Exception:
        return False


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
            lost = [c for c in old.get(rel, []) if c not in head]
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
            elif not seen["read"] and old and rel not in old:  # first run is the baseline
                issues.append(
                    {
                        "kind": "unused",
                        "file": rel,
                        "missing": [],
                        "text": f"{f.name} is new and nobody reads it yet.",
                    }
                )
    out = {"checked_at": at, "headers": headers, "issues": issues}
    api.write_json(STATE, out)
    return out


@router.get("/api/data-health")
def data_health():
    """The last data check: issues only (headers stay on the server)."""
    last = api.read_json(STATE, {}) or {}
    return {"checked_at": last.get("checked_at"), "issues": last.get("issues") or []}
