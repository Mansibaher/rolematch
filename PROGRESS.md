# RoleMatch build progress

Concise record so work can resume across sessions. Newest first.

## Stage 1 — Resume extraction, editable profile (backend) ✅
- Postgres 16 + pgvector schema via Alembic `0001` (up/down verified).
- Sandboxed PDF/DOCX extraction (subprocess, rlimits, timeout, magic bytes, zip-bomb guard), scanned-PDF fallback.
- Heuristic parser with char-offset provenance; skill taxonomy (`config/skills.yaml`) with boundary-safe aliases.
- Overlap-aware experience with min/point/max bounds.
- Auth (Argon2id + opaque server sessions + CSRF header), profile edit API with append-only edit log.
- Tests: 41 passing (`cd backend && pytest`).

## Next
- Stage 2: job sources (Greenhouse, Lever), ingestion, dedup, source health, manual import, fixtures, Celery.
