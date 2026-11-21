"""
Discovery run: read every document with Claude (Agent SDK) and keep everything it produced.

Per document, in <out>/<path>__<sha12>/:
  verdict.json     structured result (claims + verdict), see schema.py
  thinking.json    the model's thinking blocks (summarized; raw reasoning is never returned)
  transcript.json  every message: thinking, tool calls, tool results (image bytes left out), text
  meta.json        path, checksum, model, time, turns, tokens, cost, status, error

Results are keyed by file checksum and a fingerprint of schema, prompts, model and effort: a
re-run skips documents already read, and a changed file or a changed schema means read again.
Interrupt any time; the next run continues where this one stopped.
"""

import asyncio
import dataclasses
import hashlib
import json
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    ThinkingBlock,
    query,
)
from tqdm import tqdm

from verifier.image_recognizer.prompt import SYSTEM_PROMPT, USER_PROMPT
from verifier.image_recognizer.schema import OUTPUT_FORMAT

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
DOC_SUFFIXES = IMAGE_SUFFIXES | {".pdf"}
MAX_TEXT_CHARS = 20_000  # per string in the transcript; base64 image data is dropped entirely
RETRY_WAIT_SECONDS = 30


@dataclasses.dataclass
class Job:
    path: Path  # absolute
    rel: str  # relative to the portal root
    sha256: str

    @property
    def folder(self) -> str:
        return f"{self.rel.replace('/', '__')}__{self.sha256[:12]}"


@dataclasses.dataclass
class Settings:
    root: Path
    out: Path
    model: str
    effort: str
    concurrency: int
    max_turns: int
    retries: int


def select_jobs(root: Path, only: str | None, images_only: bool, limit: int | None) -> list[Job]:
    """Every document under the portal root; scans and photos first."""
    suffixes = IMAGE_SUFFIXES if images_only else DOC_SUFFIXES
    files = [
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in suffixes and not p.name.startswith(".")
    ]
    if only:
        files = [p for p in files if only in p.relative_to(root).as_posix()]
    files.sort(key=lambda p: (p.suffix.lower() not in IMAGE_SUFFIXES, p.as_posix()))
    jobs = [
        Job(p, p.relative_to(root).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest())
        for p in files
    ]
    return jobs[:limit] if limit else jobs


def fingerprint(settings: "Settings") -> str:
    """What produced a result: schema, prompts, model, effort. A change means read again."""
    basis = json.dumps(
        {
            "schema": OUTPUT_FORMAT,
            "system": SYSTEM_PROMPT,
            "user": USER_PROMPT,
            "model": settings.model,
            "effort": settings.effort,
        },
        sort_keys=True,
    )
    return hashlib.sha256(basis.encode()).hexdigest()[:16]


def is_done(job: Job, out: Path, run_fingerprint: str) -> bool:
    meta = out / job.folder / "meta.json"
    if not meta.exists():
        return False
    try:
        data = json.loads(meta.read_text())
    except json.JSONDecodeError:
        return False
    return (
        data.get("status") == "ok"
        and data.get("sha256") == job.sha256
        and data.get("fingerprint") == run_fingerprint
    )


