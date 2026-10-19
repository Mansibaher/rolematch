"""Logging with PII redaction.

RoleMatch never logs request bodies. As a second line of defence, every log record passes through a filter
that masks e-mail addresses, phone numbers and session tokens.
"""

from __future__ import annotations

import logging
import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d{1,2}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
_TOKEN = re.compile(r"(rm_session=)[^;\s]+")


def redact(text: str) -> str:
    text = _EMAIL.sub("[email]", text)
    text = _PHONE.sub("[phone]", text)
    return _TOKEN.sub(r"\1[redacted]", text)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.access":
            return True  # method/path/status only; its formatter needs the original args
        try:
            msg = record.getMessage()
        except Exception:
            return True
        record.msg = redact(msg)
        record.args = ()
        return True


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # Access logs record method, path and status only (no bodies). Uvicorn's access formatter needs the raw
    # record args, so it is left unfiltered; API paths never contain personal data (only opaque UUIDs).
