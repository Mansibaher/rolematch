# Resume bullets

Each bullet describes implemented features and cites results that were actually measured. Keep the qualifiers
("synthetic", "developer-labelled") if you quote the numbers.

**RoleMatch — Explainable job matching and application tracker** | Python, FastAPI, PostgreSQL (FTS + pgvector), Celery, Next.js, TypeScript

- Built an explainable job-matching engine that classifies each posting requirement as supported, partly
  supported, not shown, or unclear and cites the exact resume line behind every match; on a developer-labelled
  synthetic benchmark (5 personas × 36 postings) it reached nDCG@10 0.96 on the held-out split versus 0.84 for
  keyword matching and 0.79 for embedding similarity alone, with 0 of 716 evidence links failing a provenance audit.
- Engineered fault-tolerant ingestion from public Greenhouse and Lever job-board APIs with rate limiting, bounded
  retries, idempotent PostgreSQL upserts, conservative cross-source deduplication, and outage-safe closure
  detection, covered by integration tests against schema-faithful API fixtures (125 backend tests, plus a
  Playwright end-to-end test).
- Implemented a privacy-conscious resume pipeline and tracker: sandboxed PDF/DOCX extraction, an editable profile
  with an audit log of corrections, overlap-aware experience ranges, append-only application history with
  timezone-aware deadlines and calendar export, and a tailoring assistant whose claim checker rejects any added
  skill, metric or name.

Notes for interviews:
- Live Greenhouse/Lever calls were not exercised in the build environment; say "tested against fixtures matching
  the documented schemas" unless you have since run the live smoke test.
- The benchmark labels were written by the system's author; it is a sanity check, not evidence of hiring outcomes.
