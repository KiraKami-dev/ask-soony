"""Management memos (.docx) as quoted launch rules."""

import zipfile

from ask_soony import memo


def _docx(path, paras):
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paras)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f"<w:document><w:body>{body}</w:body></w:document>")


def test_memo_rules_and_first_edition(tmp_path):
    (tmp_path / "soony" / "context").mkdir(parents=True)
    (tmp_path / "soony" / "master").mkdir(parents=True)
    (tmp_path / "soony" / "master" / "products.csv").write_text(
        "product_id,name,lifecycle\nP-STD,Thing – Standard Edition,launch\n"
        "P-CE,Thing – Collector’s Edition (numbered),launch\n"
    )
    _docx(
        tmp_path / "soony" / "context" / "memo_x.docx",
        [
            "Memo: priorities",
            "Date: 2030-05-30",
            "Collector's Edition first. Short parts go to the cell first.",
            "Quality &amp; Compliance has the last word, not the plan.",
            "Air freight and rush orders above the policy limits reach me the same hour.",
        ],
    )
    kinds = {r["kind"]: r for r in memo.rules(tmp_path)}
    assert set(kinds) == {"priority", "release", "approval"}
    assert kinds["release"]["text"].startswith("Quality & Compliance")
    assert kinds["priority"]["date"] == "2030-05-30"
    ed, rule = memo.first_edition(tmp_path, ["P-STD", "P-CE"])
    assert ed == "P-CE" and rule["file"] == "soony/context/memo_x.docx"


def test_no_memo_no_rules(tmp_path):
    assert memo.rules(tmp_path) == [] and memo.first_edition(tmp_path, ["P"]) is None
