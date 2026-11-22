"""
Gate: may this reader (model + prompt) be trusted? Reads only the golden documents and scores them.

uv run python -m ask_soony.verifier.image_recognizer.gate --model gpt-5.4-mini   # ~8 calls
uv run python -m ask_soony.verifier.image_recognizer.gate \
    --readings src/ask_soony/verifier/baseline_sonnet                           # free

Readings go to a scratch folder per model and effort (data/store/gate/), never into the live store
or the baseline. Exit code 0 only if every must-catch case passes.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from ask_soony.config import load_dotenv
from ask_soony.verifier.image_recognizer.golden import load_cases, report, score_store
from ask_soony.verifier.image_recognizer.run import Settings, run_all, select_jobs


def main() -> int:
    parser = argparse.ArgumentParser(prog="ask_soony.verifier.image_recognizer.gate")
    parser.add_argument("--readings", type=Path, help="score this store; no model calls")
    parser.add_argument("--root", type=Path, default=Path("data/portal"))
    parser.add_argument("--model", default="gpt-5.4-mini")
    parser.add_argument(
        "--effort", default="medium", choices=["low", "medium", "high", "xhigh", "max"]
    )
    parser.add_argument("--out", type=Path, help="default: data/store/gate/<model>_<effort>")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--force", action="store_true", help="read again even if already read")
    args = parser.parse_args()
    cases = load_cases()

    store = args.readings
    if store is None:
        load_dotenv()
        root = args.root.resolve()
        store = (args.out or Path("data/store/gate") / f"{args.model}_{args.effort}").resolve()
        wanted = {c["path"]: c for c in cases}
        jobs = [j for j in select_jobs(root, None, False, None) if j.rel in wanted]
        for missing in sorted(set(wanted) - {j.rel for j in jobs}):
            print(f"not in the portal folder: {missing}", file=sys.stderr)
        for job in jobs:
            if job.sha256 != wanted[job.rel].get("sha256"):
                print(f"file changed since the case was written: {job.rel}", file=sys.stderr)
        print(f"{len(jobs)} golden documents → {store}  (model {args.model}, {args.effort})")
        settings = Settings(root, store, args.model, args.effort, args.concurrency, 2, None)
        asyncio.run(run_all(jobs, settings, args.force))

    text, trusted = report(score_store(store, cases))
    print(text)
    return 0 if trusted else 1


if __name__ == "__main__":
    sys.exit(main())
