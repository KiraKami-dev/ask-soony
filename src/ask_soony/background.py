"""
Background loops of the product, started inside the dashboard app so everything runs as one
server: the portal watcher, alert escalation and the supplier reply poller.

Only with ASK_BACKGROUND=on in the process environment (tests and local UI work stay quiet).
ASK_REPLY_POLL=on alone still starts just the reply poller.
"""

import os
import sys
import threading

ON = ("1", "on", "true", "yes")


def _watcher() -> None:
    """
    The team's portal loop (runner.loop): poll and mirror the portal, record each drop as an
    event (the UI's "New: … Agent reading" toast), and run the verifier and predictor pipeline
    on the changed files. The paper check notices the mirror change on its own (_papercheck), so
    every drop reaches all three detectors with one watcher.
    """
    from ask_soony import runner
    from ask_soony.config import Settings

    runner.loop(Settings.from_env().interval_seconds)


def _escalation() -> None:
    from ask_soony.cli import _escalation_loop
    from ask_soony.config import Settings

    _escalation_loop(Settings.from_env())


def _papercheck() -> None:
    """Supplier documents against the ERP: at start and whenever the portal mirror changes
    (cheap when nothing changed: LLM answers are cached by content hash)."""
    from ask_soony import papercheck

    if os.environ.get("ASK_PAPERCHECK", "on").lower() in ON:
        papercheck.loop()


def _guard(target):
    def run():
        try:
            target()
        except Exception as e:  # a crashed loop must not take the web server down
            print(f"background {target.__name__} stopped: {e}", file=sys.stderr)

    return run


def start() -> None:
    from ask_soony import replies

    if os.environ.get("ASK_BACKGROUND", "").lower() in ON:
        from ask_soony import anchors

        anchors.ensure_index()  # evidence anchoring: index portal files (cached by hash)
        from ask_soony import routines

        routines.start_background()  # routines the agent wrote, run in the Monty sandbox
        for loop in (_watcher, _escalation, _papercheck):
            threading.Thread(target=_guard(loop), daemon=True, name=loop.__name__).start()
        os.environ.setdefault("ASK_REPLY_POLL", "on")
    replies.start_background()
    from ask_soony import teams

    teams.start_background()  # ASK_TEAMS=on: post new launch alerts to a Teams channel
