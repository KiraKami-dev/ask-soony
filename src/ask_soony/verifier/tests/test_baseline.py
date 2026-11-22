"""The frozen reading baseline: it wins, live readings fill the gaps. All data is made up."""

import json

from ask_soony.verifier.baseline import add, baseline_keys, reading_metas


def store(folder, path, sha, status="ok", model="m"):
    doc = folder / f"{path.replace('/', '__')}__{sha[:12]}"
    doc.mkdir(parents=True)
    meta = {"path": path, "sha256": sha, "status": status, "model": model}
    (doc / "meta.json").write_text(json.dumps(meta))
    (doc / "verdict.json").write_text("{}")
    return doc / "meta.json"


def test_baseline_wins_for_the_same_version_live_fills_the_rest(tmp_path):
    live, base = tmp_path / "live", tmp_path / "base"
    store(live, "a.jpg", "1" * 64, model="cheap")
    base_a = store(base, "a.jpg", "1" * 64, model="strong")
    live_b = store(live, "b.jpg", "2" * 64)
    live_a2 = store(live, "a.jpg", "9" * 64)  # a newer version of a.jpg: only live has it
    assert reading_metas(live, base) == [base_a, live_a2, live_b]


def test_missing_live_store_still_gets_the_baseline(tmp_path):
    base = tmp_path / "base"
    base_a = store(base, "a.jpg", "1" * 64)
    assert reading_metas(tmp_path / "nothing", base) == [base_a]


def test_failed_reads_and_files_not_in_the_portal_are_left_out(tmp_path):
    base = tmp_path / "base"
    store(base, "failed.pdf", "3" * 64, status="error")
    store(base, "gone.jpg", "4" * 64)
    kept = store(base, "a.jpg", "1" * 64)
    assert reading_metas(None, base, portal_paths={"a.jpg", "failed.pdf"}) == [kept]
    assert baseline_keys(base) == {("a.jpg", "1" * 64), ("gone.jpg", "4" * 64)}


def test_no_baseline(tmp_path):
    base = tmp_path / "base"
    store(base, "a.jpg", "1" * 64)
    assert reading_metas(None, None) == []


def test_add_only_copies_versions_the_baseline_lacks(tmp_path):
    source, base = tmp_path / "strong", tmp_path / "base"
    kept = store(base, "a.jpg", "1" * 64, model="old")
    before = kept.read_text()
    store(source, "a.jpg", "1" * 64, model="new")
    new = store(source, "c.pdf", "5" * 64)
    (new.parent / "transcript.json").write_text("{}")
    store(source, "bad.pdf", "6" * 64, status="error")
    assert add(source, base) == ["c.pdf"]
    assert kept.read_text() == before
    added = base / new.parent.name
    assert sorted(p.name for p in added.iterdir()) == ["meta.json", "verdict.json"]


def test_add_replaces_a_failed_read_in_the_baseline(tmp_path):
    source, base = tmp_path / "strong", tmp_path / "base"
    failed = store(base, "big.pdf", "7" * 64, status="error")
    store(source, "big.pdf", "7" * 64)
    assert add(source, base) == ["big.pdf"]
    assert baseline_keys(base) == {("big.pdf", "7" * 64)}
    assert failed.exists()
