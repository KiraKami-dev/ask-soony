"""
The reading baseline: document readings frozen in the repo, never written by a run.

The first full discovery run (a strong model on every document) is kept in `baseline_sonnet/`,
one folder per document version like the live store. Anything that reads readings looks in the
live store first and falls back to the baseline for the same file version (matched by checksum),
so a new model or prompt never overwrites it and every clone has the readings without a run.
The baseline only grows: `add` copies readings of file versions it does not have yet.
"""

import json
from pathlib import Path

BASELINE_DIR = Path(__file__).parent / "baseline_sonnet"
IGNORED_FILE = Path(__file__).parent / "ignored_documents.json"


def ignored_paths(path: Path = IGNORED_FILE) -> set[str]:
    """Portal paths the team decided never to read (with the reason in the file)."""
    try:
        return {d["path"] for d in json.loads(path.read_text(encoding="utf-8"))["documents"]}
    except (OSError, json.JSONDecodeError, KeyError):
        return set()


def _ok_metas(folder: Path) -> list[tuple[Path, dict]]:
    """(meta.json path, meta) of every successful reading in a store folder."""
    out = []
    for meta_path in sorted(folder.glob("*/meta.json")) if folder.is_dir() else []:
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("status") == "ok" and (meta_path.parent / "verdict.json").exists():
            out.append((meta_path, meta))
    return out


def baseline_keys(baseline: Path | None = BASELINE_DIR) -> set[tuple[str, str]]:
    """(path, checksum) of every file version the baseline has read successfully."""
    if baseline is None:
        return set()
    return {(meta.get("path"), meta.get("sha256")) for _, meta in _ok_metas(baseline)}


def reading_metas(
    live: Path | None, baseline: Path | None = BASELINE_DIR, portal_paths: set[str] | None = None
) -> list[Path]:
    """
    meta.json paths of every successful reading: the baseline's, plus the live store's for file
    versions the baseline has not read. Same file version in both: the baseline wins, so a re-read
    with a cheaper model never replaces it silently. With `portal_paths`, baseline readings of
    files not in the portal are left out.
    """
    frozen = [
        (meta_path, meta)
        for meta_path, meta in (_ok_metas(baseline) if baseline is not None else [])
        if portal_paths is None or meta.get("path") in portal_paths
    ]
    seen = {(meta.get("path"), meta.get("sha256")) for _, meta in frozen}
    live_only = [
        meta_path
        for meta_path, meta in (_ok_metas(live) if live is not None else [])
        if (meta.get("path"), meta.get("sha256")) not in seen
    ]
    return [meta_path for meta_path, _ in frozen] + live_only


def add(source: Path, baseline: Path = BASELINE_DIR) -> list[str]:
    """
    Copy successful readings from a store into the baseline, only for file versions it lacks.
    Nothing in the baseline is changed. Returns the paths added. Only reading files are copied
    (verdict.json, meta.json), never transcripts.
    """
    have = baseline_keys(baseline)
    added = []
    for meta_path, meta in _ok_metas(source):
        if (meta.get("path"), meta.get("sha256")) in have:
            continue
        target = baseline / meta_path.parent.name
        target.mkdir(parents=True, exist_ok=True)  # an existing folder holds a failed read
        for name in ("meta.json", "verdict.json"):
            (target / name).write_bytes((meta_path.parent / name).read_bytes())
        added.append(meta["path"])
    return added


def main() -> None:
    """uv run python -m ask_soony.verifier.baseline add <store>"""
    import argparse

    parser = argparse.ArgumentParser(prog="ask_soony.verifier.baseline")
    parser.add_argument("action", choices=["add"])
    parser.add_argument("source", type=Path, help="store folder with readings to add")
    args = parser.parse_args()
    added = add(args.source)
    for path in added:
        print(f"added  {path}")
    print(f"{len(added)} readings added to {BASELINE_DIR}")


if __name__ == "__main__":
    main()
