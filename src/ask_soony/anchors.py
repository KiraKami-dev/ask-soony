"""
Evidence anchoring: find where a card's numbers and ids actually appear in the portal documents,
down to the PDF page, the scanned word or the spreadsheet row, so the UI can open the file there
and highlight it.

Every portal file is indexed once (cached by content hash under <ASK_DATA_DIR>/anchors/):
- PDF pages with a text layer: words with their boxes (pypdfium2)
- scanned PDF pages and images: words with boxes from local OCR (tesseract; Latin models are
  enough because numbers and ids are written in Latin script in every supplier language)
- CSV / XLSX: one unit per row; JSON, Markdown, text and mail: one unit per line

A card's evidence is turned into search tokens (numbers with thousands separators removed, ids
like PO, shipment or part numbers) and matched against the index; units sharing the most tokens,
boosted by the card's part, shipment and supplier, become anchors. Nothing about Soony is
hardcoded: it works on whatever files the portal holds.
"""

import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

router = APIRouter()
_lock = threading.Lock()
_index: dict[str, list[dict]] = {}  # doc path -> units
_state = {"building": False, "done": False, "files": 0}

TEXT_SUFFIXES = {".json", ".md", ".txt", ".eml", ".html", ".xml"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
_THOUSANDS = re.compile(r"(?<=\d)[,.'   ](?=\d{3}(?!\d))")
_TOKEN = re.compile(r"[A-Za-z0-9]+(?:[-_/][A-Za-z0-9]+)*")


# ---------------------------------------------------------------- tokens


def norm(text: str) -> str:
    """Upper-case with thousands separators removed: '56.000' and '56,000' both become '56000'."""
    prev = None
    s = str(text)
    while prev != s:  # 1,234,567 needs more than one pass
        prev, s = s, _THOUSANDS.sub("", s)
    return s.upper()


def tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(norm(text)))


def search_tokens(text: str) -> dict[str, int]:
    """Tokens worth searching for, with a weight: ids 3, long numbers 2, short numbers 1."""
    out: dict[str, int] = {}
    for t in tokens(text):
        digits = sum(ch.isdigit() for ch in t)
        letters = sum(ch.isalpha() for ch in t)
        if digits and letters and len(t) >= 5:
            out[t] = 3
        elif digits == len(t) and len(t) >= 5:
            out[t] = 2
        elif digits == len(t) and len(t) >= 3 and not (len(t) == 4 and t.startswith("20")):
            out[t] = 1  # skip bare years
    return out


# ---------------------------------------------------------------- indexing


def _portal() -> Path | None:
    from ask_soony import api

    return api.portal_root()


def _cache_dir() -> Path:
    return Path(os.environ.get("ASK_DATA_DIR", "data/store")) / "anchors"


def _tessdata() -> str | None:
    local = Path(os.environ.get("ASK_TESSDATA", "data/ocr/tessdata"))
    return str(local.resolve()) if local.is_dir() else None


