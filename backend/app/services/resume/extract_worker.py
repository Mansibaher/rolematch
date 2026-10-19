"""Isolated document-text extraction worker.

Run as a separate process (``python -I extract_worker.py <kind> <max_pages>``) with the document bytes on stdin.
It imports only pypdf / python-docx and prints a JSON result on stdout. The parent process applies a timeout
and resource limits, so a malformed or hostile file cannot hang or exhaust the API process.
"""

from __future__ import annotations

import io
import json
import re
import sys
import zipfile

MAX_DOCX_UNCOMPRESSED = 40 * 1024 * 1024
MAX_DOCX_ENTRIES = 2000


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ").replace("\t", "  ")
    text = re.sub(r"[​‌‍﻿]", "", text)
    # Normalise common bullet glyphs (including private-use glyphs from icon fonts) to "•".
    text = re.sub(r"^[ \t]*[•●▪■‣⁃∙◦➢–►➔❖◆♦\-\*][ \t]+", "• ", text, flags=re.M)
    # Re-join words hyphenated across line breaks ("distri-\nbuted").
    text = re.sub(r"([a-z])-\n([a-z])", r"\1\2", text)
    text = re.sub(r"[ \t]{2,}", "  ", text)
    lines = [ln.rstrip() for ln in text.split("\n")]
    return "\n".join(lines).strip("\n")


def extract_pdf(data: bytes, max_pages: int) -> dict:
    from pypdf import PdfReader

    warnings: list[str] = []
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            if not reader.decrypt(""):
                return {"status": "failed", "error": "encrypted_pdf", "text": "", "segments": [], "warnings": []}
        except Exception:
            return {"status": "failed", "error": "encrypted_pdf", "text": "", "segments": [], "warnings": []}
    n_pages = len(reader.pages)
    if n_pages > max_pages:
        warnings.append(f"Only the first {max_pages} of {n_pages} pages were read.")
    parts: list[str] = []
    segments: list[dict] = []
    pos = 0
    image_pages = 0
    for i, page in enumerate(reader.pages[:max_pages]):
        try:
            raw = page.extract_text() or ""
        except Exception:
            raw = ""
            warnings.append(f"Page {i + 1} could not be read.")
        try:
            if not raw.strip() and page.images:
                image_pages += 1
        except Exception:
            pass
        cleaned = _clean(raw)
        if parts:
            parts.append("\n\n")
            pos += 2
        segments.append({"kind": "page", "index": i, "start": pos, "end": pos + len(cleaned)})
        parts.append(cleaned)
        pos += len(cleaned)
    text = "".join(parts)
    visible = len(re.sub(r"\s", "", text))
    pages_read = min(n_pages, max_pages)
    if visible < 80 * max(1, pages_read) * 0.5:
        reason = "The PDF contains little or no selectable text"
        if image_pages:
            reason += " and appears to be scanned images"
        return {
            "status": "needs_text",
            "error": "no_text_layer",
            "text": text,
            "segments": segments,
            "warnings": warnings + [reason + ". Upload a DOCX or text-based PDF, or paste your resume text."],
        }
    return {"status": "partial" if warnings else "ok", "text": text, "segments": segments, "warnings": warnings}


def extract_docx(data: bytes) -> dict:
    # Zip-bomb and structure guards before python-docx touches the archive.
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return {"status": "failed", "error": "invalid_docx", "text": "", "segments": [], "warnings": []}
    infos = zf.infolist()
    if len(infos) > MAX_DOCX_ENTRIES or sum(i.file_size for i in infos) > MAX_DOCX_UNCOMPRESSED:
        return {"status": "failed", "error": "docx_too_large_uncompressed", "text": "", "segments": [], "warnings": []}
    if "word/document.xml" not in zf.namelist():
        return {"status": "failed", "error": "invalid_docx", "text": "", "segments": [], "warnings": []}

    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = docx.Document(io.BytesIO(data))
    blocks: list[tuple[str, str]] = []  # (kind, text)

    def para_text(p: Paragraph) -> str:
        t = p.text
        style = (p.style.name if p.style is not None else "") or ""
        if t.strip() and ("List" in style or p._p.pPr is not None and p._p.pPr.numPr is not None):
            t = "• " + t.strip()
        return t

    body = document.element.body
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            blocks.append(("paragraph", para_text(Paragraph(child, document))))
        elif tag == "tbl":
            table = Table(child, document)
            for row in table.rows:
                seen: set[int] = set()
                cells = []
                for cell in row.cells:
                    if id(cell._tc) in seen:
                        continue
                    seen.add(id(cell._tc))
                    cells.append(" ".join(p.text for p in cell.paragraphs).strip())
                blocks.append(("table_row", "  ".join(c for c in cells if c)))
    parts: list[str] = []
    segments: list[dict] = []
    pos = 0
    para_index = 0
    for kind, raw in blocks:
        cleaned = _clean(raw)
        if not cleaned.strip():
            para_index += 1
            continue
        if parts:
            parts.append("\n")
            pos += 1
        segments.append({"kind": kind, "index": para_index, "start": pos, "end": pos + len(cleaned)})
        parts.append(cleaned)
        pos += len(cleaned)
        para_index += 1
    text = "".join(parts)
    if len(text.strip()) < 40:
        return {"status": "needs_text", "error": "empty_document", "text": text, "segments": segments,
                "warnings": ["The document contains almost no text. Paste your resume text instead."]}
    return {"status": "ok", "text": text, "segments": segments, "warnings": []}


def main() -> None:
    kind = sys.argv[1]
    max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    data = sys.stdin.buffer.read()
    try:
        if kind == "pdf":
            result = extract_pdf(data, max_pages)
        elif kind == "docx":
            result = extract_docx(data)
        else:
            result = {"status": "failed", "error": "unsupported_type", "text": "", "segments": [], "warnings": []}
    except Exception as exc:  # report the class only; never echo document content
        result = {"status": "failed", "error": f"parse_error:{type(exc).__name__}", "text": "", "segments": [], "warnings": []}
    sys.stdout.write(json.dumps(result))


if __name__ == "__main__":
    main()
