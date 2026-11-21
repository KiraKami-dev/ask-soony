import hashlib
import io
import json

import httpx
import openpyxl

from ask_soony.agent import Analyst
from ask_soony.extract import extract
from ask_soony.llm import RunResult
from ask_soony.notify import DiscordNotifier, Event, Fanout
from ask_soony.portal import PortalClient
from ask_soony.store import Store
from ask_soony.watcher import analyze_pending, describe_poll, poll

BASE = "https://portal.example.com"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakePortal:
    """In-memory portal served through httpx.MockTransport."""

    def __init__(self, files: dict[str, bytes]):
        self.files = dict(files)
        self.downloads = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-token"
        path = request.url.path
        if path == "/api/v1/files":
            items = [
                {"path": p, "sha256": sha(d), "bytes": len(d)}
                for p, d in sorted(self.files.items())
            ]
            return httpx.Response(200, json={"count": len(items), "files": items})
        prefix = "/api/v1/files/"
        if path.startswith(prefix):
            self.downloads += 1
            return httpx.Response(200, content=self.files[path[len(prefix) :]])
        return httpx.Response(404)

    def client(self) -> PortalClient:
        return PortalClient(
            BASE, "test-token", http=httpx.Client(transport=httpx.MockTransport(self.handler))
        )


def test_poll_tracks_new_changed_removed_and_keeps_old_versions(tmp_path):
    portal = FakePortal({"acme/orders.csv": b"po,qty\n1001,500\n", "acme/notes.md": b"hello"})
    store = Store(tmp_path)

    first = poll(store, portal.client())
    assert first.baseline
    assert store.pending() == []  # baseline is stored, not analysed

    portal.files["acme/orders.csv"] = b"po,qty\n1001,450\n"
    portal.files["acme/new_notice.txt"] = b"Plant closed on Monday"
    del portal.files["acme/notes.md"]
    second = poll(store, portal.client())

    assert [v.path for v in second.new] == ["acme/new_notice.txt"]
    assert [v.path for v in second.changed] == ["acme/orders.csv"]
    assert second.removed == ["acme/notes.md"]
    assert {v.path for v in store.pending()} == {"acme/new_notice.txt", "acme/orders.csv"}
    # Both versions of the changed file are still on disk.
    assert store.read_blob(sha(b"po,qty\n1001,500\n")) == b"po,qty\n1001,500\n"
    assert store.read_blob(sha(b"po,qty\n1001,450\n")) == b"po,qty\n1001,450\n"

    third = poll(store, portal.client())
    assert not third.has_changes
    assert describe_poll(third) is None
    assert portal.downloads == 4  # unchanged files are never downloaded twice


def test_extract_reads_xlsx_and_csv():
    wb = openpyxl.Workbook()
    wb.active.title = "Plan"
    wb.active.append(["Part", "Qty"])
    wb.active.append(["P-1", 7])
    buf = io.BytesIO()
    wb.save(buf)
    assert "P-1 | 7" in extract("plan.xlsx", buf.getvalue()).text
    assert extract("a.csv", "Zawiadomienie;przestój\n".encode("cp1250")).text.startswith(
        "Zawiadomienie"
    )


class ScriptedBackend:
    """Stands in for an LLM: calls a lookup tool, then submits an analysis."""

    provider = "fake"
    model = "scripted"

    def run(self, system, text, images, tools, handler, should_stop, max_turns=12):
        assert "acme/new_notice.txt" in text
        found = handler("search_files", {"query": "1001"})
        assert "acme/orders.csv" in found
        handler(
            "submit_analysis",
            {
                "summary": "Supplier plant closed on Monday.",
                "document_type": "notice",
                "language": "en",
                "launch_relevance": "high",
                "findings": [
                    {
                        "severity": "critical",
                        "title": "Plant closure blocks order 1001",
                        "detail": "Order 1001 (450 pcs) cannot ship.",
                        "evidence": [
                            {"path": "acme/new_notice.txt", "quote": "Plant closed on Monday"}
                        ],
                        "suggested_action": "Ask the buyer for a second source today.",
                    }
                ],
            },
        )
        assert should_stop()
        return RunResult(final_text="", tool_calls=2, stop_reason="submitted")


class Recorder:
    def __init__(self):
        self.events: list[Event] = []

    def send(self, event: Event) -> None:
        self.events.append(event)


def test_agent_findings_are_stored_and_notified(tmp_path):
    portal = FakePortal({"acme/orders.csv": b"po,qty\n1001,450\n"})
    store = Store(tmp_path)
    poll(store, portal.client())
    portal.files["acme/new_notice.txt"] = b"Plant closed on Monday"
    poll(store, portal.client())

    rec = Recorder()
    n = analyze_pending(
        store, Analyst(store, ScriptedBackend()), Fanout([rec]), min_severity="high"
    )

    assert n == 1
    findings = store.findings("open")
    assert [f["title"] for f in findings] == ["Plant closure blocks order 1001"]
    assert findings[0]["path"] == "acme/new_notice.txt"
    assert rec.events and rec.events[0].severity == "critical"
    assert store.pending() == []


def test_discord_notifier_posts_embed():
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(204)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    DiscordNotifier("https://discord.example.com/hook", http=http).send(
        Event("finding", "Title", "Body", "high")
    )
    assert sent[0]["embeds"][0]["title"] == "Title"
