"""Record sanitized real responses from the Greenhouse and Lever public posting APIs as test fixtures.

    python scripts/record_fixtures.py --greenhouse <board_token> --lever <site> [--limit 5]

Writes fixtures/recorded/greenhouse_list_<token>.json and lever_page1_<site>.json, keeping the documented field
structure but removing application questions, e-mail addresses and phone numbers, and truncating to --limit jobs.
Only public, unauthenticated GET endpoints are called. Review the files before committing them.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parents[2] / "fixtures" / "recorded"
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")


def scrub(obj):
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items() if k not in ("questions", "location_questions", "compliance")}
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    if isinstance(obj, str):
        return PHONE.sub("[phone]", EMAIL.sub("[email]", obj))
    return obj


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--greenhouse")
    ap.add_argument("--lever")
    ap.add_argument("--limit", type=int, default=5)
    a = ap.parse_args()
    with httpx.Client(timeout=30, headers={"User-Agent": "RoleMatch fixture recorder"}) as c:
        if a.greenhouse:
            data = c.get(f"https://boards-api.greenhouse.io/v1/boards/{a.greenhouse}/jobs", params={"content": "true"}).raise_for_status().json()
            data["jobs"] = data["jobs"][: a.limit]
            data["meta"] = {"total": len(data["jobs"])}
            (OUT / f"greenhouse_list_{a.greenhouse}.json").write_text(json.dumps(scrub(data), indent=2))
        if a.lever:
            data = c.get(f"https://api.lever.co/v0/postings/{a.lever}", params={"mode": "json", "limit": a.limit}).raise_for_status().json()
            (OUT / f"lever_page1_{a.lever}.json").write_text(json.dumps(scrub(data[: a.limit]), indent=2))
    print("written to", OUT)


if __name__ == "__main__":
    main()
