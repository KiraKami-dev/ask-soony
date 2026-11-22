"""
uv run python -m ask_soony.verifier.image_recognizer                     # all documents, resumable
uv run python -m ask_soony.verifier.image_recognizer --only WZ-261116-473 # one document
uv run python -m ask_soony.verifier.image_recognizer --images-only --limit 5

One OpenAI call per document (OPENAI_API_KEY from .env).
"""

import argparse
import asyncio
import json
from pathlib import Path

from ask_soony.config import load_dotenv
from ask_soony.verifier.baseline import BASELINE_DIR
from ask_soony.verifier.image_recognizer.run import Settings, run_all, select_jobs


def main() -> None:
    parser = argparse.ArgumentParser(prog="ask_soony.verifier.image_recognizer")
    parser.add_argument("--root", type=Path, default=Path("data/portal"))
    parser.add_argument("--out", type=Path, default=Path("data/store/image_recognizer"))
    parser.add_argument("--model", default="gpt-5.4-mini")
    parser.add_argument(
        "--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"]
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--only", help="only paths containing this text")
    parser.add_argument("--images-only", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--force", action="store_true", help="read again even if already read")
    parser.add_argument(
        "--ignore-baseline",
        action="store_true",
        help="also read file versions the frozen baseline already has (costs calls)",
    )
    args = parser.parse_args()
    load_dotenv()

    root = args.root.resolve()
    settings = Settings(
        root=root,
        out=args.out.resolve(),
        model=args.model,
        effort=args.effort,
        concurrency=args.concurrency,
        retries=args.retries,
        baseline=None if args.ignore_baseline else BASELINE_DIR,
    )
    jobs = select_jobs(root, args.only, args.images_only, args.limit)
    print(f"{len(jobs)} documents under {root} → {settings.out}  (model {args.model})")
    summary = asyncio.run(run_all(jobs, settings, args.force))
    print(json.dumps({k: v for k, v in summary.items() if k != "errors"}, indent=2))
    if summary.get("errors"):
        print(f"{len(summary['errors'])} errors: see {settings.out / 'run_summary.json'}")


if __name__ == "__main__":
    main()
