"""
Demo cards from the real portal data: runs the paper check (src/ask_soony/papercheck.py), which
reads every supplier document, holds it against the ERP and writes findings, decisions and
impacts into data/state.

Nothing about the cases lives here any more: the paper check finds them on its own. Entries are
merged by id (other entries stay), so running it twice changes nothing but the timestamps. Nothing
is approved and no email is sent.

    uv run python scripts/demo_cards.py            # add or refresh the cards
    uv run python scripts/demo_cards.py --reset    # also clear decisions, actions, replies,
                                                   # Teams posts and drafts; switch routines off
    uv run python scripts/demo_cards.py --no-llm   # deterministic extraction only

--reset first backs up every file it changes as <name>.before-demo-<timestamp>.json and removes
the hand-written cards an earlier version of this script wrote (ids gr-*, rev-*, qa-*).
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from ask_soony import api, papercheck  # noqa: E402

RESET_FILES = {
    "decision_status.json": {},
    "actions.json": [],
    "replies_seen.json": [],
    "teams_posted.json": [],
    "routine_drafts.json": {},
}
# Ids the hand-curated version of this script used: <case>-<number>-<part>.
OLD_IDS = re.compile(r"^(gr|rev|qa)-\d+-")


def backup(name: str, stamp: str) -> None:
    path = api.state_dir() / name
    if path.exists():
        shutil.copy2(path, path.with_name(f"{path.stem}.before-demo-{stamp}.json"))


def drop_old_cards(stamp: str) -> int:
    removed = 0
    for name, key in (
        ("findings.json", "id"),
        ("decisions.json", "finding_id"),
        ("impacts.json", "finding_id"),
    ):
        raw = api.read_json(name, [])
        if not isinstance(raw, list):
            continue
        keep = [e for e in raw if not (isinstance(e, dict) and OLD_IDS.match(str(e.get(key))))]
        if len(keep) != len(raw):
            backup(name, stamp)
            removed += len(raw) - len(keep)
            api.write_json(name, keep)
    return removed


def reset(stamp: str) -> None:
    for name, empty in RESET_FILES.items():
        backup(name, stamp)
        api.write_json(name, empty)
    backup("routines.json", stamp)
    routines = api.read_list("routines.json")
    for r in routines:
        r["enabled"] = False
    api.write_json("routines.json", routines)
    print(f"removed {drop_old_cards(stamp)} hand-written entries", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    ap.add_argument("--reset", action="store_true", help="clear decisions, actions and routines")
    ap.add_argument("--no-llm", action="store_true", help="deterministic extraction only")
    args = ap.parse_args(argv)
    if api.portal_root() is None:
        print("No portal data found under data/ (set ASK_PORTAL_DIR).", file=sys.stderr)
        return 1
    if args.reset:
        reset(api.now().strftime("%Y%m%d-%H%M%S"))
    cards = papercheck.run(write=True, llm=False if args.no_llm else None)
    for f, d, _ in cards:
        print(f"{f['id']}: {d['headline']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
