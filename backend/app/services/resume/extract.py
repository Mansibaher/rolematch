"""Upload validation and sandboxed text extraction for resumes."""

from __future__ import annotations

import json
import re
import resource
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import get_settings

WORKER = Path(__file__).with_name("extract_worker.py")

ALLOWED = {
    "pdf": {"extensions": {".pdf"}, "content_types": {"application/pdf"}},
    "docx": {
        "extensions": {".docx"},
        "content_types": {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/octet-stream",
        },
    },
}

ERROR_MESSAGES = {
    "too_large": "The file is larger than the {limit_mb} MB limit.",
    "empty_file": "The file is empty.",
    "unsupported_type": "Only PDF and DOCX resumes are supported. For other formats, paste the text instead.",
    "type_mismatch": "The file contents do not match its extension. Only genuine PDF or DOCX files are accepted.",
    "encrypted_pdf": "The PDF is password-protected. Remove the password or upload a DOCX.",
    "invalid_docx": "The DOCX file is damaged or not a Word document.",
    "docx_too_large_uncompressed": "The DOCX file expands to an unexpectedly large size and was rejected.",
    "timeout": "Reading the file took too long and was stopped. Try a simpler PDF/DOCX or paste the text.",
    "no_text_layer": "No selectable text was found (the PDF may be a scan). Upload a DOCX or text-based PDF, or paste the text.",
    "empty_document": "The document contains almost no text. Paste your resume text instead.",
}


class UploadRejected(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ExtractionResult:
    status: str  # ok | partial | needs_text | failed
    text: str
    segments: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


def detect_kind(filename: str, content_type: str | None, data: bytes) -> str:
    settings = get_settings()
    if not data:
        raise UploadRejected("empty_file", ERROR_MESSAGES["empty_file"])
    if len(data) > settings.max_upload_bytes:
        raise UploadRejected("too_large", ERROR_MESSAGES["too_large"].format(limit_mb=settings.max_upload_bytes // (1024 * 1024)))
    ext = Path(filename or "").suffix.lower()
    kind = next((k for k, v in ALLOWED.items() if ext in v["extensions"]), None)
    if kind is None:
        raise UploadRejected("unsupported_type", ERROR_MESSAGES["unsupported_type"])
    # Magic bytes decide, not the client-declared content type.
    if kind == "pdf" and not data[:1024].lstrip().startswith(b"%PDF-"):
        raise UploadRejected("type_mismatch", ERROR_MESSAGES["type_mismatch"])
    if kind == "docx" and not data.startswith(b"PK\x03\x04"):
        raise UploadRejected("type_mismatch", ERROR_MESSAGES["type_mismatch"])
    return kind


def _limit_resources() -> None:  # runs in the child before exec
    mem = 768 * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    cpu = get_settings().extraction_timeout_seconds + 5
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))  # the worker never needs to write files


def extract_text(kind: str, data: bytes) -> ExtractionResult:
    settings = get_settings()
    try:
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [sys.executable, "-I", str(WORKER), kind, str(settings.max_pdf_pages)],
            input=data,
            capture_output=True,
            timeout=settings.extraction_timeout_seconds,
            preexec_fn=_limit_resources,  # noqa: PLW1509
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ExtractionResult("failed", "", error="timeout", warnings=[ERROR_MESSAGES["timeout"]])
    if proc.returncode != 0 or not proc.stdout:
        return ExtractionResult("failed", "", error="worker_failed", warnings=["The file could not be read. Try another format or paste the text."])
    out = json.loads(proc.stdout)
    error = out.get("error")
    warnings = list(out.get("warnings") or [])
    if out["status"] == "failed" and error in ERROR_MESSAGES and not warnings:
        warnings = [ERROR_MESSAGES[error]]
    elif out["status"] == "failed" and not warnings:
        warnings = ["The file could not be read. Try another format or paste the text."]
    return ExtractionResult(out["status"], out.get("text", ""), out.get("segments", []), warnings, error)


def normalize_pasted_text(text: str) -> ExtractionResult:
    """Pasted resume text goes through the same normalisation as extracted text."""
    from app.services.resume.extract_worker import _clean

    cleaned = _clean(text)
    if len(re.sub(r"\s", "", cleaned)) < 40:
        return ExtractionResult("needs_text", cleaned, warnings=[ERROR_MESSAGES["empty_document"]], error="empty_document")
    lines = cleaned.split("\n")
    segments, pos = [], 0
    for i, ln in enumerate(lines):
        segments.append({"kind": "line", "index": i, "start": pos, "end": pos + len(ln)})
        pos += len(ln) + 1
    return ExtractionResult("ok", cleaned, segments)


def locate(segments: list[dict], start: int) -> dict | None:
    """Map a character offset in raw_text back to a page / paragraph for evidence references."""
    for seg in segments:
        if seg["start"] <= start <= seg["end"]:
            return {"kind": seg["kind"], "index": seg["index"]}
    return None
