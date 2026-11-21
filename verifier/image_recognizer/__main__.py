"""
uv run python -m verifier.image_recognizer                     # all documents, resumable
uv run python -m verifier.image_recognizer --only WZ-261116-473 # one document
uv run python -m verifier.image_recognizer --images-only --limit 5

Uses the Claude Agent SDK, so it runs on your Claude Code login: no API key needed.
"""

import argparse
import asyncio
import json
from pathlib import Path

from verifier.image_recognizer.run import Settings, run_all, select_jobs


def main() -> None:
    parser = argparse.ArgumentParser(prog="verifier.image_recognizer")
    parser.add_argument("--root", type=Path, default=Path("data/portal"))
    parser.add_argument("--out", type=Path, default=Path("data/store/image_recognizer"))
    parser.add_argument("--model", default="claude-sonnet-5-5")
    parser.add_argument(
        "--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"]
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--max-turns", type=int, default=6)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--only", help="only paths containing this text")
    parser.add_argument("--images-only", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--force", action="store_true", help="read again even if already read")
    args = parser.parse_args()

    root = args.root.resolve()
    settings = Settings(
        root=root,
        out=args.out.resolve(),
        model=args.model,
        effort=args.effort,
        concurrency=args.concurrency,
        max_turns=args.max_turns,
        retries=args.retries,
    )
    jobs = select_jobs(root, args.only, args.images_only, args.limit)
    print(f"{len(jobs)} documents under {root} → {settings.out}  (model {args.model})")
    summary = asyncio.run(run_all(jobs, settings, args.force))
    print(json.dumps({k: v for k, v in summary.items() if k != "errors"}, indent=2))
    if summary.get("errors"):
        print(f"{len(summary['errors'])} errors: see {settings.out / 'run_summary.json'}")


if __name__ == "__main__":
    main()
