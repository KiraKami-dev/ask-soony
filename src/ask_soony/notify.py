"""
Notifiers: where the watcher reports changes and findings.

One small interface with interchangeable adapters, so the same events can go to the console,
a macOS notification, a Discord channel or any webhook (for example the product's own backend).
"""

import subprocess
import sys
from dataclasses import dataclass, field
from typing import Protocol

import httpx

SEVERITY_COLORS = {
    "critical": 0xB3261E,
    "high": 0xD97706,
    "medium": 0x2563EB,
    "low": 0x6B7280,
    "info": 0x6B7280,
}


@dataclass
class Event:
    kind: str  # changes | finding | error
    title: str
    body: str
    severity: str = "info"
    data: dict = field(default_factory=dict)


class Notifier(Protocol):
    def send(self, event: Event) -> None: ...


class ConsoleNotifier:
    def send(self, event: Event) -> None:
        print(f"[{event.severity.upper():8}] {event.title}\n{event.body}\n", file=sys.stderr)


class MacNotifier:
    def send(self, event: Event) -> None:
        script = 'display notification (system attribute "B") with title (system attribute "T")'
        env = {"T": f"ask-soony: {event.title}"[:120], "B": event.body[:240]}
        subprocess.run(["osascript", "-e", script], env=env, check=False, capture_output=True)


class DiscordNotifier:
    """Posts to a Discord channel through an incoming webhook URL."""

    def __init__(self, url: str, http: httpx.Client | None = None):
        self.url = url
        self.http = http or httpx.Client(timeout=15.0)

    def send(self, event: Event) -> None:
        embed = {
            "title": event.title[:256],
            "description": event.body[:4000],
            "color": SEVERITY_COLORS.get(event.severity, 0x6B7280),
        }
        self.http.post(self.url, json={"username": "ask-soony watcher", "embeds": [embed]})


class WebhookNotifier:
    """Generic JSON webhook, e.g. for the product backend or another chat tool."""

    def __init__(self, url: str, http: httpx.Client | None = None):
        self.url = url
        self.http = http or httpx.Client(timeout=15.0)

    def send(self, event: Event) -> None:
        self.http.post(
            self.url,
            json={
                "kind": event.kind,
                "title": event.title,
                "body": event.body,
                "severity": event.severity,
                "data": event.data,
            },
        )


class Fanout:
    def __init__(self, notifiers: list[Notifier]):
        self.notifiers = notifiers

    def send(self, event: Event) -> None:
        for n in self.notifiers:
            try:
                n.send(event)
            except Exception as e:  # a broken channel must not stop the watcher
                print(f"notifier {type(n).__name__} failed: {e}", file=sys.stderr)


def build_notifiers(names: list[str], discord_url: str = "", webhook_url: str = "") -> Fanout:
    out: list[Notifier] = []
    for name in names:
        if name == "console":
            out.append(ConsoleNotifier())
        elif name == "macos":
            out.append(MacNotifier())
        elif name == "discord":
            if not discord_url:
                raise ValueError(
                    "ASK_NOTIFIERS includes discord but ASK_DISCORD_WEBHOOK_URL is empty"
                )
            out.append(DiscordNotifier(discord_url))
        elif name == "webhook":
            if not webhook_url:
                raise ValueError("ASK_NOTIFIERS includes webhook but ASK_WEBHOOK_URL is empty")
            out.append(WebhookNotifier(webhook_url))
        else:
            raise ValueError(f"unknown notifier {name!r}: use console, macos, discord or webhook")
    return Fanout(out)