def _clean(value):
    """JSON-safe copy of a message: image bytes dropped, very long strings cut."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        # Field by field (not asdict) so nested blocks keep their type name too
        fields = {f.name: _clean(getattr(value, f.name)) for f in dataclasses.fields(value)}
        return {"_type": type(value).__name__, **fields}
    if isinstance(value, dict):
        out = {}
        for key, inner in value.items():
            if key == "data" and isinstance(inner, str) and len(inner) > 200:
                out[key] = f"<{len(inner)} chars of encoded data left out>"
            else:
                out[key] = _clean(inner)
        return out
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, str) and len(value) > MAX_TEXT_CHARS:
        return value[:MAX_TEXT_CHARS] + f"… <{len(value) - MAX_TEXT_CHARS} more chars>"
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _options(settings: Settings) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        model=settings.model,
        system_prompt=SYSTEM_PROMPT,
        tools=["Read"],  # the only tool it gets: documents are untrusted input
        allowed_tools=["Read"],
        permission_mode="dontAsk",  # anything not allowed is denied, never asked
        setting_sources=[],  # isolation: no project hooks, CLAUDE.md or user settings
        skills=[],
        cwd=str(settings.root),
        max_turns=settings.max_turns,
        thinking={"type": "adaptive", "display": "summarized"},
        effort=settings.effort,
        output_format=OUTPUT_FORMAT,
    )


async def read_document(job: Job, settings: Settings) -> dict:
    """One attempt: run the model on one document. Returns what to save."""
    prompt = USER_PROMPT.format(path=str(job.path), suffix=job.path.suffix.lower())
    transcript, thinking, result = [], [], None
    async for message in query(prompt=prompt, options=_options(settings)):
        transcript.append(_clean(message))
        if isinstance(message, AssistantMessage):
            thinking += [
                {"turn": len(transcript), "thinking": b.thinking}
                for b in message.content
                if isinstance(b, ThinkingBlock)
            ]
        elif isinstance(message, ResultMessage):
            result = message
    verdict = result.structured_output if result else None
    if verdict is None and result and result.result:
        try:  # fallback: the final text may itself be the JSON
            verdict = json.loads(result.result)
        except json.JSONDecodeError:
            pass
    error = None
    if result is None:
        error = "no result message"
    elif result.is_error:
        error = f"{result.subtype}: {result.errors or result.result}"
    elif verdict is None:
        error = "no structured output"
    return {
        "verdict": verdict,
        "thinking": thinking,
        "transcript": transcript,
        "result": result,
        "error": error,
    }


def _save(job: Job, settings: Settings, run: dict, started: float, attempts: int) -> dict:
    folder = settings.out / job.folder
    folder.mkdir(parents=True, exist_ok=True)
    result = run.get("result")
    meta = {
        "path": job.rel,
        "sha256": job.sha256,
        "model": settings.model,
        "effort": settings.effort,
        "fingerprint": fingerprint(settings),
        "status": "error" if run.get("error") else "ok",
        "error": run.get("error"),
        "attempts": attempts,
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "seconds": round(time.time() - started, 1),
        "num_turns": getattr(result, "num_turns", None),
        "cost_usd": getattr(result, "total_cost_usd", None),
        "usage": _clean(getattr(result, "usage", None)),
    }
    if run.get("verdict") is not None:
        (folder / "verdict.json").write_text(
            json.dumps(run["verdict"], indent=2, ensure_ascii=False)
        )
    (folder / "thinking.json").write_text(
        json.dumps(run.get("thinking", []), indent=2, ensure_ascii=False)
    )
    (folder / "transcript.json").write_text(
        json.dumps(run.get("transcript", []), indent=2, ensure_ascii=False)
    )
    (folder / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    verdict = run.get("verdict") or {}
    return {
        **{k: meta[k] for k in ("path", "status", "error", "seconds", "cost_usd")},
        "document_type": verdict.get("document_type"),
        "verdict": (verdict.get("verdict") or {}).get("status"),
        "red_flags": (verdict.get("verdict") or {}).get("red_flags", []),
        "folder": job.folder,
    }


# A safety block or a schema problem gives the same answer on every attempt: don't wait for it
NOT_RETRYABLE = ("safeguards", "refusal", "no structured output")


def _not_retryable(error: str) -> bool:
    return any(marker in error for marker in NOT_RETRYABLE)


async def process(job: Job, settings: Settings, slots: asyncio.Semaphore) -> dict:
    """Read one document with retries. A slot is held only while reading, not while waiting."""
    started, run, attempts = time.time(), {}, 0
    for attempt in range(1, settings.retries + 2):
        attempts = attempt
        async with slots:
            try:
                run = await read_document(job, settings)
            except Exception as error:  # SDK/CLI/process errors: retry, then record
                run = {"error": f"{type(error).__name__}: {error}"}
        if not run.get("error") or _not_retryable(run["error"]):
            break
        if attempt <= settings.retries:
            await asyncio.sleep(RETRY_WAIT_SECONDS * attempt)
    return _save(job, settings, run, started, attempts)


def _summarise(index_path: Path, out: Path) -> dict:
    rows = [json.loads(line) for line in index_path.read_text().splitlines() if line.strip()]
    latest = {row["path"]: row for row in rows}  # last attempt per document wins

    def count(key: str) -> dict:
        return dict(Counter(str(r.get(key)) for r in latest.values()).most_common())

    summary = {
        "documents": len(latest),
        "by_status": count("status"),
        "by_document_type": count("document_type"),
        "by_verdict": count("verdict"),
        "with_red_flags": sum(1 for r in latest.values() if r.get("red_flags")),
        "total_cost_usd": round(sum(r.get("cost_usd") or 0 for r in latest.values()), 4),
        "errors": {r["path"]: r["error"] for r in latest.values() if r.get("status") == "error"},
    }
    (out / "run_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


async def run_all(jobs: list[Job], settings: Settings, force: bool) -> dict:
    settings.out.mkdir(parents=True, exist_ok=True)
    index_path = settings.out / "index.jsonl"
    current = fingerprint(settings)
    todo = [j for j in jobs if force or not is_done(j, settings.out, current)]
    skipped = len(jobs) - len(todo)
    if skipped:
        tqdm.write(f"{skipped} documents already read (same file, schema and prompt): skipped")
    semaphore = asyncio.Semaphore(settings.concurrency)
    bar = tqdm(total=len(todo), unit="doc", desc="reading")
    stats = {"ok": 0, "error": 0, "cost": 0.0}

    async def worker(job: Job) -> None:
        row = await process(job, settings, semaphore)
        with index_path.open("a") as index:
            index.write(json.dumps(row, ensure_ascii=False) + "\n")
        stats[row["status"]] += 1
        stats["cost"] += row.get("cost_usd") or 0
        if row["status"] == "error":
            tqdm.write(f"✗ {job.rel}: {row['error']}")
        bar.set_postfix(ok=stats["ok"], err=stats["error"], cost=f"${stats['cost']:.2f}")
        bar.update(1)

    await asyncio.gather(*(worker(j) for j in todo))
    bar.close()
    return _summarise(index_path, settings.out) if index_path.exists() else {}
