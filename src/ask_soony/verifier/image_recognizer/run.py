"""
Discovery run: read every document with one OpenAI vision call each and keep what it produced.

Per document, in <out>/<path>__<sha12>/:
  verdict.json     structured result (claims + verdict), see schema.py
  meta.json        path, checksum, model, time, tokens, status, error

Scans and photos are sent as images; PDFs as their text plus images of pages without a text
layer (shared ask_soony.extract). The answer is forced into the JSON schema, so there is no
agent loop and the model gets no tools: documents are untrusted input.

Results are keyed by file checksum and a fingerprint of schema, prompts, model and effort: a
re-run skips documents already read, and a changed file or a changed schema means read again.
File versions the frozen baseline already holds (verifier/baseline.py) are never read again, so
a new model or prompt only costs calls for new drops and never overwrites the baseline.
Interrupt any time; the next run continues where this one stopped.
"""

import asyncio
import base64
import dataclasses
import hashlib
import json
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from openai import AsyncOpenAI
from tqdm import tqdm

from ask_soony.extract import extract
from ask_soony.verifier.baseline import BASELINE_DIR, baseline_keys, ignored_paths
from ask_soony.verifier.image_recognizer.prompt import SYSTEM_PROMPT, USER_PROMPT
from ask_soony.verifier.image_recognizer.schema import VERDICT_SCHEMA

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
DOC_SUFFIXES = IMAGE_SUFFIXES | {".pdf"}
RETRY_WAIT_SECONDS = 30
# OpenAI reasoning effort has no xhigh/max: cap at high.
EFFORT = {"low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high"}


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
    retries: int
    baseline: Path | None = BASELINE_DIR  # file versions read here are skipped; None: read all


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
            "schema": VERDICT_SCHEMA,
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


def _content(job: Job) -> list[dict]:
    """The user message: instructions, any text layer, then the page images."""
    doc = extract(job.rel, job.path.read_bytes())
    text = USER_PROMPT.format(path=job.rel, suffix=job.path.suffix.lower())
    if doc.text.strip():
        text += f"\n\nText layer of the document:\n{doc.text}"
    parts = [{"type": "text", "text": text}]
    for img in doc.images:
        b64 = base64.standard_b64encode(img.data).decode("ascii")
        url = f"data:{img.media_type};base64,{b64}"
        parts.append({"type": "image_url", "image_url": {"url": url, "detail": "high"}})
    return parts


async def read_document(job: Job, settings: Settings, client: AsyncOpenAI) -> dict:
    """One attempt: one model call on one document. Returns what to save."""
    response = await client.chat.completions.create(
        model=settings.model,
        reasoning_effort=EFFORT.get(settings.effort, "medium"),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _content(job)},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "document_reading", "schema": VERDICT_SCHEMA, "strict": True},
        },
    )
    choice = response.choices[0]
    usage = response.usage.model_dump() if response.usage else None
    if choice.message.refusal:
        return {"error": f"refusal: {choice.message.refusal}", "usage": usage}
    try:
        verdict = json.loads(choice.message.content or "")
    except json.JSONDecodeError:
        return {"error": "no structured output", "usage": usage}
    return {"verdict": verdict, "usage": usage, "error": None}


def _tokens(run: dict) -> int:
    return (run.get("usage") or {}).get("total_tokens") or 0


# A key or permission problem says nothing about the document: it does not count as a failure
AUTH_ERRORS = ("AuthenticationError", "PermissionDeniedError")


def failures(folder: Path) -> tuple[int, str | None]:
    """How often this file version failed to read in a row (across runs), and the last error."""
    try:
        meta = json.loads((folder / "meta.json").read_text())
    except (OSError, json.JSONDecodeError):
        return 0, None
    return int(meta.get("failures") or 0), meta.get("error")