def ocr_words(image) -> list[list]:
    """Words with normalised boxes [text, x0, y0, x1, y1] from tesseract, or [] without it."""
    exe = shutil.which("tesseract")
    if not exe:
        return []
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="PNG")
    env = dict(os.environ)
    if _tessdata():
        env["TESSDATA_PREFIX"] = _tessdata()
    langs = os.environ.get("ASK_OCR_LANGS", "eng+pol")
    try:
        out = subprocess.run(
            [exe, "stdin", "stdout", "-l", langs, "-c", "tessedit_create_tsv=1"],
            input=buf.getvalue(),
            capture_output=True,
            timeout=120,
            env=env,
            check=False,
        ).stdout.decode("utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return []
    w, h = image.size
    words = []
    for line in out.splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 12 or parts[0] != "5" or not parts[11].strip():
            continue
        left, top, width, height = (int(p) for p in parts[6:10])
        words.append([parts[11], left / w, top / h, (left + width) / w, (top + height) / h])
    return words


def _pdf_units(path: Path, rel: str) -> list[dict]:
    import pypdfium2 as pdfium

    units = []
    pdf = pdfium.PdfDocument(str(path))
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            pw, ph = page.get_size()
            tp = page.get_textpage()
            words, cur, box = [], "", None
            n = tp.count_chars()
            text = tp.get_text_range(0, n) if n else ""
            for c in range(min(n, len(text))):
                ch = text[c]
                if ch.isspace() or ch == "|":
                    if cur:
                        words.append([cur, *box])
                    cur, box = "", None
                    continue
                left, bottom, right, top = tp.get_charbox(c)
                b = [left / pw, 1 - top / ph, right / pw, 1 - bottom / ph]
                box = (
                    b
                    if box is None
                    else [
                        min(box[0], b[0]),
                        min(box[1], b[1]),
                        max(box[2], b[2]),
                        max(box[3], b[3]),
                    ]
                )
                cur += ch
            if cur:
                words.append([cur, *box])
            source = "text"
            if len(words) < 5:  # a scan: read the rendered page
                words = ocr_words(page.render(scale=2).to_pil())
                source = "ocr"
            units.append(
                {"doc": rel, "kind": "pdf", "page": i + 1, "words": words, "source": source}
            )
    finally:
        pdf.close()
    return units


def _image_units(path: Path, rel: str) -> list[dict]:
    from PIL import Image

    with Image.open(path) as im:
        return [{"doc": rel, "kind": "image", "page": 1, "words": ocr_words(im), "source": "ocr"}]


def _table_units(path: Path, rel: str) -> list[dict]:
    units = []
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", errors="replace", newline="") as fh:
            rows = list(csv.reader(fh))
        sheets = [("", rows)]
    else:
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        sheets = [
            (
                ws.title,
                [["" if v is None else str(v) for v in r] for r in ws.iter_rows(values_only=True)],
            )
            for ws in wb.worksheets
        ]
        wb.close()
    for sheet, rows in sheets:
        for r, cells in enumerate(rows):
            if any(str(c).strip() for c in cells):
                units.append(
                    {
                        "doc": rel,
                        "kind": "table",
                        "sheet": sheet,
                        "row": r,
                        "text": " | ".join(str(c) for c in cells),
                    }
                )
    return units


def _text_units(path: Path, rel: str) -> list[dict]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return [
        {"doc": rel, "kind": "text", "row": i, "text": line}
        for i, line in enumerate(lines)
        if line.strip()
    ]


def index_file(path: Path, rel: str) -> list[dict]:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    cached = _cache_dir() / f"{digest}.json"
    if cached.exists():
        try:
            units = json.loads(cached.read_text())
            for u in units:
                u["doc"] = rel
            return units
        except (OSError, ValueError):
            pass
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            units = _pdf_units(path, rel)
        elif suffix in IMAGE_SUFFIXES:
            units = _image_units(path, rel)
        elif suffix in (".csv", ".xlsx", ".xlsm"):
            units = _table_units(path, rel)
        elif suffix in TEXT_SUFFIXES:
            units = _text_units(path, rel)
        else:
            units = []
    except Exception:  # one unreadable file must not stop the index
        units = []
    for u in units:
        u["tokens"] = sorted(tokens(u.get("text") or " ".join(w[0] for w in u.get("words", []))))
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(units, ensure_ascii=False))
    return units


def build_index() -> None:
    root = _portal()
    if root is None:
        return
    with _lock:
        if _state["building"]:
            return
        _state["building"] = True
    try:
        files = [p for p in sorted(root.rglob("*")) if p.is_file()]
        for p in files:
            rel = str(p.relative_to(root))
            units = index_file(p, rel)
            with _lock:
                _index[rel] = units
                _state["files"] += 1
        with _lock:
            for rel in [r for r in _index if not (root / r).is_file()]:
                del _index[rel]  # removed from the portal
            _state["done"] = True
    finally:
        _state["building"] = False


def ensure_index() -> None:
    if not _state["done"] and not _state["building"]:
        threading.Thread(target=build_index, daemon=True, name="anchor-index").start()


# ---------------------------------------------------------------- matching


def _boxes(unit: dict, wanted: set[str]) -> list[list[float]]:
    out = []
    words = unit.get("words") or []
    for i, w in enumerate(words):
        if tokens(w[0]) & wanted:
            out.append(w[1:5])
        elif (
            i + 1 < len(words)
            and not tokens(words[i + 1][0]) & wanted  # a number split over two words
            and tokens(w[0] + words[i + 1][0]) & wanted
        ):
            out.append(
                [
                    min(w[1], words[i + 1][1]),
                    min(w[2], words[i + 1][2]),
                    max(w[3], words[i + 1][3]),
                    max(w[4], words[i + 1][4]),
                ]
            )
    return out


_WORD = re.compile(r"[^\W\d_]{5,}")


