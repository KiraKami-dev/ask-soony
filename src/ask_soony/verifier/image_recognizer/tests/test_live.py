"""Live reading before the verifier links a drop. No model calls: run_all is faked."""

import json
import time

import pytest

from ask_soony.verifier.image_recognizer import live, run
from ask_soony.verifier.image_recognizer.run import Settings, fingerprint


def make(root, rel, data):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


@pytest.fixture
def portal(tmp_path, monkeypatch):
    root = tmp_path / "portal"
    make(root, "a/old.jpg", b"old")
    make(root, "a/new.jpg", b"new")
    make(root, "b/other.pdf", b"other")
    make(root, "b/redline.pdf", b"red")
    make(root, "b/data.csv", b"x,y")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.delenv("ASK_READER", raising=False)
    monkeypatch.delenv("ASK_READER_MAX_DOCS", raising=False)
    monkeypatch.setattr(live, "ignored_paths", lambda: {"b/redline.pdf"})
    return root


def trusted():
    return {fingerprint(Settings(None, None, "gpt-5.4-mini", "medium", 1, 1))}


def fake_reader(monkeypatch, fail=(), error="boom"):
    """Stands in for run_all; saves results the way the real runner does (run._save)."""
    calls = []

    async def run_all(jobs, settings, force):
        for job in jobs:
            calls.append(job.rel)
            outcome = {"error": error} if job.rel in fail else {"verdict": {}, "error": None}
            run._save(job, settings, outcome, time.time(), 1)
        return {}

    monkeypatch.setattr(live, "run_all", run_all)
    return calls


def test_reads_changed_documents_first_and_skips_baseline_ignored_and_data(
    portal, tmp_path, monkeypatch
):
    base = tmp_path / "base"
    from ask_soony.verifier.image_recognizer.run import select_jobs

    old = next(j for j in select_jobs(portal, None, False, None) if j.rel == "a/old.jpg")
    (base / old.folder).mkdir(parents=True)
    (base / old.folder / "meta.json").write_text(
        json.dumps({"path": old.rel, "sha256": old.sha256, "status": "ok"})
    )
    (base / old.folder / "verdict.json").write_text("{}")
    calls = fake_reader(monkeypatch)
    result = live.read_unread(["b/other.pdf"], portal, tmp_path / "out", base, trusted())
    assert calls == ["b/other.pdf", "a/new.jpg"]  # changed first; no baseline, ignored or csv
    assert sorted(result.read) == ["a/new.jpg", "b/other.pdf"]
    calls.clear()
    assert live.read_unread([], portal, tmp_path / "out", base, trusted()).read == []
    assert calls == []  # already read: nothing to do


def test_a_failed_read_is_retried_on_the_next_refresh(portal, tmp_path, monkeypatch):
    calls = fake_reader(monkeypatch, fail={"a/new.jpg"})
    first = live.read_unread([], portal, tmp_path / "out", None, trusted())
    assert [f["path"] for f in first.failed] == ["a/new.jpg"]
    calls.clear()
    live.read_unread([], portal, tmp_path / "out", None, trusted())
    assert calls == ["a/new.jpg"]


def test_an_untrusted_reader_or_no_key_reads_nothing_and_says_why(portal, tmp_path, monkeypatch):
    calls = fake_reader(monkeypatch)
    untrusted = live.read_unread([], portal, tmp_path / "out", None, set())
    assert calls == []
    assert {u["reason"] for u in untrusted.unread} == {
        "reader gpt-5.4-mini/medium has not passed the gate"
    }
    monkeypatch.delenv("OPENAI_API_KEY")
    nokey = live.read_unread([], portal, tmp_path / "out", None, trusted())
    assert calls == [] and {u["reason"] for u in nokey.unread} == {"no OPENAI_API_KEY"}


def test_the_cap_leaves_the_rest_for_the_next_refresh(portal, tmp_path, monkeypatch):
    monkeypatch.setenv("ASK_READER_MAX_DOCS", "1")
    calls = fake_reader(monkeypatch)
    result = live.read_unread(["a/new.jpg"], portal, tmp_path / "out", None, trusted())
    assert calls == ["a/new.jpg"]
    assert [u["reason"] for u in result.unread] == ["over the cap, next refresh"] * 2


def test_switched_off(portal, tmp_path, monkeypatch):
    monkeypatch.setenv("ASK_READER", "off")
    calls = fake_reader(monkeypatch)
    assert live.read_unread([], portal, tmp_path / "out", None, trusted()).as_dict()["read"] == []
    assert calls == []


def test_the_committed_trust_matches_the_current_reader():
    # A prompt or schema change without a new gate run fails here, before it reaches the server
    assert trusted() <= live.trusted_fingerprints()


def test_a_file_that_keeps_failing_is_given_up_after_three_tries(portal, tmp_path, monkeypatch):
    calls = fake_reader(monkeypatch, fail={"a/new.jpg"})
    for _ in range(3):
        live.read_unread([], portal, tmp_path / "out", None, trusted())
    assert calls.count("a/new.jpg") == 3
    calls.clear()
    result = live.read_unread([], portal, tmp_path / "out", None, trusted())
    assert "a/new.jpg" not in calls
    assert [u for u in result.unread if u["path"] == "a/new.jpg"] == [
        {"path": "a/new.jpg", "reason": "failed 3x, not retried: boom"}
    ]


def test_a_bad_key_does_not_count_against_the_files(portal, tmp_path, monkeypatch):
    calls = fake_reader(monkeypatch, fail={"a/new.jpg"}, error="AuthenticationError: 401")
    for _ in range(4):
        live.read_unread([], portal, tmp_path / "out", None, trusted())
    assert calls.count("a/new.jpg") == 4  # still tried: the key, not the file, was the problem
