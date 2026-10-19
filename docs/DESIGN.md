# RoleMatch design

RoleMatch answers "which jobs should I prioritise, and what in my resume supports that?" Every design choice
below follows from one rule: **never claim more than the documents establish.**

## 1. Data model and provenance

```
User ─┬─ Preferences (filters, hard/soft flags, bounded feedback nudges)
      ├─ WorkEligibility (private: sponsorship need, citizenship-exclusion flag)
      ├─ Resume ── ResumeVersion (immutable text + page/paragraph segments, origin, evidence_eligible)
      │               ├─ ProfileEntry (employment | project | education | activity; offsets; original extraction)
      │               ├─ ProfileSkill (canonical id; every mention with offset + context; or user_added)
      │               └─ ProfileEditLog (append-only before/after of each correction)
      └─ Application ─┬─ ApplicationEvent (append-only stage history)
                      ├─ Deadline (UTC instant + the IANA zone it was entered in)
                      └─ job_snapshot, resume_version_id
JobSource ── SourceRun (health) ── Job (source id, timestamps, status, extraction, embedding, dedup link)
```

- **Resume versions are immutable.** A new upload is a new version; a correction edits the *profile layer* of a
  version, never its text, and keeps the parser's original output in `ProfileEntry.extracted` plus a log row.
- **Every claim points at text.** Skills store each mention as a character span plus where it occurred
  (work, project, skills list, education…). Evidence shown on a job is the literal resume line containing that
  span; for PDFs/DOCX the span also maps to a page or paragraph. User-added skills are labelled
  "added by you, not in resume text" everywhere they appear.
- **Tailored versions are quarantined.** `evidence_eligible = false`, no profile rows, cannot be reviewed into the
  matching profile, cannot be edited. Matching always reads the reviewed, evidence-eligible version.

## 2. Resume extraction

1. Validation: extension allow-list, magic bytes (`%PDF-`, `PK\x03\x04`), 5 MB limit, DOCX zip-bomb guard
   (entry count and uncompressed size).
2. Extraction runs in a separate `python -I` process with `RLIMIT_AS` 768 MB, a CPU limit, no file writes, and a
   wall-clock timeout. A hostile or broken file can fail, but cannot hang or exhaust the API.
3. Scanned PDFs (little or no text layer) return `needs_text` and the UI asks for DOCX or pasted text. There is
   no OCR: OCR errors would become fabricated evidence.
