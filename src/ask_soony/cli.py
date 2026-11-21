"""Command line: ask-soony poll | analyze | watch | findings | status."""

import argparse
import json
import sys

from ask_soony.agent import Analyst
from ask_soony.config import Settings
from ask_soony.llm import make_backend
from ask_soony.notify import build_notifiers
from ask_soony.portal import PortalClient
from ask_soony.store import Store
from ask_soony.watcher import analyze_pending, describe_poll, poll, watch


def _analyst(settings: Settings, store: Store) -> Analyst | None:
    if settings.llm_provider == "none":
        return None
    backend = make_backend(
        settings.llm_provider,
        settings.llm_model,
        settings.llm_api_key,
        settings.llm_base_url,
        settings.llm_effort,
    )
    return Analyst(store, backend)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ask-soony", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_poll = sub.add_parser("poll", help="Poll once: store new versions, report changes")
    p_poll.add_argument(
        "--analyze-baseline", action="store_true", help="Queue the first snapshot for analysis too"
    )
    p_an = sub.add_parser("analyze", help="Analyse pending versions")
    p_an.add_argument("--limit", type=int)
    p_an.add_argument("--path", help="Queue current files under this path prefix first")
    p_watch = sub.add_parser("watch", help="Poll, analyse and notify in a loop")
    p_watch.add_argument(
        "--interval", type=int, help="Seconds between polls (default ASK_INTERVAL_SECONDS)"
    )
    p_watch.add_argument("--analyze-baseline", action="store_true")
    p_f = sub.add_parser("findings", help="Print findings")
    p_f.add_argument("--all", action="store_true", help="Include resolved findings")
    p_f.add_argument("--json", action="store_true")
    sub.add_parser("status", help="Versions, pending analyses and open findings")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    store = Store(settings.data_dir)
    notify = build_notifiers(settings.notifiers, settings.discord_webhook_url, settings.webhook_url)

    if args.cmd == "poll":
        result = poll(
            store, PortalClient(settings.portal_url, settings.portal_token), args.analyze_baseline
        )
        event = describe_poll(result)
        if event:
            notify.send(event)
        else:
            print("No changes.", file=sys.stderr)
        return 0
    if args.cmd == "analyze":
        analyst = _analyst(settings, store)
        if analyst is None:
            print("ASK_LLM_PROVIDER=none: nothing to analyse with.", file=sys.stderr)
            return 1
        if args.path:
            print(f"Queued {store.mark_pending(args.path)} versions.", file=sys.stderr)
        n = analyze_pending(store, analyst, notify, settings.notify_min_severity, args.limit)
        print(f"Analysed {n} versions.", file=sys.stderr)
        return 0
    if args.cmd == "watch":
        watch(
            store,
            PortalClient(settings.portal_url, settings.portal_token),
            _analyst(settings, store),
            notify,
            args.interval or settings.interval_seconds,
            settings.notify_min_severity,
            args.analyze_baseline,
        )
        return 0
    if args.cmd == "findings":
        items = store.findings(None if args.all else "open")
        if args.json:
            print(json.dumps(items, ensure_ascii=False, indent=2))
            return 0
        for f in items:
            print(f"#{f['id']} [{f['severity']}] {f['title']}  ({f['status']}, from {f['path']})")
            print(f"    {f['detail']}")
            if f["suggested_action"]:
                print(f"    Action: {f['suggested_action']}")
        if not items:
            print("No findings.")
        return 0
    if args.cmd == "status":
        q = store.db.execute
        print(f"current files:   {len(store.current())}")
        print(f"versions stored: {q('SELECT COUNT(*) FROM versions').fetchone()[0]}")
        for row in q("SELECT analysis_status, COUNT(*) n FROM versions GROUP BY 1").fetchall():
            print(f"  {row[0]:8} {row[1]}")
        print(f"polls:           {q('SELECT COUNT(*) FROM polls').fetchone()[0]}")
        print(f"open findings:   {len(store.findings('open'))}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
