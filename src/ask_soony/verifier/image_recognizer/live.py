"""
Live reading: before the verifier links a drop, read every document it has no reading for yet.

Called by dispatch.refresh on every portal change. Reads only file versions that neither the
frozen baseline nor the live store has, and skips the ignored list. Documents among the changed
paths go first; everything else unread follows, so a read that failed on an earlier poll is
retried on the next one.

Guard rails:
- Only a reader that passed the gate runs (trusted.json, written by the gate). A new model or
  prompt has a new fingerprint and needs a gate run first.
- Never blocks the pipeline: no key, an untrusted reader or a failed read is reported in the
  result, and the verifier builds with what it has.
- At most ASK_READER_MAX_DOCS documents per refresh (default 30); the rest wait for the next one.
- A file version that failed MAX_FAILURES times in a row is not retried (listed as unread with
  the error). Key and permission errors do not count: they say nothing about the file.

Settings (environment): ASK_READER (on/off, default on), ASK_READER_MODEL (default
gpt-5.4-mini), ASK_READER_EFFORT (default medium), OPENAI_API_KEY.
"""

import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ask_soony.verifier.baseline import BASELINE_DIR, baseline_keys, ignored_paths
from ask_soony.verifier.image_recognizer.run import (
    DOC_SUFFIXES,
    Settings,
    failures,
    fingerprint,
    is_done,
    run_all,
    select_jobs,
)

TRUSTED_FILE = Path(__file__).parent / "trusted.json"
MAX_FAILURES = 3  # a file version that failed this often in a row is left for a human
OFF = ("0", "off", "false", "no")


@dataclass
class LiveResult:
    read: list[str] = field(default_factory=list)  # read now, ok
    failed: list[dict] = field(default_factory=list)  # {path, error}: retried next refresh
    unread: list[dict] = field(default_factory=list)  # {path, reason}: not attempted
    model: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def trusted_fingerprints(path: Path = TRUSTED_FILE) -> set[str]:
    try:
        return {r["fingerprint"] for r in json.loads(path.read_text(encoding="utf-8"))["readers"]}
    except (OSError, json.JSONDecodeError, KeyError):
        return set()


def record_trust(
    model: str, effort: str, reader_fingerprint: str, passed: list[str], path: Path = TRUSTED_FILE
) -> None:
    """Called by the gate when every must-catch case passed: this reader may run live."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {
            "about": "Readers (model, effort, prompt+schema fingerprint) that passed the "
            "gate. Written by the gate; the live reader only runs these.",
            "readers": [],
        }
    data["readers"] = [r for r in data["readers"] if r["fingerprint"] != reader_fingerprint]
    data["readers"].append(
        {
            "model": model,
            "effort": effort,
            "fingerprint": reader_fingerprint,
            "passed_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "cases": passed,
        }
    )
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _settings(root: Path, out: Path) -> Settings:
    return Settings(
        root=root,
        out=out,
        model=os.environ.get("ASK_READER_MODEL", "gpt-5.4-mini"),
        effort=os.environ.get("ASK_READER_EFFORT", "medium"),
        concurrency=4,
        retries=1,
        baseline=BASELINE_DIR,
    )


def read_unread(
    changed: list[str],
    root: Path,
    out: Path,
    baseline: Path | None = BASELINE_DIR,
    trusted: set[str] | None = None,
) -> LiveResult:
    """Read the documents that have no reading yet. See the module docstring."""
    result = LiveResult()
    if os.environ.get("ASK_READER", "on").lower() in OFF or not root.is_dir():
        return result
    settings = _settings(root, out)
    settings.baseline = baseline
    result.model = settings.model
    current = fingerprint(settings)
    frozen = baseline_keys(baseline)
    ignored = ignored_paths()
    jobs = [
        j
        for j in select_jobs(root, None, False, None)
        if (j.rel, j.sha256) not in frozen and j.rel not in ignored and not is_done(j, out, current)
    ]
    gave_up = []
    for job in jobs:
        count, error = failures(out / job.folder)
        if count >= MAX_FAILURES:
            gave_up.append({"path": job.rel, "reason": f"failed {count}x, not retried: {error}"})
    result.unread += gave_up
    skip = {u["path"] for u in gave_up}
    jobs = [j for j in jobs if j.rel not in skip]
    if not jobs:
        return result
    first = {p for p in changed if Path(p).suffix.lower() in DOC_SUFFIXES}
    jobs.sort(key=lambda j: j.rel not in first)

    reason = None
    if not os.environ.get("OPENAI_API_KEY"):
        reason = "no OPENAI_API_KEY"
    elif current not in (trusted if trusted is not None else trusted_fingerprints()):
        reason = f"reader {settings.model}/{settings.effort} has not passed the gate"
    if reason:
        result.unread += [{"path": j.rel, "reason": reason} for j in jobs]
        print(f"live reading: {len(jobs)} documents unread ({reason})", file=sys.stderr)
        return result

    cap = int(os.environ.get("ASK_READER_MAX_DOCS", "30"))
    now, later = jobs[:cap], jobs[cap:]
    result.unread += [{"path": j.rel, "reason": "over the cap, next refresh"} for j in later]
    try:
        asyncio.run(run_all(now, settings, force=False))
    except Exception as e:  # a broken reader must not stop the verifier
        result.failed = [{"path": j.rel, "error": f"{type(e).__name__}: {e}"} for j in now]
        return result
    for job in now:
        meta_path = out / job.folder / "meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {"status": "error", "error": "no result written"}
        if meta.get("status") == "ok":
            result.read.append(job.rel)
        else:
            result.failed.append({"path": job.rel, "error": meta.get("error")})
    print(
        f"live reading: {len(result.read)} read, {len(result.failed)} failed, "
        f"{len(result.unread)} waiting",
        file=sys.stderr,
    )
    return result