def _save(job: Job, settings: Settings, run: dict, started: float, attempts: int) -> dict:
    folder = settings.out / job.folder
    folder.mkdir(parents=True, exist_ok=True)
    error = run.get("error")
    before, _ = failures(folder)
    if not error:
        failed = 0
    elif any(marker in error for marker in AUTH_ERRORS):
        failed = before
    else:
        failed = before + 1
    meta = {
        "path": job.rel,
        "sha256": job.sha256,
        "model": settings.model,
        "effort": settings.effort,
        "fingerprint": fingerprint(settings),
        "status": "error" if run.get("error") else "ok",
        "error": run.get("error"),
        "attempts": attempts,
        "failures": failed,  # failed runs in a row; live reading gives up at MAX_FAILURES
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "seconds": round(time.time() - started, 1),
        "usage": run.get("usage"),
    }
    if run.get("verdict") is not None:
        (folder / "verdict.json").write_text(
            json.dumps(run["verdict"], indent=2, ensure_ascii=False)
        )
    (folder / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    verdict = run.get("verdict") or {}
    return {
        **{k: meta[k] for k in ("path", "status", "error", "seconds")},
        "tokens": _tokens(run),
        "document_type": verdict.get("document_type"),
        "verdict": (verdict.get("verdict") or {}).get("status"),
        "red_flags": (verdict.get("verdict") or {}).get("red_flags", []),
        "folder": job.folder,
    }


# A refusal or a schema problem gives the same answer on every attempt: don't wait for it
NOT_RETRYABLE = ("refusal", "no structured output")


def _not_retryable(error: str) -> bool:
    return any(marker in error for marker in NOT_RETRYABLE)


async def process(
    job: Job, settings: Settings, slots: asyncio.Semaphore, client: AsyncOpenAI | None = None
) -> dict:
    """Read one document with retries. A slot is held only while reading, not while waiting."""
    started, run, attempts = time.time(), {}, 0
    for attempt in range(1, settings.retries + 2):
        attempts = attempt
        async with slots:
            try:
                run = await read_document(job, settings, client)
            except Exception as error:  # API/network/file errors: retry, then record
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
        "total_tokens": sum(r.get("tokens") or 0 for r in latest.values()),
        "errors": {r["path"]: r["error"] for r in latest.values() if r.get("status") == "error"},
    }
    (out / "run_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


async def run_all(jobs: list[Job], settings: Settings, force: bool) -> dict:
    settings.out.mkdir(parents=True, exist_ok=True)
    index_path = settings.out / "index.jsonl"
    current = fingerprint(settings)
    frozen = baseline_keys(settings.baseline)
    in_baseline = [j for j in jobs if (j.rel, j.sha256) in frozen]
    if in_baseline:
        tqdm.write(f"{len(in_baseline)} documents already in the baseline: skipped")
    jobs = [j for j in jobs if (j.rel, j.sha256) not in frozen]
    ignored = ignored_paths()
    if any(j.rel in ignored for j in jobs):
        tqdm.write(f"{sum(j.rel in ignored for j in jobs)} documents ignored by decision: skipped")
    jobs = [j for j in jobs if j.rel not in ignored]
    todo = [j for j in jobs if force or not is_done(j, settings.out, current)]
    skipped = len(jobs) - len(todo)
    if skipped:
        tqdm.write(f"{skipped} documents already read (same file, schema and prompt): skipped")
    if not todo:
        return _summarise(index_path, settings.out) if index_path.exists() else {}
    client = AsyncOpenAI(max_retries=2)  # reads OPENAI_API_KEY
    semaphore = asyncio.Semaphore(settings.concurrency)
    bar = tqdm(total=len(todo), unit="doc", desc="reading")
    stats = {"ok": 0, "error": 0, "tokens": 0}

    async def worker(job: Job) -> None:
        row = await process(job, settings, semaphore, client)
        with index_path.open("a") as index:
            index.write(json.dumps(row, ensure_ascii=False) + "\n")
        stats[row["status"]] += 1
        stats["tokens"] += row.get("tokens") or 0
        if row["status"] == "error":
            tqdm.write(f"✗ {job.rel}: {row['error']}")
        bar.set_postfix(ok=stats["ok"], err=stats["error"], tokens=stats["tokens"])
        bar.update(1)

    await asyncio.gather(*(worker(j) for j in todo))
    bar.close()
    return _summarise(index_path, settings.out) if index_path.exists() else {}
