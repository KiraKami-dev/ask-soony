"""One run loop: watcher -> teammates' modules -> data/state/ -> UI.

`ask-soony run` serves the UI and, in a background thread, polls the portal, mirrors new files,
records each new or changed file as a drop event (the UI pulses and toasts it) and hands the
changed paths to the pipeline. The pipeline is whatever teammates' modules are installed: the
verifier/predictor router (`ask_soony.dispatch.refresh`) when present, nothing otherwise. A
missing module or a failing step never stops the loop or the UI.

`ask-soony drop PATH` does the same for one file by hand, for rehearsals: it announces a portal
file (optionally copied in from a local file first) and runs the pipeline on it.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
from datetime import datetime
from pathlib import Path

from ask_soony.api import portal_root, read_json, read_list, state_dir, tz, write_json

MAX_EVENTS = 200


def mirror_dir() -> Path:
    """Where portal files live on disk: ASK_PORTAL_DIR, an existing mirror, or data/portal."""
    if os.environ.get("ASK_PORTAL_DIR"):
        return Path(os.environ["ASK_PORTAL_DIR"])
    return portal_root() or Path("data/portal")


def record_events(paths: list[str], kind: str = "file") -> list[dict]:
    stamp = datetime.now(tz()).isoformat()
    new = [{"at": stamp, "path": p, "kind": kind} for p in paths]
    if new:
        write_json("events.json", (new + read_list("events.json"))[:MAX_EVENTS])
    return new


def mark_checked(**extra) -> None:
    status = read_json("status.json", {})
    status.update(last_check_at=datetime.now(tz()).isoformat(), **extra)
    write_json("status.json", status)


def run_pipeline(paths: list[str], root: Path) -> str:
    """Route a drop through verification, prediction, dispatch and internal escalation."""
    from ask_soony.alerts import AlertBook, Policy
    from ask_soony.config import Settings
    from ask_soony.dispatch import refresh
    from ask_soony.dispatch.alerts import sync
    from ask_soony.escalation import Escalator, Mailer, make_caller
    from ask_soony.llm import make_backend
    from ask_soony.predictor import ImpactAgent
    from ask_soony.store import Store

    settings = Settings.from_env()
    predictor = None
    if settings.llm_provider != "none":
        backend = make_backend(
            settings.llm_provider, settings.llm_model, settings.llm_api_key,
            settings.llm_base_url, settings.llm_effort,
        )
        predictor = ImpactAgent(Store(settings.data_dir), backend, state_dir())
    dispatch_file = state_dir() / "dispatch.json"
    routed = refresh(paths, root, settings.data_dir / "image_recognizer", dispatch_file, predictor)
    store = Store(settings.data_dir)
    try:
        policy = Policy.load(settings.alert_policy)
        book = AlertBook(store)
        escalator = Escalator(
            book,
            policy,
            make_caller(settings.call_provider),
            Mailer.from_env(),
            settings.public_url,
        )
        alert_ids = sync(
            dispatch_file, state_dir() / "dispatch_alerts.json", book, policy, escalator
        )
    finally:
        store.db.close()
    return (
        f"pipeline: verifier={routed.verifier} predictor={routed.predictor} "
        f"alerts={len(alert_ids)}"
    )


def poll_once(store, client, dest: Path) -> tuple[list[str], bool]:
    """Changed paths since the last poll, and whether this poll was the first (baseline)."""
    from ask_soony.watcher import mirror, poll

    result = poll(store, client)
    mirror(store, dest)
    if result.baseline:
        return sorted(store.current()), True
    return [v.path for v in result.new] + [v.path for v in result.changed], False


def loop(interval: int, stop: threading.Event | None = None) -> None:
    from ask_soony.config import Settings
    from ask_soony.portal import PortalClient
    from ask_soony.store import Store

    settings = Settings.from_env()
    dest = mirror_dir()
    if not settings.portal_token:
        print("run: SOONY_PORTAL_TOKEN not set; serving the files already on disk", file=sys.stderr)
        return
    store = Store(settings.data_dir)
    client = PortalClient(settings.portal_url, settings.portal_token)
    stop = stop or threading.Event()
    while not stop.is_set():
        try:
            paths, baseline = poll_once(store, client, dest)
            if paths:
                if not baseline:  # the first sync is not a drop: no toast for every file
                    record_events(paths)
                what = "files (baseline)" if baseline else "new or changed files"
                print(f"run: {len(paths)} {what}", file=sys.stderr)
                print(run_pipeline(paths, dest), file=sys.stderr)
            if baseline:
                mark_checked(files_read_since_06=len(paths))
            else:
                mark_checked()
        except Exception as e:  # network hiccups: keep watching
            print(f"run: poll failed: {e}", file=sys.stderr)
        stop.wait(interval)


def escalation_loop(interval: int = 30, stop: threading.Event | None = None) -> None:
    """Continue policy escalation for dispatch alerts that have not been answered."""
    from ask_soony.alerts import AlertBook, Policy
    from ask_soony.config import Settings
    from ask_soony.escalation import Escalator, Mailer, make_caller
    from ask_soony.store import Store

    settings = Settings.from_env()
    stop = stop or threading.Event()
    while not stop.is_set():
        store = Store(settings.data_dir)
        try:
            policy = Policy.load(settings.alert_policy)
            Escalator(
                AlertBook(store), policy, make_caller(settings.call_provider),
                Mailer.from_env(), settings.public_url,
            ).tick()
        except Exception as exc:
            print(f"escalation failed: {exc}", file=sys.stderr)
        finally:
            store.db.close()
        stop.wait(interval)


def serve_with_loop(host: str, port: int, interval: int) -> None:
    import uvicorn

    threading.Thread(target=loop, args=(interval,), daemon=True, name="watcher").start()
    threading.Thread(target=escalation_loop, daemon=True, name="escalation").start()
    uvicorn.run("ask_soony.api:app", host=host, port=port)


def drop(path: str, source: Path | None = None) -> dict:
    """Announce one portal file as a new drop (copying it in first if given) and process it."""
    root = mirror_dir()
    target = (root / path).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError(f"refusing path outside the portal mirror: {path!r}")
    if source:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    elif not target.is_file():
        raise FileNotFoundError(f"{path} is not in {root}; pass --from to copy a file in")
    else:
        target.touch()
    event = record_events([path], kind="drop")[0]
    outcome = run_pipeline([path], root)
    mark_checked()
    return {"event": event, "pipeline": outcome}