4. The parser finds section headings, groups lines into entries, parses date ranges with explicit precision
   (month / year / season), and tags skill mentions with the taxonomy. Anything it can't decide is left blank
   with a review flag ("could not tell title from organization", "research role counted as employment —
   change it if unpaid").

**Experience.** Only `employment` entries count. Intervals are merged before summing (no double counting).
Each interval has lower/upper bounds from its date precision, so "2018 – 2020" is reported as 14–36 months, not a
fake point value. Internships are reported separately; research and part-time roles mark the total uncertain.

## 3. Skill taxonomy

`config/skills.yaml` (111 skills) maps aliases to canonical ids with *technical* word boundaries — `+`, `#` and
`_` count as word characters, so `C` never matches inside `C++`/`C#` and `Java` never matches inside
`JavaScript`. Case-sensitive patterns handle `Go`, `R`, `C`, `React` ("react to incidents" is not React).
Overlaps resolve longest-first (`React Native` over `React`, `PL/SQL` over `SQL`). `implies` edges
(PostgreSQL → SQL, FastAPI → Python) give **partial** evidence only. Tests pin these edge cases.

## 4. Job ingestion and source reliability

- Adapters call only fixed, documented hosts with validated identifiers; redirects are not followed.
- Per-host politeness delay, connect/read timeouts, ≤3 retries with exponential backoff and jitter for timeouts,
  connection errors, 429 (honouring `Retry-After`), 5xx and Lever's occasional HTML-instead-of-JSON. 404/401/403
  fail fast and show in source health.
- `INSERT … ON CONFLICT (source_id, source_job_id) DO UPDATE` makes ingestion idempotent; `first_seen_at` is
  never overwritten; `status_changed_at` only moves on a real change. `SourceRun.run_key` is the Celery task id,
  so a re-delivered task returns the recorded run instead of fetching again. A Redis lock prevents concurrent
  runs of one source.
- **Closing is outage-safe:** only a *complete* listing (Greenhouse `meta.total` matches; Lever paginated to the
  end) can count a job as missing, and two consecutive complete misses are required. Failed and partial runs
  change nothing. Jobs not re-verified within 72 h are shown as "not re-checked recently" and lose freshness.
- **Dedup is conservative.** Never within a source; never across different requisition ids; merges on the same
  canonical URL, or on identical employer/title/location with ≥0.85 description overlap *only if* there is exactly
  one such look-alike and none in the job's own source (several look-alikes mean several openings).
  Duplicates link to the canonical job, which shows "also listed via …".
- **Dates:** posted date only from the source (`first_published`; Lever `createdAt` labelled as creation time).
  Greenhouse `updated_at` is stored separately and never shown as a posting date.

## 5. Matching and ranking

### Requirement extraction (at ingestion)
Headings classify items as required / preferred / responsibilities / other (benefits, about-the-company);
inline cues ("is a plus", "required", "you should") refine or classify unstructured text; plain-text list markers
and "Requirements: …" prefixes are normalised. Each item records how it was classified
(`confidence: high | medium | low`). Unstructured postings are flagged as uncertain. Eligibility statements —
sponsorship, citizenship/clearance, location requirements — are extracted **only from explicit text**; silence is
`unknown`. Text aimed at automated screeners ("ignore previous instructions…") is flagged for the user and the
sentence is removed before extraction and embedding, so it cannot affect anything.

### Eligibility and filters
Each preference check returns pass / fail / unknown with a reason and evidence. Exclusions are always hard; other
filters are hard only if the user says so. **A hard filter excludes only on an explicit fail.** Unknowns on a hard
filter keep the job visible and flagged. Hidden jobs remain inspectable, and an empty feed explains which filter
removed how many jobs and suggests changes — filters are never relaxed automatically.

### Candidate retrieval
PostgreSQL full-text search (title weighted above description, `websearch_to_tsquery` over the profile's top
skills) and pgvector HNSW cosine search over job embeddings, unioned (top 300 each), plus all of the user's manual
jobs. At personal scale this retrieves everything; it exists so ranking stays bounded as watchlists grow.

### Evidence evaluation per requirement
| Requirement type | Supported when | Partly supported when |
|---|---|---|
| Skills (all of) | every skill is shown in a role, project, or verified by the user | some skills shown, or listed only, or implied |
| Skills (any of: "Python, Java, or Go") | any option is shown | an option is listed/implied |
| Years ("3+ years") | non-internship experience lower bound ≥ N, domain-aware ("product management", "data engineering") | upper bound ≥ N under uncertain dates, or only with internships, or related skills under different titles |
| Years + skill ("3+ years of Python") | roles mentioning the skill span ≥ N years | the skill is shown but for fewer years |
| Degree | completed degree at the level, in a related field | in progress, unrelated field, or "equivalent experience" accepted |
| Enrollment / graduation window | an in-progress degree / graduation year in range | — |

Anything checkable with no evidence is **not shown on resume** (never "you lack this"). Vague items are
**unclear**. If no explicit evidence exists but a resume line is semantically similar (MiniLM cosine ≥ 0.55), the
status becomes *unclear* with that line shown as "possibly related — similarity is not proof". Semantic similarity
never produces *supported*.

### Scores (`config/scoring.yaml`)
**Qualification match** (0–100) = weighted mean of available components, renormalised when one is undefined:

| Component | Weight | Definition |
|---|---|---|
| Required coverage | 0.50 | mean credit over required (and unlabelled) qualifications: supported 1, partly 0.5, unclear 0.5, not shown 0 |
| Preferred coverage | 0.15 | same over preferred qualifications |
| Evidence depth | 0.20 | 0.7 × mean strength of matched skill evidence (work 1.0, project 0.75, user-verified 0.7, listed 0.5; implied ×0.5) + 0.3 × resume↔posting cosine mapped from [0.15, 0.65] to [0, 1] |
| Seniority | 0.15 | compatibility of the posting's level (title or required years) with the experience range; uses the favourable end of an uncertain range |

**Preference fit** (0–100) = mean of soft checks (pass 1, unknown 0.5, fail 0) plus bounded feedback nudges.
**Freshness** = 0.5^(age/10 days) from the posted date, or first-seen date when the source gives none; ×0.6 if not
re-verified within 72 h; 0 if closed.
**Application priority** = 0.65 × qualification + 0.20 × preference fit + 0.15 × freshness.

Freshness never enters the qualification match. None of these is a probability; the UI says so next to every score.

**Feedback** ("relevant / not relevant") adds ±0.05 per vote to the job's role families and employer, capped at
±0.15, and only inside preference fit. It cannot change eligibility, evidence or qualification scores (tested).

## 6. Application tracking
Statuses are the user's own pipeline, independent of whether a source still lists the job. Every change appends
an event (future dates rejected). The first transition into a submitted status records the resume version used
and freezes a snapshot of the posting; later edits by the employer are detected and flagged, but the snapshot is
unchanged. Deadlines are entered as wall-clock time in an IANA zone, stored as UTC instants, displayed in the
user's zone, and exported to `.ics` with `TZID`. Times that don't exist (DST gaps) are rejected rather than shifted.
Opening the employer's link never changes status; there is no automated applying, messaging or e-mail.

**Analytics** are descriptive counts with denominators, medians with sample sizes and an observation window, and
explicit warnings about small samples and selection bias. Silence counts as pending, not rejection. No model is
trained on outcomes.

## 7. Tailoring
Bullets from the verified profile are ranked by how many posting requirements their skills (or implications)
touch plus semantic similarity. Rewording is conservative and rule-based: align a skill's surface form with the
posting's term (Postgres → PostgreSQL), name an implied skill the posting asks for ("FastAPI (Python)"), drop
filler openings. **Every** candidate — rule-based, user-edited, or from the optional LLM — must pass a claim
checker against its source line: no new skills beyond taxonomy implications of skills already in that line, no new
numbers, no new capitalised names. Requirements that can't be supported are listed, never papered over.

## 8. Privacy and security
- Argon2id password hashing; opaque 256-bit session tokens stored as SHA-256; httpOnly SameSite=Lax cookies
  (`Secure` in production); state-changing requests require a custom header (CSRF defence); login throttling.
- Ownership checks on every user-scoped record; other users' ids return 404. Manual jobs are private to their owner.
- Work authorization lives in a separate table, is returned only to its owner, never logged, never sent to an
  external provider, and only used against explicit posting statements.
- Logs never include request bodies; a filter redacts e-mails, phone numbers and session tokens.
- URL fetching: only Greenhouse/Lever links are fetched, via their fixed API hosts with validated identifiers;
  other URLs are stored, never fetched. A guard rejects private, loopback, link-local, metadata and reserved
  addresses for any user-influenced outbound request.
- Account deletion removes files on disk and all personal rows (tested).
- Optional LLM: off by default, needs server config **and** per-user opt-in; sends only the bullets being reworded
  and requirement sentences, redacted; documents are passed as quoted JSON data; output must match a strict schema
  and pass the claim checker.

## 9. Trade-offs and known limits
- **Rules over a model for extraction.** Deterministic, testable, explainable, and free — but brittle on unusual
  resume layouts (multi-column PDFs, tables) and on postings without structure. The review screen is the
  mitigation; extraction is never trusted silently.
- **Small embedding model** (MiniLM, 384-d) run with NumPy: no PyTorch in the image, ~10 ms/sentence on CPU,
  modest semantic quality. It is one signal, never proof.
- **Coverage is opt-in.** Only followed employers on two ATS platforms plus manual entry. That is a deliberate
  consequence of respecting source terms.
- **Weights are hand-set**, not learned. With one person's outcomes there is not enough data to learn them
  honestly; they are documented and configurable instead.
- **Evaluation is synthetic and developer-labelled** (see `eval/README.md`).
- In-process login throttling and per-worker rate limiting assume a small deployment; use a shared store
  before scaling out.
