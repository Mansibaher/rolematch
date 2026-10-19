# RoleMatch build progress

Concise record so work can resume across sessions. Newest first.

## Status: all six stages implemented (2026-10-19)

Verified in the build environment:
- Backend: 125 tests pass (2 opt-in live tests skipped); `ruff`; `alembic upgrade/downgrade` and `alembic check`.
- Frontend: `tsc --noEmit`, `next build`; Playwright E2E (upload → review → rank → evidence → apply → OA deadline) passes.
- Evaluation run committed in `eval/results/` (first tuning-only run kept in `eval/results/history/`).
- Screenshots in `docs/screenshots/` captured from the running app (demo account).
- `docker compose config` validates.

Not verified (environment limits; see README "Status"):
- Live Greenhouse/Lever requests (hosts blocked) → run `pytest -m live` and `scripts/record_fixtures.py` on a networked machine.
- Docker image builds / `compose up` (no daemon).
- NumPy MiniLM parity vs sentence-transformers → `scripts/verify_embedding_parity.py` (in CI).
- GitHub Actions workflow has not run.

## Stage log
1. Resume extraction, editable profile, auth, demo account — done.
2. Job storage, Greenhouse/Lever/fixture adapters, ingestion reliability, dedup, manual import, Celery — done.
3. Requirement extraction, eligibility, FTS + pgvector retrieval, evidence reranking, explanations, feed — done.
4. Tracker, stage history, snapshots, tz-aware deadlines, reminders, ICS, analytics, dashboard — done.
5. Evaluation, security checks, tests, E2E, docs, Docker, CI — done.
6. Evidence-grounded tailoring with claim checker; optional opt-in LLM provider — done.

## Ideas for next iterations
- Verify live sources; replace constructed API fixtures with sanitized recordings.
- OCR is intentionally absent; consider an explicit "OCR draft, must review" path.
- Layout-aware PDF parsing for multi-column resumes.
- More labelled personas from real (consented) resumes; second labeller for agreement.
- Email integration for OA/interview detection (future work per brief).
