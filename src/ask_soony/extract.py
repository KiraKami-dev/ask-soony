"""
Turn any portal file into something a model can read: text where the file has text, page images
where it does not (scans, photos). The parsers make no assumption about column names, delimiters
or languages; they only make the content readable.
"""

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath

TEXT_SUFFIXES = {".csv", ".tsv", ".txt", ".md", ".json", ".xml", ".log", ".eml"}
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
MAX_IMAGE_SIDE = 1600
MAX_PDF_IMAGE_PAGES = 4
# A PDF page with less extracted text than this is treated as a scan.
MIN_TEXT_PER_PAGE = 40


@dataclass
class Image:
    media_type: str
    data: bytes


@dataclass
class Extracted:
    kind: str
    text: str
    images: list[Image] = field(default_factory=list)

    @property
    def needs_vision(self) -> bool:
        return bool(self.images)


def decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1250", "latin-1"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if encoding == "utf-16" and "\x00" in text:
            continue
        return text
    return data.decode("utf-8", errors="replace")


def extract(path: str, data: bytes, render_images: bool = True) -> Extracted:
    """Parse a file; with render_images=False scans and photos come back without page images."""
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return Extracted(kind=suffix.lstrip("."), text=decode_text(data))
    if suffix in {".xlsx", ".xlsm"}:
        return Extracted(kind="xlsx", text=_xlsx_text(data))
    if suffix == ".docx":
        return Extracted(kind="docx", text=_docx_text(data))
    if suffix == ".pdf":
        return _pdf(data, render_images)
    if suffix in IMAGE_TYPES:
        images = [_fit_image(data, IMAGE_TYPES[suffix])] if render_images else []
        return Extracted(kind="image", text="", images=images)
    return Extracted(
        kind="binary", text=f"[unsupported file type {suffix or '(none)'}, {len(data)} bytes]"
    )


def _xlsx_text(data: bytes) -> str:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    parts = []
    for ws in wb.worksheets:
        parts.append(f"## Sheet: {ws.title}")
        for row in ws.iter_rows(values_only=True):
            if any(c is not None for c in row):
                parts.append(" | ".join("" if c is None else str(c) for c in row))
        for row in ws.iter_rows():
            for cell in row:
                if cell.comment:
                    parts.append(f"[comment {cell.coordinate}] {cell.comment.text}")
    return "\n".join(parts)


def _docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    return re.sub(r"<[^>]+>", "", xml).strip()


def _pdf(data: bytes, render_images: bool) -> Extracted:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = [(page.extract_text() or "").strip() for page in reader.pages]
    text = "\n\n".join(f"--- page {i + 1} ---\n{t}" for i, t in enumerate(pages))
    scanned = [i for i, t in enumerate(pages) if len(t) < MIN_TEXT_PER_PAGE]
    images = (
        _render_pdf_pages(data, scanned[:MAX_PDF_IMAGE_PAGES]) if scanned and render_images else []
    )
    if scanned and len(scanned) > MAX_PDF_IMAGE_PAGES:
        text += f"\n[{len(scanned) - MAX_PDF_IMAGE_PAGES} more scanned pages not rendered]"
    return Extracted(kind="pdf", text=text, images=images)


def _render_pdf_pages(data: bytes, page_indexes: list[int]) -> list[Image]:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    images = []
    for i in page_indexes:
        pil = pdf[i].render(scale=1.5).to_pil()
        images.append(_pil_to_image(pil))
    return images


def _fit_image(data: bytes, media_type: str) -> Image:
    from PIL import Image as PILImage

    pil = PILImage.open(io.BytesIO(data))
    if max(pil.size) <= MAX_IMAGE_SIDE and media_type in {"image/png", "image/jpeg"}:
        return Image(media_type, data)
    return _pil_to_image(pil)


def _pil_to_image(pil) -> Image:
    pil = pil.convert("RGB")
    pil.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    buf = io.BytesIO()
    pil.save(buf, format="JPEG", quality=85)
    return Image("image/jpeg", buf.getvalue())
