"""Command line for the portal watcher, its agent and the dashboard."""

import argparse
import json
import sys
from pathlib import Path

from ask_soony.agent import Analyst
from ask_soony.config import Settings
from ask_soony.llm import make_backend
from ask_soony.notify import build_notifiers
from ask_soony.portal import PortalClient
from ask_soony.store import Store
from ask_soony.watcher import (
    analyze_pending,
    describe_poll,
    import_snapshot,
    mirror,
    poll,
    watch,
)


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


def _escalation_loop(settings: Settings, every: int = 30) -> None:
    """Background loop: escalate undecided alerts once their answer time has run out."""
    import time

    from ask_soony.alerts import AlertBook, Policy
    from ask_soony.escalation import Escalator, Mailer, make_caller

    while True:
        try:
            store = Store(settings.data_dir)
            escalator = Escalator(
                AlertBook(store),
                Policy.load(settings.alert_policy),
                make_caller(settings.call_provider),
                Mailer.from_env(),
                settings.public_url,
            )
            escalator.tick()
            store.db.close()
        except Exception as e:  # keep the loop alive; problems show up in the log
            print(f"escalation loop: {e}", file=sys.stderr)
        time.sleep(every)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ask-soony", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_dl = sub.add_parser(
        "download", help="One-off: fetch the portal and write all files under their paths"
    )
    p_dl.add_argument("dest", nargs="?", default="data/portal", help="Target folder")
    p_imp = sub.add_parser(
        "import-snapshot", help="Seed an empty store with an earlier state (archive.zip or folder)"
    )
    p_imp.add_argument("source", help="archive.zip from the portal, or a folder with portal paths")
    p_imp.add_argument(
        "--at", required=True, help="When that state was taken, ISO 8601 with offset"
    )
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
    p_dash = sub.add_parser("dashboard", help="Serve the read-only web dashboard")
    p_dash.add_argument("--host", default="127.0.0.1")
    p_dash.add_argument("--port", type=int)
    p_serve = sub.add_parser("serve", help="Watch loop and dashboard in one process")
    p_serve.add_argument("--interval", type=int, help="Seconds between polls")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int)
    p_alert = sub.add_parser("alert", help="Raise, list and decide alerts")
    asub = p_alert.add_subparsers(dest="alert_cmd", required=True)
    a_raise = asub.add_parser("raise", help="Raise an alert and notify per the escalation policy")
    a_raise.add_argument("--title", required=True)
    a_raise.add_argument("--message", required=True)
    a_raise.add_argument("--action", default="")
    a_raise.add_argument(
        "--severity", default="medium", choices=["info", "low", "medium", "high", "critical"]
    )
    a_raise.add_argument(
        "--deadline", help="ISO 8601 with offset; with --impact launch it raises severity"
    )
    a_raise.add_argument("--impact", default="launch", choices=["launch", "cost", "data"])
    a_raise.add_argument("--external", action="store_true", help="The action goes to a supplier")
    asub.add_parser("list", help="List alerts")
    a_dec = asub.add_parser("decide", help="Record a decision")
    a_dec.add_argument("alert_id", type=int)
    a_dec.add_argument("decision", choices=["approve", "reject", "escalate", "resolve"])
    asub.add_parser("tick", help="Escalate alerts whose answer time ran out")
    p_f = sub.add_parser("findings", help="Print findings")
    p_f.add_argument("--all", action="store_true", help="Include resolved findings")
    p_f.add_argument("--json", action="store_true")
    sub.add_parser("status", help="Versions, pending analyses and open findings")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    store = Store(settings.data_dir)
    notify = build_notifiers(settings.notifiers, settings.discord_webhook_url, settings.webhook_url)

    if args.cmd == "import-snapshot":
        result = import_snapshot(store, Path(args.source), args.at)
        print(f"Baseline of {result.stored} files recorded at {args.at}.", file=sys.stderr)
        return 0
    if args.cmd == "download":
        result = poll(store, PortalClient(settings.portal_url, settings.portal_token))
        n = mirror(store, Path(args.dest))
        changes = describe_poll(result)
        print(f"{n} files in {args.dest}" + (f" ({changes.title})" if changes else ""))
        return 0
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
    if args.cmd == "alert":
        from ask_soony.alerts import AlertBook, Policy
        from ask_soony.escalation import Escalator, Mailer, make_caller

        policy = Policy.load(settings.alert_policy)
        book = AlertBook(store)
        escalator = Escalator(
            book,
            policy,
            make_caller(settings.call_provider),
            Mailer.from_env(),
            settings.public_url,
        )
        if args.alert_cmd == "raise":
            alert = book.raise_alert(
                title=args.title,
                message=args.message,
                action=args.action,
                severity=args.severity,
                deadline=args.deadline,
                impact=args.impact,
                external=args.external,
                policy=policy,
            )
            escalator.notify(alert)
            print(f"Alert #{alert.id} ({alert.severity}) raised.", file=sys.stderr)
            for e in book.events(alert.id):
                print(f"  {e['kind']:9} {e['channel']:9} {e['contact']:12} {e['detail']}")
        elif args.alert_cmd == "list":
            for a in book.list():
                print(f"#{a.id} [{a.severity}] {a.status:9} {a.title}")
        elif args.alert_cmd == "decide":
            a = book.decide(args.alert_id, args.decision, by="cli", channel="cli")
            if args.decision == "escalate":
                escalator.tick()
            print(f"Alert #{a.id}: {a.status}", file=sys.stderr)
        elif args.alert_cmd == "tick":
            print(f"Escalated {escalator.tick()} alerts.", file=sys.stderr)
        return 0
    if args.cmd in {"dashboard", "serve"}:
        import threading

        from ask_soony.dashboard import Dashboard

        if args.host not in {"127.0.0.1", "localhost"} and not settings.dashboard_password:
            print(
                "Refusing to listen beyond localhost without ASK_DASHBOARD_PASSWORD.",
                file=sys.stderr,
            )
            return 1
        server = Dashboard(settings.data_dir, settings.dashboard_password).serve(
            args.host, args.port or settings.dashboard_port
        )
        print(f"Dashboard on http://{args.host}:{server.server_port}/", file=sys.stderr)
        if args.cmd == "dashboard":
            server.serve_forever()
            return 0
        threading.Thread(target=server.serve_forever, daemon=True).start()
        threading.Thread(target=_escalation_loop, args=(settings,), daemon=True).start()
        watch(
            store,
            PortalClient(settings.portal_url, settings.portal_token),
            _analyst(settings, store),
            notify,
            args.interval or settings.interval_seconds,
            settings.notify_min_severity,
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
