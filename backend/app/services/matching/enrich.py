"""Job enrichment: structured requirement extraction + embeddings, computed at ingestion time."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import Job
from app.services.embeddings.backend import embed_long_text, get_embedder
from app.services.embeddings.store import pack
from app.services.matching.requirements import EXTRACTION_VERSION, extract


def enrich_job(job: Job) -> None:
    ex = extract(job.title, job.description_text)
    data = ex.to_dict()
    emb = get_embedder()
    texts = [r.text for r in ex.requirements]
    if texts:
        vecs = emb.embed(texts)
        data["req_embeddings"] = {r.id: pack(v) for r, v in zip(ex.requirements, vecs, strict=True)}
    else:
        data["req_embeddings"] = {}
    data["embedding_model"] = emb.name
    job.extraction = data
    job.extraction_version = EXTRACTION_VERSION
    job.embedding = embed_long_text(f"{job.title}\n{job.description_text}", emb).tolist()
    job.embedding_model = emb.name


def enrich_jobs(db: Session, job_ids: list[uuid.UUID]) -> None:
    for jid in job_ids:
        job = db.get(Job, jid)
        if job is not None:
            enrich_job(job)
    db.commit()


def needs_enrichment(job: Job) -> bool:
    return (
        job.extraction is None
        or job.extraction_version != EXTRACTION_VERSION
        or job.embedding is None
        or job.embedding_model != get_embedder().name
    )
