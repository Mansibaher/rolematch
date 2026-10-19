# API response fixtures

**Provenance: constructed, not captured.** These files reproduce the *documented response shapes* of the
Greenhouse Job Board API (https://developers.greenhouse.io/job-board.html, checked 2026-10-19) and the Lever
Postings API (github.com/lever/postings-api README). Live capture was not possible in the build environment
(outbound access to those hosts was blocked), so the content is synthetic and sanitized by construction:
fictional employers, example.com/fake board URLs, no personal data.

They are used by `backend/tests/test_sources.py` through mocked HTTP (respx). To replace them with real,
sanitized recordings from a networked machine:

    cd backend && python scripts/record_fixtures.py --greenhouse <board_token> --lever <site>

The script strips application questions, recruiter names and e-mail addresses before writing.
The opt-in live smoke test (`ROLEMATCH_LIVE_TESTS=1 pytest -m live`) checks the real endpoints.
