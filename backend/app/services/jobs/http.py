"""HTTP client for job sources: per-host politeness delay, timeouts, bounded retries with backoff.

Retries happen only for transient conditions (timeouts, connection errors, 429, 5xx, and Lever's documented
habit of occasionally returning HTML instead of JSON). Everything else fails fast so a broken source is
reported in source health instead of being hammered.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)

_last_request: dict[str, float] = {}
_lock = threading.Lock()


class SourceError(Exception):
    """A failure talking to a job source. `kind` is a short machine-readable reason for source health."""

    def __init__(self, kind: str, detail: str = "", status: int | None = None):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail
        self.status = status


@dataclass
class Counter:
    requests: int = 0


def _wait_turn(host: str, min_interval: float) -> None:
    with _lock:
        now = time.monotonic()
        wait = _last_request.get(host, 0.0) + min_interval - now
        _last_request[host] = max(now, _last_request.get(host, 0.0) + min_interval)
    if wait > 0:
        time.sleep(wait)


def get_json(client: httpx.Client, url: str, params: dict | None = None, counter: Counter | None = None) -> object:
    s = get_settings()
    host = urlsplit(url).hostname or ""
    attempts = s.http_max_retries + 1
    last: SourceError | None = None
    for attempt in range(attempts):
        _wait_turn(host, s.http_min_interval_seconds)
        if counter:
            counter.requests += 1
        try:
            r = client.get(url, params=params)
        except httpx.TimeoutException as exc:
            last = SourceError("timeout", type(exc).__name__)
        except httpx.TransportError as exc:
            last = SourceError("connection_error", type(exc).__name__)
        else:
            if r.status_code == 404:
                raise SourceError("not_found", "board or posting not found (check the identifier)", 404)
            if r.status_code in (401, 403):
                raise SourceError("forbidden", "the source refused access", r.status_code)
            if r.status_code == 429 or r.status_code >= 500:
                last = SourceError("rate_limited" if r.status_code == 429 else "server_error", f"HTTP {r.status_code}", r.status_code)
                retry_after = r.headers.get("Retry-After")
                if retry_after and retry_after.isdigit() and attempt < attempts - 1:
                    time.sleep(min(int(retry_after), 60))
                    continue
            elif r.status_code != 200:
                raise SourceError("http_error", f"HTTP {r.status_code}", r.status_code)
            else:
                ctype = r.headers.get("content-type", "")
                if "json" not in ctype:
                    last = SourceError("unexpected_content_type", ctype[:60], r.status_code)
                else:
                    try:
                        return r.json()
                    except ValueError:
                        last = SourceError("invalid_json", "", r.status_code)
        if attempt < attempts - 1:
            time.sleep(min(30.0, (2**attempt) * 0.5 + random.uniform(0, 0.25)))
    assert last is not None
    raise last


def make_client() -> httpx.Client:
    s = get_settings()
    return httpx.Client(
        timeout=httpx.Timeout(s.http_timeout_seconds, connect=s.http_connect_timeout_seconds),
        headers={"User-Agent": s.user_agent, "Accept": "application/json"},
        follow_redirects=False,  # adapters only call fixed, documented API hosts
    )
