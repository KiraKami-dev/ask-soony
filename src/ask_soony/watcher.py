"""
The deterministic loop: poll the portal, store new versions, analyse what changed, notify.
No LLM decides when or what to fetch; the model only reads and judges.
"""

import hashlib
import io
import sys
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

from ask_soony.agent import Analyst
from ask_soony.changes import summarize
from ask_soony.notify import Event, Fanout
from ask_soony.portal import PortalClient, RemoteFile
from ask_soony.store import PollResult, Store, severity_at_least

# Above this many missing files, one archive download beats single requests.
ARCHIVE_THRESHOLD = 20


def poll(store: Store, portal: PortalClient, analyze_baseline: bool = False) -> PollResult:
    listing = portal.list_files()
    missing = [f for f in listing if not store.has_blob(f.sha256)]
    if len(missing) > ARCHIVE_THRESHOLD:
        _fill_from_archive(store, portal, {f.sha256 for f in missing})
    for f in listing:
        if not store.has_blob(f.sha256):
            store.put_blob(f.sha256, portal.download(f.path))
    result = store.record_listing(listing, analyze_baseline=analyze_baseline)
    summarize_changes(store)
    return result


def import_snapshot(store: Store, source: Path, at: str) -> PollResult:
    """
    Record an earlier portal state as the baseline: a portal archive.zip (files under one
    top-level folder) or a folder with the portal's paths. Only for an empty store.
    """
    if not store.is_empty():
        raise ValueError("import-snapshot only seeds an empty store")
    files: dict[str, bytes] = {}
    if source.suffix == ".zip":
        with zipfile.ZipFile(source) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            prefix = names[0].split("/")[0] + "/" if names and all("/" in n for n in names) else ""
            if prefix and not all(n.startswith(prefix) for n in names):
                prefix = ""
            for n in names:
                files[n[len(prefix) :]] = z.read(n)
    else:
        for f in sorted(source.rglob("*")):
            if f.is_file():
                files[f.relative_to(source).as_posix()] = f.read_bytes()
    listing = []
    for path, data in files.items():
        digest = hashlib.sha256(data).hexdigest()
        store.put_blob(digest, data)
        listing.append(RemoteFile(path=path, sha256=digest, size=len(data)))
    return store.record_listing(listing, at=at)


def summarize_changes(store: Store) -> int:
    """Compute a 'what changed' summary for every new or changed version that has none yet."""
    done = 0
    for version in store.versions_without_change():
        prev = store.previous_version(version)
        old = store.read_blob(prev.sha256) if prev else None
        summary, diff = summarize(version.path, old, store.read_blob(version.sha256))
        store.put_change(version.id, summary, diff)
        done += 1
    return done


def _fill_from_archive(store: Store, portal: PortalClient, wanted: set[str]) -> None:
    """Store every archive member whose checksum the listing asked for; ignore the rest."""
    with zipfile.ZipFile(io.BytesIO(portal.download_archive())) as z:
        for name in z.namelist():
            if name.endswith("/"):
                continue
            data = z.read(name)
            digest = hashlib.sha256(data).hexdigest()
            if digest in wanted:
                store.put_blob(digest, data)


def mirror(store: Store, dest: Path) -> int:
    """Write every current portal file to dest under its portal path. Returns the file count."""
    root = dest.resolve()
    count = 0
    for path, version in store.current().items():
        target = (root / path).resolve()
        if not target.is_relative_to(root):  # never write outside dest, whatever the portal sends
            raise ValueError(f"refusing unsafe portal path {path!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        data = store.read_blob(version.sha256)
        if not target.exists() or target.read_bytes() != data:
            target.write_bytes(data)
        count += 1
    return count


def describe_poll(result: PollResult) -> Event | None:
    if result.baseline:
        n = result.stored
        return Event(
            "changes",
            "Baseline stored",
            f"Stored the current portal state as the baseline ({n} versions).",
        )
    if not result.has_changes:
        return None
    lines = [f"+ {v.path}" for v in result.new]
    lines += [f"~ {v.path}" for v in result.changed]
    lines += [f"- {p}" for p in result.removed]
    title = f"{len(result.new)} new, {len(result.changed)} changed, {len(result.removed)} removed"
    return Event(
        "changes", title, "\n".join(lines), severity="medium", data={"poll_id": result.poll_id}
    )


def analyze_pending(
    store: Store, analyst: Analyst, notify: Fanout, min_severity: str, limit: int | None = None
) -> int:
    done = 0
    for version in store.pending(limit):
        try:
            outcome = analyst.analyze(version)
        except Exception as e:
            store.set_status(version.id, "error")
            notify.send(Event("error", f"Analysis failed: {version.path}", str(e)[:500], "low"))
            continue
        done += 1
        if outcome.result is None:
            notify.send(
                Event(
                    "error",
                    f"No analysis submitted: {version.path}",
                    f"stop reason {outcome.stop_reason}",
                    "low",
                )
            )
            continue
        print(
            f"analysed {version.path}: {len(outcome.result.get('findings') or [])} findings, "
            f"{outcome.tool_calls} tool calls, {outcome.usage}",
            file=sys.stderr,
        )
        for f in store.findings(status=None, analysis_id=outcome.analysis_id):
            if severity_at_least(f["severity"], min_severity):
                evidence = "\n".join(
                    f"> {e.get('path')}: {e.get('quote')}" for e in f["evidence"][:3]
                )
                body = f"{f['detail']}\n\nAction: {f['suggested_action']}\n\n{evidence}"
                notify.send(
                    Event("finding", f"#{f['id']} {f['title']}", body, f["severity"], data=f)
                )
    return done


def watch(
    store: Store,
    portal: PortalClient,
    analyst: Analyst | None,
    notify: Fanout,
    interval: int,
    min_severity: str,
    analyze_baseline: bool = False,
    on_change: Callable[[list[str]], None] | None = None,
) -> None:
    while True:
        try:
            result = poll(store, portal, analyze_baseline=analyze_baseline)
            event = describe_poll(result)
            if event:
                notify.send(event)
            changed = [v.path for v in result.new] + [v.path for v in result.changed]
            if changed and on_change:
                on_change(changed)
            if analyst:
                analyze_pending(store, analyst, notify, min_severity)
        except KeyboardInterrupt:
            raise
        except Exception as e:  # keep watching through network hiccups
            notify.send(Event("error", "Poll failed", str(e)[:500], "low"))
        time.sleep(interval)
