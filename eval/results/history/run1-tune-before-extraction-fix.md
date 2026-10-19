# Evaluation results

Generated 2026-10-19T22:45:35.148281+00:00 on 2 CPU(s), Python 3.13.16, embeddings: all-MiniLM-L6-v2.

All data is synthetic (5 personas × 36 fictional postings). Labels were written by the developer; see eval/labels.yaml for the rubric and known biases. These numbers say nothing about interview or offer outcomes.


## Ranking quality: tune split (18 jobs, 5 personas)

| Ranker | P@5 (grade ≥ 2) | nDCG@5 | nDCG@10 |
|---|---|---|---|
| keyword | 0.320 | 0.634 | 0.675 |
| semantic | 0.360 | 0.767 | 0.825 |
| hybrid | 0.400 | 0.903 | 0.931 |

## Requirement extraction (8 hand-labelled postings, 32 items)

- Items found: 27/32 (recall 0.844)
- Required-vs-preferred accuracy on found items: 0.926
- Required qualifications: precision 0.933, recall 0.737, F1 0.824
- Missed: ex-03: MS or PhD; ex-03: PyTorch; ex-03: deploying models; ex-03: recommender systems; ex-04: Node.js is required

## Evidence links

- 716 evidence items attached to supported/partly supported requirements; 0 failed the audit (rate 0.0).

## Hard filters

- 42 jobs shown across 3 scenarios; 0 violated a hard filter according to an independent oracle (rate 0.0).
  - LA on-site/hybrid, full-time, needs sponsorship: shown 6, oracle-eligible 6, violations 0
  - Remote only in the US, excluded employer: shown 10, oracle-eligible 11, violations 0
  - Seattle or remote, contract excluded: shown 26, oracle-eligible 26, violations 0

## Latency

- 36 jobs: uncached ranking median 69.4 ms (max 73.9); cached median 35.7 ms.