def find(
    text: str, context: str = "", doc: str | None = None, limit: int = 3, prefer: str = ""
) -> list[dict]:
    """
    The best places in the portal documents for this evidence text. Ids and numbers decide;
    shared words (5+ letters) break ties, and `prefer` ("table" for what the system says,
    "document" for what the paper says) favours that kind of source.
    """
    want = search_tokens(text)
    if not want:
        return []
    ctx = set(search_tokens(context)) - set(want)
    words = {w.upper() for w in _WORD.findall(text)}
    with _lock:
        units = [u for d, us in _index.items() if doc is None or d == doc for u in us]
    scored = []
    for u in units:
        have = set(u.get("tokens") or [])
        hit = set(want) & have
        if not hit:
            continue
        score = sum(want[t] for t in hit) + len(ctx & have)
        if score < 2:
            continue
        score += min(3, len(words & have)) * 0.5
        is_table = u["kind"] == "table"
        if (prefer == "table" and is_table) or (prefer == "document" and not is_table):
            score += 1
        scored.append((score, len(hit), u, hit))
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    out, seen = [], set()
    for score, _, u, hit in scored:
        key = (u["doc"], u.get("page"), u.get("sheet"))
        if key in seen:
            continue
        seen.add(key)
        anchor = {"doc": u["doc"], "kind": u["kind"], "score": score, "matched": sorted(hit)}
        if u["kind"] in ("pdf", "image"):
            anchor.update(page=u["page"], source=u.get("source"), boxes=_boxes(u, hit))
            words = u.get("words") or []
            anchor["snippet"] = " ".join(w[0] for w in words if tokens(w[0]) & hit)[:160]
        else:
            anchor.update(row=u["row"], sheet=u.get("sheet"), snippet=u["text"][:200])
        out.append(anchor)
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- routes


def _resolve(path: str) -> Path:
    root = _portal()
    if root is None:
        raise HTTPException(404)
    target = (root / path).resolve()
    if not target.is_file() or root.resolve() not in target.parents:
        raise HTTPException(404)
    return target


@router.get("/api/anchors/{card_id}")
def card_anchors(card_id: str):
    from ask_soony import api

    ensure_index()
    card = api._card(card_id)
    context = " ".join(
        str(card.get(k) or "") for k in ("part", "shipment", "supplier_id", "edition")
    )
    items = []
    for i, e in enumerate(card.get("evidence") or []):
        text = " ".join(str(e.get(k) or "") for k in ("value", "quote", "translation"))
        label = str(e.get("source") or "").lower()
        # What the system says (book stock, plans, ERP) lives in tables; what the paper says in
        # documents. A hint for ranking only; every match is still listed.
        system = re.search(r"\b(system|plan|book|stock|sap|erp)\b", f"{label} {text}".lower())
        prefer = "table" if system else "document"
        items.append(
            {"evidence": i, "anchors": find(text, context, e.get("doc") or None, prefer=prefer)}
        )
    return {"ready": _state["done"], "indexed_files": _state["files"], "items": items}


@router.get("/api/doc/page")
def doc_page(path: str, page: int = 1, width: int = 1400):
    """A PDF page (or an image) as PNG for the highlight viewer."""
    target = _resolve(path)
    buf = io.BytesIO()
    if target.suffix.lower() == ".pdf":
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(target))
        try:
            if not 1 <= page <= len(pdf):
                raise HTTPException(404)
            p = pdf[page - 1]
            scale = max(0.5, min(4.0, width / p.get_size()[0]))
            p.render(scale=scale).to_pil().save(buf, format="PNG")
        finally:
            pdf.close()
    else:
        from PIL import Image

        with Image.open(target) as im:
            im.convert("RGB").save(buf, format="PNG")
    return Response(
        buf.getvalue(), media_type="image/png", headers={"Cache-Control": "private, max-age=3600"}
    )


@router.get("/api/doc/table")
def doc_table(path: str, row: int = 0, sheet: str = "", around: int = 12):
    """The header and the rows around one row of a CSV or XLSX, for the highlight viewer."""
    target = _resolve(path)
    units = [u for u in _table_units(target, path) if (u.get("sheet") or "") == sheet]
    rows = [u["text"].split(" | ") for u in units]
    numbers = [u["row"] for u in units]
    if not rows:
        raise HTTPException(404)
    pos = next((i for i, n in enumerate(numbers) if n == row), 0)
    lo, hi = max(1, pos - around), min(len(rows), pos + around + 1)
    return {
        "header": rows[0],
        "rows": [
            {"row": numbers[i], "cells": rows[i], "hit": numbers[i] == row} for i in range(lo, hi)
        ],
        "sheet": sheet,
    }
