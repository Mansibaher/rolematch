"""Optional live smoke test against the real public posting APIs.

Skipped unless ROLEMATCH_LIVE_TESTS=1. Set ROLEMATCH_LIVE_GREENHOUSE_BOARD / ROLEMATCH_LIVE_LEVER_SITE to boards
you know exist. These tests only check that the documented shape still parses; they never write to the database.
"""

import os

import pytest

from app.services.jobs.adapters.greenhouse import GreenhouseAdapter
from app.services.jobs.adapters.lever import LeverAdapter

pytestmark = [pytest.mark.live, pytest.mark.skipif(os.environ.get("ROLEMATCH_LIVE_TESTS") != "1", reason="live tests are opt-in")]


@pytest.mark.skipif(not os.environ.get("ROLEMATCH_LIVE_GREENHOUSE_BOARD"), reason="no Greenhouse board configured")
def test_greenhouse_live():
    res = GreenhouseAdapter().fetch_all(os.environ["ROLEMATCH_LIVE_GREENHOUSE_BOARD"], "Live test")
    assert res.jobs, "board returned no jobs"
    j = res.jobs[0]
    assert j.source_job_id and j.title and j.apply_url
    assert j.source_updated_at is not None


@pytest.mark.skipif(not os.environ.get("ROLEMATCH_LIVE_LEVER_SITE"), reason="no Lever site configured")
def test_lever_live():
    res = LeverAdapter().fetch_all(os.environ["ROLEMATCH_LIVE_LEVER_SITE"], "Live test")
    assert res.jobs and res.complete
    assert res.jobs[0].apply_url
