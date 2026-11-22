"""
Golden set: must-catch facts per document, to decide whether a reader (model + prompt) can be
trusted. See golden.json.

A check looks for a fact, not a field name, so two models that name a field differently still
compare. Numbers are compared after the linker's fixes (the same `_value` the linker loads with),
so a reader error that code already repairs does not count against the model.

Check kinds:
  {"value": N, "field": prefix?, "crossed_out": bool?, "written": str?}  some value has number N
      (and its field starts with the prefix: the schema fixes field names, so this is safe)
  {"no_marks": prefix}  negative control: no value of that field is crossed out or handwritten
  {"standing": N, "field": prefix}  N is a value that stands (not crossed out) for that field
  {"reference": "X"}  a reference on the document has value X
  {"decision": "fail"}  a decision's outcome contains this text
  {"text": regex}  the regex matches somewhere in the reading's text (values, statements, flags)
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from ask_soony.verifier.linker.adapters.readings import _value, final_values

GOLDEN_FILE = Path(__file__).parent / "golden.json"


def load_cases(path: Path = GOLDEN_FILE) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


def _lines(verdict: dict) -> list[list[dict]]:
    return [[_value(v) for v in line.get("values") or []] for line in verdict.get("lines") or []]


def _text(verdict: dict) -> str:
    """Everything the reader wrote down as text, in one string."""
    parts: list[str] = []
    for line in verdict.get("lines") or []:
        parts += [str(line.get(k) or "") for k in ("item", "description", "material_id")]
        parts += [str(v.get("value") or "") for v in line.get("values") or []]
    for key in ("statements", "decisions", "corrections", "references"):
        parts += [json.dumps(x, ensure_ascii=False) for x in verdict.get(key) or []]
    block = verdict.get("verdict") or {}
    parts += [str(x) for x in block.get("red_flags") or []]
    parts += [str(block.get("summary_en") or ""), json.dumps(block.get("observations") or "")]
    return "\n".join(parts)


def _number_matches(value: dict, expected: float) -> bool:
    return value["number"] is not None and float(value["number"]) == float(expected)


def passes(check: dict, verdict: dict) -> bool:
    lines = _lines(verdict)
    values = [v for line in lines for v in line]
    if "value" in check:
        return any(
            _number_matches(v, check["value"])
            and v["field"].startswith(check.get("field", ""))
            and ("crossed_out" not in check or v["crossed_out"] == check["crossed_out"])
            and ("written" not in check or v["written"] == check["written"])
            for v in values
        )
    if "no_marks" in check:
        return not any(
            v["field"].startswith(check["no_marks"])
            and (v["crossed_out"] or v["written"] == "handwritten")
            for v in values
        )
    if "standing" in check:
        prefix = check.get("field", "")
        return any(
            field.startswith(prefix) and _number_matches(v, check["standing"])
            for line in lines
            for field, v in final_values(line).items()
        )
    if "reference" in check:
        wanted = check["reference"]
        return any(wanted in str(r.get("value") or "") for r in verdict.get("references") or [])
    if "decision" in check:
        return any(
            check["decision"].lower() in str(d.get("outcome") or "").lower()
            for d in verdict.get("decisions") or []
        )
    if "text" in check:
        return re.search(check["text"], _text(verdict), re.IGNORECASE) is not None
    raise ValueError(f"unknown check: {check}")


@dataclass
class CaseResult:
    case: dict
    found: bool  # a reading of this document exists
    same_file: bool  # the reading is of the file version the case was written for; if not,
    # the case no longer describes that file and fails until a human rewrites it
    results: list[tuple[dict, bool]]

    @property
    def passed(self) -> bool:
        return self.found and self.same_file and all(ok for _, ok in self.results)


def score(case: dict, verdict: dict | None, read_sha: str | None = None) -> CaseResult:
    if verdict is None:
        return CaseResult(case, False, False, [(c, False) for c in case["checks"]])
    results = [(c, passes(c, verdict)) for c in case["checks"]]
    return CaseResult(case, True, read_sha in (None, case.get("sha256")), results)


def readings_by_path(store: Path) -> dict[str, tuple[dict, str]]:
    """path -> (verdict, sha256) for every successful reading in a store folder."""
    out = {}
    for meta_path in sorted(store.glob("*/meta.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("status") != "ok":
                continue
            verdict = json.loads((meta_path.parent / "verdict.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out[meta["path"]] = (verdict, meta.get("sha256"))
    return out


def score_store(store: Path, cases: list[dict] | None = None) -> list[CaseResult]:
    readings = readings_by_path(store)
    results = []
    for case in cases if cases is not None else load_cases():
        verdict, sha = readings.get(case["path"], (None, None))
        results.append(score(case, verdict, sha))
    return results


def _label(check: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in check.items())


def report(results: list[CaseResult]) -> tuple[str, bool]:
    """Readable table, and whether every must-catch case passed."""
    rows, trusted = [], True
    for r in results:
        case = r.case
        mark = "PASS" if r.passed else "FAIL"
        flags = []
        if case.get("must"):
            flags.append("must")
            trusted &= r.passed
        if not case.get("confirmed"):
            flags.append("unconfirmed")
        if r.found and not r.same_file:
            flags.append("file changed: case invalid, rewrite it")
        if not r.found:
            flags.append("not read")
        rows.append(f"{mark}  {case['id']}  ({', '.join(flags)})")
        rows += [f"   {'ok ' if ok else 'XX '} {_label(c)}" for c, ok in r.results]
    verdict = "TRUSTED: every must-catch case passed" if trusted else "NOT TRUSTED"
    return "\n".join(rows + ["", verdict]), trusted
