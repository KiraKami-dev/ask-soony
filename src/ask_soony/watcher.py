"""
The deterministic loop: poll the portal, store new versions, analyse what changed, notify.
No LLM decides when or what to fetch; the model only reads and judges.
"""

import hashlib
import io
import sys
import time
import zipfile

from ask_soony.agent import Analyst
from ask_soony.notify import Event, Fanout
from ask_soony.portal import PortalClient
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
    return store.record_listing(listing, analyze_baseline=analyze_baseline)


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
) -> None:
    while True:
        try:
            result = poll(store, portal, analyze_baseline=analyze_baseline)
            event = describe_poll(result)
            if event:
                notify.send(event)
            if analyst:
                analyze_pending(store, analyst, notify, min_severity)
        except KeyboardInterrupt:
            raise
        except Exception as e:  # keep watching through network hiccups
            notify.send(Event("error", "Poll failed", str(e)[:500], "low"))
        time.sleep(interval)
