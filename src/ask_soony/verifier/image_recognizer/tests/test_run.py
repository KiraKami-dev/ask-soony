"""The parts of the runner that don't call the model."""

import asyncio
import json

from ask_soony.verifier.image_recognizer import run
from ask_soony.verifier.image_recognizer.run import (
    Settings,
    _not_retryable,
    fingerprint,
    is_done,
    select_jobs,
)
from ask_soony.verifier.image_recognizer.schema import VERDICT_SCHEMA

FP = "f" * 16


def settings(tmp_path, model="m", effort="high") -> Settings:
    return Settings(tmp_path, tmp_path / "out", model, effort, 1, 2)


def make(root, rel, data=b"x"):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_select_jobs_takes_documents_scans_first(tmp_path):
    make(tmp_path, "b/doc.pdf")
    make(tmp_path, "a/scan.jpg")
    make(tmp_path, "a/notes.csv")
    make(tmp_path, "a/.hidden.png")
    jobs = select_jobs(tmp_path, None, False, None)
    assert [j.rel for j in jobs] == ["a/scan.jpg", "b/doc.pdf"]
    assert [j.rel for j in select_jobs(tmp_path, None, True, None)] == ["a/scan.jpg"]
    assert [j.rel for j in select_jobs(tmp_path, "doc", False, None)] == ["b/doc.pdf"]


def test_changed_file_is_read_again(tmp_path):
    root, out = tmp_path / "portal", tmp_path / "out"
    make(root, "x/scan.jpg", b"v1")
    job = select_jobs(root, None, False, None)[0]
    (out / job.folder).mkdir(parents=True)
    (out / job.folder / "meta.json").write_text(
        json.dumps({"status": "ok", "sha256": job.sha256, "fingerprint": FP})
    )
    assert is_done(job, out, FP)
    make(root, "x/scan.jpg", b"v2")
    changed = select_jobs(root, None, False, None)[0]
    assert not is_done(changed, out, FP)  # new checksum, new folder


def test_failed_read_is_not_done(tmp_path):
    root, out = tmp_path / "portal", tmp_path / "out"
    make(root, "x/scan.jpg")
    job = select_jobs(root, None, False, None)[0]
    (out / job.folder).mkdir(parents=True)
    (out / job.folder / "meta.json").write_text(
        json.dumps({"status": "error", "sha256": job.sha256, "fingerprint": FP})
    )
    assert not is_done(job, out, FP)


def test_safety_blocks_are_not_retried():
    assert _not_retryable("refusal: cannot help")
    assert not _not_retryable("ProcessError: exit code 1")


def test_schema_requires_every_field():
    # Structured output needs every property listed as required, at every level
    def check(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                assert set(node["required"]) == set(node["properties"]), node["required"]
                assert node.get("additionalProperties") is False
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(VERDICT_SCHEMA)


def test_result_from_another_schema_or_model_is_read_again(tmp_path):
    root, out = tmp_path / "portal", tmp_path / "out"
    make(root, "x/scan.jpg")
    job = select_jobs(root, None, False, None)[0]
    (out / job.folder).mkdir(parents=True)
    current = fingerprint(settings(tmp_path))
    meta = out / job.folder / "meta.json"
    meta.write_text(json.dumps({"status": "ok", "sha256": job.sha256, "fingerprint": current}))
    assert is_done(job, out, current)
    assert fingerprint(settings(tmp_path, model="other")) != current
    assert fingerprint(settings(tmp_path, effort="low")) != current
    # A result from before fingerprints existed: read again
    meta.write_text(json.dumps({"status": "ok", "sha256": job.sha256}))
    assert not is_done(job, out, current)


def _fake_reader(results):
    calls = []

    async def read(job, settings, client):
        calls.append(job.rel)
        return results[min(len(calls), len(results)) - 1]

    return read, calls


def test_flaky_error_is_retried_until_it_works(tmp_path, monkeypatch):
    ok = {
        "verdict": {"verdict": {}},
        "usage": None,
        "error": None,
    }
    read, calls = _fake_reader([{"error": "ProcessError: exit 1"}, ok])
    monkeypatch.setattr(run, "read_document", read)
    monkeypatch.setattr(run, "RETRY_WAIT_SECONDS", 0)
    make(tmp_path, "x/scan.jpg")
    job = select_jobs(tmp_path, None, False, None)[0]
    row = asyncio.run(run.process(job, settings(tmp_path), asyncio.Semaphore(1)))
    assert (row["status"], len(calls)) == ("ok", 2)


def test_safety_block_is_tried_once(tmp_path, monkeypatch):
    read, calls = _fake_reader([{"error": "refusal: cannot help"}])
    monkeypatch.setattr(run, "read_document", read)
    monkeypatch.setattr(run, "RETRY_WAIT_SECONDS", 0)
    make(tmp_path, "x/scan.jpg")
    job = select_jobs(tmp_path, None, False, None)[0]
    row = asyncio.run(run.process(job, settings(tmp_path), asyncio.Semaphore(1)))
    assert (row["status"], len(calls)) == ("error", 1)
