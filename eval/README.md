# Offline evaluation

`python eval/run_eval.py` (from `backend/` with its virtualenv) rebuilds an isolated database
(`ROLEMATCH_EVAL_DATABASE_URL`, default `rolematch_eval`), ingests the 36 synthetic fixture postings with real
all-MiniLM-L6-v2 embeddings, creates five synthetic personas (`personas/`), and writes
`results/results.json` and `results/results.md`. Latest committed results: [results/results.md](results/results.md).

## What is compared

| Ranker | Score |
|---|---|
| keyword | share of the posting's skill keywords that appear as substrings of the resume (naive: "java" matches inside "javascript") |
| semantic | cosine similarity of whole-resume and whole-posting embeddings |
| hybrid | RoleMatch's qualification match: requirement coverage + evidence depth + seniority (+ semantic as one signal) |

Relevance labels (0–3, rubric in `labels.yaml`) cover qualification fit only. Eligibility filters are
evaluated separately against an independent oracle that reads fixture metadata, not engine output.

## Protocol

1. Labels, personas and the job split (`split` field in `fixtures/jobs/synthetic_jobs.json`) were written before
   the first run.
2. First run on the **tuning split only**, kept in `results/history/`. It exposed extraction bugs on the
   development extraction set; a second, held-out extraction set was written *before* fixing them.
3. Extractor fixes were made on general grounds. Ranking weights in `config/scoring.yaml` were not changed.
4. One final run reports tune, held-out and combined splits.

## Limitations — read before quoting any number

- **Synthetic and small**: 5 personas × 36 fictional postings; 3–20 labelled-relevant jobs per persona. Several
  personas have only one or two relevant jobs in a split (e.g. the frontend persona's held-out split has one),
  which makes P@5 and nDCG coarse.
- **Labeller = developer**: the person who wrote the matcher wrote the labels, which favours the hybrid ranker's
  notion of relevance (requirement coverage).
- **Leakage**: during development the demo persona's ranking over all 36 jobs was inspected and two extraction
  bugs were fixed because of it.
- **Postings are cleaner than real ones**: most fixtures use explicit requirement headings.
- Synthetic relevance metrics do **not** show that RoleMatch improves interview or offer outcomes.
- Known miss: for the demo persona the hybrid ranker places a streaming data-engineering job (labelled 0) in its
  held-out top five, because Java and Kafka-adjacent evidence partially supports several of its requirements.

## Metric definitions

- P@5: share of the top 5 with grade ≥ 2. nDCG@k uses gain 2^grade − 1.
- Required-qualification extraction: a gold item is found when its phrase appears in an extracted requirement;
  precision/recall are for items classified as required.
- Unsupported evidence-link rate: evidence attached to supported/partly supported requirements that is not
  literal resume text or whose resume span does not mention the matched skill (directly or via a taxonomy
  implication).
- Hard-filter violation rate: shown (eligible) jobs that violate a scenario's hard filters per the oracle.
- Latency: wall-clock `rank_for_user` on the build machine (2 vCPU), including retrieval, evidence reranking and
  cache writes; "scaled" duplicates fixtures to ~536 postings.
