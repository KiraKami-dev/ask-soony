"""The golden set and its scorer. Free: scores committed readings, no model calls."""

import copy
import json

from ask_soony.verifier.baseline import BASELINE_DIR
from ask_soony.verifier.image_recognizer.golden import (
    load_cases,
    passes,
    report,
    score,
    score_store,
)


def test_the_baseline_passes_every_case():
    results = score_store(BASELINE_DIR)
    failed = [(r.case["id"], [c for c, ok in r.results if not ok]) for r in results if not r.passed]
    assert not failed
    assert all(r.same_file for r in results)


def test_cases_are_well_formed():
    cases = load_cases()
    assert len({c["id"] for c in cases}) == len(cases)
    assert any(c["must"] for c in cases)
    for case in cases:
        assert case["path"] and len(case["sha256"]) == 64 and case["checks"]


def _artbook():
    case = next(c for c in load_cases() if c["id"] == "artbook_handwritten_correction")
    meta = next(BASELINE_DIR.glob("*WZ-261116-473*/meta.json"))
    return case, json.loads((meta.parent / "verdict.json").read_text())


def test_missing_the_crossed_out_value_fails():
    case, verdict = _artbook()
    misread = copy.deepcopy(verdict)
    for line in misread["lines"]:
        for value in line["values"]:
            value["crossed_out"] = False  # the reader did not see the strike-through
    result = score(case, misread)
    assert not result.passed
    assert not passes({"value": 80000, "crossed_out": True}, misread)


def test_a_missing_must_case_is_not_trusted(tmp_path):
    text, trusted = report(score_store(tmp_path))  # empty store: nothing read
    assert not trusted and "not read" in text


def test_thousands_dot_is_judged_after_the_linker_fix():
    verdict = {
        "lines": [
            {
                "values": [
                    {
                        "field": "quantity_delivered",
                        "value": "134.000",
                        "number": 134,
                        "written": "printed",
                        "crossed_out": False,
                    }
                ]
            }
        ]
    }
    assert passes({"value": 134000}, verdict)


def _v(field, number, written="printed", crossed_out=False):
    return {
        "field": field,
        "value": str(number),
        "number": number,
        "written": written,
        "crossed_out": crossed_out,
    }


def test_a_number_on_another_field_does_not_count():
    verdict = {"lines": [{"values": [_v("packages", 3)]}]}
    assert not passes({"value": 3, "field": "accept_number"}, verdict)
    assert passes({"value": 3, "field": "packages"}, verdict)


def test_an_invented_correction_fails_the_clean_control():
    clean = {"lines": [{"values": [_v("quantity_delivered", 96000)]}]}
    invented = {
        "lines": [
            {
                "values": [
                    _v("quantity_delivered", 96000),
                    _v("quantity_received", 96, written="handwritten"),
                ]
            }
        ]
    }
    assert passes({"no_marks": "quantity"}, clean)
    assert not passes({"no_marks": "quantity"}, invented)


def test_a_changed_file_makes_the_case_fail():
    case, verdict = _artbook()
    assert score(case, verdict, case["sha256"]).passed
    changed = score(case, verdict, "0" * 64)
    assert not changed.passed
    assert "case invalid" in report([changed])[0]
