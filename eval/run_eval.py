"""RoleMatch offline evaluation on synthetic data.

    cd backend && python ../eval/run_eval.py [--split tune|heldout|all] [--scale 500]

Compares three rankers on the same personas and jobs:
  keyword  – naive keyword counting: share of the posting's skill keywords that occur as substrings of the resume
             (case-insensitive, no word boundaries — what simple keyword matchers do)
  semantic – cosine similarity between whole-resume and whole-posting embeddings (all-MiniLM-L6-v2) only
  hybrid   – RoleMatch's evidence-based qualification match (requirements + evidence + seniority + semantic)

Also reports: requirement-extraction quality on a hand-labelled set, unsupported evidence-link rate,
hard-filter violation rate against an independent oracle, and ranking latency.
Writes eval/results/results.json and eval/results/results.md. Uses its own database (ROLEMATCH_EVAL_DATABASE_URL).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import re
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

EVAL = Path(__file__).resolve().parent
REPO = EVAL.parent
sys.path.insert(0, str(REPO / "backend"))
os.environ.setdefault("ROLEMATCH_DATABASE_URL", os.environ.get("ROLEMATCH_EVAL_DATABASE_URL", "postgresql+psycopg://rolematch:rolematch@localhost:5432/rolematch_eval"))
os.environ.setdefault("ROLEMATCH_EMBEDDING_BACKEND", "minilm")
os.environ.setdefault("ROLEMATCH_CELERY_EAGER", "true")

import numpy as np  # noqa: E402
import yaml  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db.session import get_engine, get_sessionmaker  # noqa: E402
from app.models import Job, Preferences, User, UserWatch, WorkEligibility  # noqa: E402
from app.services.embeddings.backend import embed_long_text, get_embedder  # noqa: E402
from app.services.jobs.adapters.fixtures import FixtureAdapter, load_fixture_file  # noqa: E402
from app.services.jobs.ingest import ensure_source, run_source  # noqa: E402
from app.services.matching.engine import rank_for_user, score_job, scoring_config  # noqa: E402
from app.services.matching.profile import build_profile  # noqa: E402
from app.services.matching.requirements import extract  # noqa: E402
from app.services.resume.service import create_version, mark_reviewed  # noqa: E402
from app.services.skills import get_taxonomy  # noqa: E402


# ------------------------------------------------------------------ metrics


def precision_at_k(ranked: list[str], labels: dict[str, int], k: int, rel: int = 2) -> float:
    top = ranked[:k]
    return sum(1 for j in top if labels.get(j, 0) >= rel) / k


def ndcg_at_k(ranked: list[str], labels: dict[str, int], k: int) -> float:
    def dcg(grades):
        return sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(grades))

    ideal = sorted(labels.values(), reverse=True)[:k]
    idcg = dcg(ideal)
    return dcg([labels.get(j, 0) for j in ranked[:k]]) / idcg if idcg else float("nan")


# ------------------------------------------------------------------ setup


def reset_db() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(REPO / "backend" / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO / "backend" / "alembic"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


def setup(db):
    src = ensure_source(db, "fixture", "fixtures", "Demo fixtures")
    db.commit()
    run_source(db, src, "eval-fixtures", adapter=FixtureAdapter())
    return src


def make_user(db, name: str, resume: str, src, prefs: dict | None = None, needs_sponsorship=None) -> User:
    u = User(email=f"{name}@eval.example.com", password_hash=hash_password("eval-password"), timezone="UTC")
    db.add(u)
    db.flush()
    p = Preferences(user_id=u.id, hard_filters={}, feedback_adjustments={})
    for k, v in (prefs or {}).items():
        setattr(p, k, v)
    db.add(p)
    if needs_sponsorship is not None:
        db.add(WorkEligibility(user_id=u.id, needs_sponsorship=needs_sponsorship))
    db.add(UserWatch(user_id=u.id, source_id=src.id))
    db.commit()
    v = create_version(db, u, filename=None, content_type=None, data=None, pasted_text=resume)
    mark_reviewed(db, u, v)
    return u


# ------------------------------------------------------------------ baselines


def naive_keyword_score(resume: str, job_text: str) -> float:
    """Substring keyword counting over taxonomy aliases — deliberately naive (Java matches inside JavaScript)."""
    tax = get_taxonomy()
    r, j = resume.lower(), job_text.lower()
    wanted = 0
    hit = 0
    for sid, sd in tax.skills.items():
        names = {sd.name.lower(), sid.replace("_", " ")}
        if any(n in j for n in names if len(n) > 1):
            wanted += 1
            if any(n in r for n in names if len(n) > 1):
                hit += 1
    return hit / wanted if wanted else 0.0


# ------------------------------------------------------------------ main evaluation


def evaluate_rankings(db, users: dict[str, User], resumes: dict[str, str], labels: dict, jobs: dict[str, Job], split: str) -> dict:
    emb = get_embedder()
    job_ids = [fid for fid, j in jobs.items() if split == "all" or j["split"] == split]
    per = {"keyword": [], "semantic": [], "hybrid": []}
    detail = {}
    for persona, user in users.items():
        lab = {k: v for k, v in labels[persona].items() if k in job_ids}
        out = rank_for_user(db, user, use_cache=False)
        hyb = {j.source_job_id: r["scores"]["qualification_match"] for j, r in out["results"] if j.source_job_id in job_ids}
        rvec = embed_long_text(resumes[persona], emb)
        sem = {j.source_job_id: float(np.dot(np.asarray(j.embedding, dtype=np.float32), rvec)) for j, _ in out["results"] if j.source_job_id in job_ids}
        kw = {j.source_job_id: naive_keyword_score(resumes[persona], j.title + "\n" + j.description_text) for j, _ in out["results"] if j.source_job_id in job_ids}
        detail[persona] = {}
        for name, scores in (("keyword", kw), ("semantic", sem), ("hybrid", hyb)):
            # Ties broken by job id so results are deterministic.
            ranked = [j for j, _ in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))]
            m = {"P@5": precision_at_k(ranked, lab, 5), "nDCG@5": ndcg_at_k(ranked, lab, 5), "nDCG@10": ndcg_at_k(ranked, lab, 10),
                 "top5": ranked[:5]}
            per[name].append(m)
            detail[persona][name] = m
    summary = {}
    for name, ms in per.items():
        summary[name] = {k: round(statistics.mean([m[k] for m in ms if not math.isnan(m[k])]), 3) for k in ("P@5", "nDCG@5", "nDCG@10")}
    return {"split": split, "n_jobs": len(job_ids), "n_personas": len(users), "summary": summary, "per_persona": detail}


def extraction_quality(name: str = "extraction_gold.yaml") -> dict:
    gold = yaml.safe_load((EVAL / name).read_text())["postings"]
    tp = fp = fn = 0
    kind_correct = kind_total = 0
    missing = []
    for p in gold:
        ex = extract(p["title"], p["text"])
        reqs = [r for r in ex.requirements if r.kind in ("required", "preferred", "unclear")]
        for g in p["gold"]:
            match = next((r for r in reqs if g["phrase"].lower() in r.text.lower()), None)
            if match is None:
                missing.append(f"{p['id']}: {g['phrase']}")
                if g["kind"] == "required":
                    fn += 1
                continue
            kind_total += 1
            kind_correct += match.kind == g["kind"]
            if g["kind"] == "required":
                if match.kind == "required":
                    tp += 1
                else:
                    fn += 1
            elif match.kind == "required":
                fp += 1
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    n_gold = sum(len(p["gold"]) for p in gold)
    return {
        "postings": len(gold), "gold_items": n_gold, "items_found": kind_total,
        "item_recall": round(kind_total / n_gold, 3),
        "kind_accuracy_on_found": round(kind_correct / kind_total, 3) if kind_total else None,
        "required_precision": round(prec, 3), "required_recall": round(rec, 3),
        "required_f1": round(2 * prec * rec / (prec + rec), 3) if prec + rec else 0.0,
        "missing_items": missing,
    }


def evidence_link_audit(db, users: dict[str, User]) -> dict:
    """Unsupported evidence-link rate: evidence that is not literal resume text or does not mention the skill."""
    tax = get_taxonomy()
    total = bad = 0
    problems = []
    cfg = scoring_config()
    for persona, user in users.items():
        out = rank_for_user(db, user, use_cache=False)
        from app.models import ResumeVersion

        prefs = db.get(Preferences, user.id)
        raw = db.get(ResumeVersion, prefs.active_resume_version_id).raw_text
        for job, res in out["results"]:
            for r in res["requirements"]:
                if r["status"] not in ("supported", "partially_supported"):
                    continue
                for ev in r["evidence"]:
                    total += 1
                    ok = True
                    if ev["context"] == "user_added":
                        continue
                    if ev.get("skill"):
                        ok = ev["text"].rstrip("…").lstrip("…") in raw or ev["text"] in raw
                        names = [s.strip() for s in ev["skill"].split(",")]
                        ids = [tax.resolve(n) or n for n in names]
                        span = raw[ev["start"]:ev["end"]] if ev.get("start") is not None else ""
                        ok = ok and span and span in ev["text"] and any(i in tax.find_ids(span) or tax.resolve(span) == i
                                                                        or i in tax.implied_by(tax.resolve(span) or "") for i in ids)
                    elif ev.get("entry_title"):
                        ok = ev["entry_title"] in raw
                    if not ok:
                        bad += 1
                        problems.append({"persona": persona, "job": job.source_job_id, "requirement": r["text"], "evidence": ev["text"][:120]})
    _ = cfg
    return {"evidence_items": total, "unsupported": bad, "rate": round(bad / total, 4) if total else None, "examples": problems[:10]}


def oracle_violates(item: dict, scenario: dict) -> list[str]:
    """Independent check of fixture metadata against a hard-filter scenario (does not use the engine)."""
    v = []
    loc = item["location"]
    if scenario.get("employment_types") and {"Full-time": "full_time", "Contract": "contract", "Intern": "internship"}.get(item["commitment"]) not in scenario["employment_types"]:
        v.append("employment_type")
    if scenario.get("remote_modes") and item["workplace"] not in scenario["remote_modes"]:
        v.append("work_mode")
    if scenario.get("city") and item["workplace"] != "remote" and scenario["city"] not in loc:
        v.append("location")
    if scenario.get("us_only") and item["workplace"] == "remote" and ("Canada" in loc or "UK" in loc):
        v.append("location")
    if scenario.get("city") and item["workplace"] == "remote" and "Canada" in loc:
        v.append("location")
    if scenario.get("needs_sponsorship") and re.search(r"(?i)unable to sponsor|without sponsorship|no sponsorship", item["description"]):
        v.append("sponsorship")
    if scenario.get("excluded_employers") and any(e.lower() in item["employer"].lower() for e in scenario["excluded_employers"]):
        v.append("excluded_employers")
    return v


def hard_filter_audit(db, src, resumes: dict[str, str]) -> dict:
    items = {i["id"]: i for i in load_fixture_file()}
    scenarios = [
        {"name": "LA on-site/hybrid, full-time, needs sponsorship",
         "prefs": {"locations": ["Los Angeles, CA"], "remote_modes": ["hybrid", "onsite"], "employment_types": ["full_time"],
                   "hard_filters": {"location": True, "work_mode": True, "employment_type": True, "sponsorship": True}},
         "oracle": {"city": "Los Angeles", "remote_modes": ["hybrid", "onsite"], "employment_types": ["full_time"], "needs_sponsorship": True},
         "needs_sponsorship": True},
        {"name": "Remote only in the US, excluded employer",
         "prefs": {"locations": ["United States"], "remote_modes": ["remote"], "excluded_employers": ["Petalform"],
                   "hard_filters": {"location": True, "work_mode": True}},
         "oracle": {"remote_modes": ["remote"], "excluded_employers": ["Petalform"], "city": None, "us_only": True},
         "needs_sponsorship": None},
        {"name": "Seattle or remote, contract excluded",
         "prefs": {"locations": ["Seattle, WA", "United States"], "remote_modes": ["remote", "hybrid"], "employment_types": ["full_time"],
                   "hard_filters": {"work_mode": True, "employment_type": True}},
         "oracle": {"remote_modes": ["remote", "hybrid"], "employment_types": ["full_time"]},
         "needs_sponsorship": None},
    ]
    results = []
    total_shown = total_viol = 0
    for i, sc in enumerate(scenarios):
        u = make_user(db, f"filters{i}", resumes["jordan_backend_ml"], src, sc["prefs"], sc["needs_sponsorship"])
        out = rank_for_user(db, u, use_cache=False)
        shown = [j for j, r in out["results"] if r["eligible"]]
        viol = [(j.source_job_id, oracle_violates(items[j.source_job_id], sc["oracle"])) for j in shown]
        viol = [(j, v) for j, v in viol if v]
        expected_visible = sum(1 for it in items.values() if not oracle_violates(it, sc["oracle"]))
        results.append({"scenario": sc["name"], "shown": len(shown), "violations": viol, "oracle_eligible": expected_visible})
        total_shown += len(shown)
        total_viol += len(viol)
    return {"scenarios": results, "shown_total": total_shown, "violations_total": total_viol,
            "violation_rate": round(total_viol / total_shown, 4) if total_shown else None}


def latency(db, users: dict[str, User], scale: int) -> dict:
    u = next(iter(users.values()))
    cold, warm = [], []
    for _ in range(5):
        t = time.perf_counter()
        rank_for_user(db, u, use_cache=False)
        cold.append(1000 * (time.perf_counter() - t))
    for _ in range(5):
        t = time.perf_counter()
        rank_for_user(db, u, use_cache=True)
        warm.append(1000 * (time.perf_counter() - t))
    res = {"jobs": 36, "uncached_ms": {"median": round(statistics.median(cold), 1), "max": round(max(cold), 1)},
           "cached_ms": {"median": round(statistics.median(warm), 1), "max": round(max(warm), 1)}}
    if scale:
        # Replicate fixture postings to measure reranking cost at a larger, still synthetic, scale.
        from app.services.jobs.ingest import upsert_jobs
        from app.services.jobs.adapters.fixtures import normalize
        from app.services.matching.enrich import enrich_jobs

        src = ensure_source(db, "fixture", "scale", "Scale test (synthetic copies)")
        db.commit()
        db.add(UserWatch(user_id=u.id, source_id=src.id))
        db.commit()
        items = load_fixture_file()
        copies = []
        for n in range(scale):
            it = dict(items[n % len(items)])
            it["id"] = f"scale-{n}"
            it["description"] = it["description"] + f"\n\nReference {n}."
            copies.append(normalize(it))
        t = time.perf_counter()
        ids, _, _ = upsert_jobs(db, src, copies, datetime.now(UTC))
        db.commit()
        enrich_jobs(db, ids)
        enrich_s = time.perf_counter() - t
        times = []
        for _ in range(3):
            t = time.perf_counter()
            out = rank_for_user(db, u, use_cache=False)
            times.append(1000 * (time.perf_counter() - t))
        res["scaled"] = {"jobs": out["stats"]["visible"], "uncached_ms_median": round(statistics.median(times), 1),
                         "ingest_enrich_seconds": round(enrich_s, 1), "stats": out["stats"]["timing_ms"]}
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", type=int, default=500)
    ap.add_argument("--out", default=str(EVAL / "results"))
    ap.add_argument("--splits", default="tune,heldout,all", help="comma-separated; run 'tune' alone while iterating")
    args = ap.parse_args()
    reset_db()
    db = get_sessionmaker()()
    src = setup(db)
    labels = yaml.safe_load((EVAL / "labels.yaml").read_text())["personas"]
    resumes = {p: (EVAL / "personas" / f"{p}.txt").read_text() for p in labels}
    users = {p: make_user(db, p, resumes[p], src) for p in labels}
    jobs = {i["id"]: i for i in load_fixture_file()}

    started = time.perf_counter()
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "environment": {"python": platform.python_version(), "machine": platform.machine(), "cpus": os.cpu_count(),
                        "embedding_model": get_embedder().name},
        "rankings": {s: evaluate_rankings(db, users, resumes, labels, jobs, s) for s in args.splits.split(",")},
        "extraction": {"dev": extraction_quality("extraction_gold.yaml"), "heldout": extraction_quality("extraction_gold_heldout.yaml")},
        "evidence_links": evidence_link_audit(db, users),
        "hard_filters": hard_filter_audit(db, src, resumes),
    }
    report["latency"] = latency(db, users, args.scale)
    report["runtime_seconds"] = round(time.perf_counter() - started, 1)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(report, indent=2, default=str))
    (out / "results.md").write_text(render_md(report))
    print(render_md(report))


def render_md(r: dict) -> str:
    lines = [f"# Evaluation results\n", f"Generated {r['generated_at']} on {r['environment']['cpus']} CPU(s), Python {r['environment']['python']}, "
             f"embeddings: {r['environment']['embedding_model']}.\n",
             "All data is synthetic (5 personas × 36 fictional postings). Labels were written by the developer; see eval/labels.yaml for the rubric and known biases. "
             "These numbers say nothing about interview or offer outcomes.\n"]
    for split in r["rankings"]:
        s = r["rankings"][split]
        lines.append(f"\n## Ranking quality: {split} split ({s['n_jobs']} jobs, {s['n_personas']} personas)\n")
        lines.append("| Ranker | P@5 (grade ≥ 2) | nDCG@5 | nDCG@10 |\n|---|---|---|---|")
        for name in ("keyword", "semantic", "hybrid"):
            m = s["summary"][name]
            lines.append(f"| {name} | {m['P@5']:.3f} | {m['nDCG@5']:.3f} | {m['nDCG@10']:.3f} |")
    for which, label in (("dev", "development set (used to find and fix bugs after the first run)"), ("heldout", "held-out set (written before the fixes)")):
        e = r["extraction"][which]
        lines.append(f"\n## Requirement extraction: {label}\n\n{e['postings']} hand-labelled postings, {e['gold_items']} items.\n")
        lines.append(f"- Items found: {e['items_found']}/{e['gold_items']} (recall {e['item_recall']})")
        lines.append(f"- Required-vs-preferred accuracy on found items: {e['kind_accuracy_on_found']}")
        lines.append(f"- Required qualifications: precision {e['required_precision']}, recall {e['required_recall']}, F1 {e['required_f1']}")
        if e["missing_items"]:
            lines.append(f"- Missed: {'; '.join(e['missing_items'])}")
    ev = r["evidence_links"]
    lines.append(f"\n## Evidence links\n\n- {ev['evidence_items']} evidence items attached to supported/partly supported requirements; {ev['unsupported']} failed the audit (rate {ev['rate']}).")
    hf = r["hard_filters"]
    lines.append(f"\n## Hard filters\n\n- {hf['shown_total']} jobs shown across {len(hf['scenarios'])} scenarios; {hf['violations_total']} violated a hard filter according to an independent oracle (rate {hf['violation_rate']}).")
    for sc in hf["scenarios"]:
        lines.append(f"  - {sc['scenario']}: shown {sc['shown']}, oracle-eligible {sc['oracle_eligible']}, violations {len(sc['violations'])}")
    lt = r["latency"]
    lines.append(f"\n## Latency\n\n- 36 jobs: uncached ranking median {lt['uncached_ms']['median']} ms (max {lt['uncached_ms']['max']}); cached median {lt['cached_ms']['median']} ms.")
    if "scaled" in lt:
        lines.append(f"- {lt['scaled']['jobs']} jobs (synthetic copies): uncached ranking median {lt['scaled']['uncached_ms_median']} ms; ingesting and enriching {lt['scaled']['jobs'] - 36} postings took {lt['scaled']['ingest_enrich_seconds']} s.")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
