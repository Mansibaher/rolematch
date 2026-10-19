# Evaluation results

Generated 2026-10-19T22:48:18.896227+00:00 on 2 CPU(s), Python 3.13.16, embeddings: all-MiniLM-L6-v2.

All data is synthetic (5 personas × 36 fictional postings). Labels were written by the developer; see eval/labels.yaml for the rubric and known biases. These numbers say nothing about interview or offer outcomes.


## Ranking quality: tune split (18 jobs, 5 personas)

| Ranker | P@5 (grade ≥ 2) | nDCG@5 | nDCG@10 |
|---|---|---|---|
| keyword | 0.320 | 0.634 | 0.675 |
| semantic | 0.360 | 0.767 | 0.825 |
| hybrid | 0.400 | 0.903 | 0.931 |

## Ranking quality: heldout split (18 jobs, 5 personas)

| Ranker | P@5 (grade ≥ 2) | nDCG@5 | nDCG@10 |
|---|---|---|---|
| keyword | 0.440 | 0.821 | 0.844 |
| semantic | 0.360 | 0.721 | 0.794 |
| hybrid | 0.520 | 0.950 | 0.959 |

## Ranking quality: all split (36 jobs, 5 personas)

| Ranker | P@5 (grade ≥ 2) | nDCG@5 | nDCG@10 |
|---|---|---|---|
| keyword | 0.520 | 0.619 | 0.719 |
| semantic | 0.520 | 0.655 | 0.774 |
| hybrid | 0.760 | 0.950 | 0.943 |

## Requirement extraction: development set (used to find and fix bugs after the first run)

8 hand-labelled postings, 32 items.

- Items found: 32/32 (recall 1.0)
- Required-vs-preferred accuracy on found items: 1.0
- Required qualifications: precision 1.0, recall 1.0, F1 1.0

## Requirement extraction: held-out set (written before the fixes)

6 hand-labelled postings, 19 items.

- Items found: 19/19 (recall 1.0)
- Required-vs-preferred accuracy on found items: 0.947
- Required qualifications: precision 1.0, recall 0.909, F1 0.952

## Evidence links

- 716 evidence items attached to supported/partly supported requirements; 0 failed the audit (rate 0.0).

## Hard filters

- 42 jobs shown across 3 scenarios; 0 violated a hard filter according to an independent oracle (rate 0.0).
  - LA on-site/hybrid, full-time, needs sponsorship: shown 6, oracle-eligible 6, violations 0
  - Remote only in the US, excluded employer: shown 10, oracle-eligible 10, violations 0
  - Seattle or remote, contract excluded: shown 26, oracle-eligible 26, violations 0

## Latency

- 36 jobs: uncached ranking median 49.7 ms (max 86.8); cached median 38.3 ms.
- 536 jobs (synthetic copies): uncached ranking median 425.8 ms; ingesting and enriching 500 postings took 50.6 s.
