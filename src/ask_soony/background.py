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
    from ask_soony.cli import _analyst
    from ask_soony.config import Settings
    from ask_soony.notify import build_notifiers
    from ask_soony.portal import PortalClient
    from ask_soony.store import Store
    from ask_soony.watcher import watch

    settings = Settings.from_env()
    store = Store(settings.data_dir)
    notify = build_notifiers(settings.notifiers, settings.discord_webhook_url, settings.webhook_url)
    watch(
        store,
        PortalClient(settings.portal_url, settings.portal_token),
        _analyst(settings, store),
        notify,
        settings.interval_seconds,
        settings.notify_min_severity,
    )


def _escalation() -> None:
    from ask_soony.cli import _escalation_loop
    from ask_soony.config import Settings

    _escalation_loop(Settings.from_env())


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
        for loop in (_watcher, _escalation):
            threading.Thread(target=_guard(loop), daemon=True, name=loop.__name__).start()
        os.environ.setdefault("ASK_REPLY_POLL", "on")
    replies.start_background()
    from ask_soony import teams

    teams.start_background()  # ASK_TEAMS=on: post new launch alerts to a Teams channel
