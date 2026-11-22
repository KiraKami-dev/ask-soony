"""
Management memos in the customer's context folder (Word files): the rules they set for the launch,
as quoted evidence. Read at run time from any .docx under soony/context; nothing is assumed about
their wording beyond a few rule shapes:

- priority: "<X> Edition first" (who gets a short part)
- release: who has the last word on what may go into the box (quality, compliance)
- approval: air freight or rush orders above limits, and how fast they go to whom
"""

from __future__ import annotations

import re
import zipfile
from html import unescape
from pathlib import Path

READS_DOCX = True  # datacheck: Word files in soony/context have a reader

_PARA = re.compile(r"<w:p[ >].*?</w:p>", re.S)
_TAG = re.compile(r"<[^>]+>")
_DATE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
RULES = {
    "priority": re.compile(r"\b[\w'’ -]{0,40}\bedition\b[^.]{0,20}\bfirst\b", re.I),
    "release": re.compile(r"last word|quality\s*(?:&|and)\s*compliance", re.I),
    "approval": re.compile(r"\b(?:air|rush|express)\b.*\b(?:limit|hour|approv)", re.I),
}


def paragraphs(path: Path) -> list[str]:
    """The text paragraphs of a .docx (python-docx not needed: the XML in the zip)."""
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    except (OSError, KeyError, zipfile.BadZipFile):
        return []
    out = []
    for p in _PARA.findall(xml):
        p = re.sub(r"<w:(?:br|tab)\b[^>]*/>", " ", p)
        text = " ".join(unescape(_TAG.sub("", p)).split())
        if text:
            out.append(text)
    return out


def rules(root: Path | None) -> list[dict]:
    """[{kind, text, file, date}] from every memo in soony/context."""
    if root is None:
        return []
    out = []
    for f in sorted((root / "soony" / "context").glob("*.docx")):
        paras = paragraphs(f)
        date = next((m.group(1) for p in paras[:4] if (m := _DATE.search(p))), None)
        for p in paras:
            for kind, pattern in RULES.items():
                if pattern.search(p):
                    out.append(
                        {"kind": kind, "text": p, "file": str(f.relative_to(root)), "date": date}
                    )
    return out


def _norm(s: str) -> str:
    return s.replace("’", "'").lower()


def first_edition(root: Path | None, editions: list[str]) -> tuple[str, dict] | None:
    """The edition a memo puts first among `editions` (product ids), with the rule quoted."""
    from ask_soony import api

    labels = api.edition_labels(root)
    for rule in rules(root):
        if rule["kind"] != "priority":
            continue
        head = _norm(RULES["priority"].search(rule["text"]).group(0))
        for ed in editions:
            label = labels.get(ed)
            if label and _norm(label) in head:
                return ed, rule
    return None
