"""Job enrichment: structured requirement extraction + embeddings, computed at ingestion time."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import Job
from app.services.embeddings.backend import embed_long_text, get_embedder
from app.services.embeddings.store import pack
from app.services.matching.requirements import EXTRACTION_VERSION, extract
from app.services.text_safety import find_instruction_like_text, strip_instruction_like_sentences


def enrich_job(job: Job) -> None:
    # Instruction-like sentences are flagged for the user but removed before extraction and embedding, so
    # text aimed at automated screeners cannot change requirements, evidence or similarity.
    flags = find_instruction_like_text(job.description_text)
    clean = strip_instruction_like_sentences(job.description_text)
    ex = extract(job.title, clean)
    ex.instruction_like_text = flags
    if flags and not any("instruction-like" in w for w in ex.warnings):
        ex.warnings.append("The posting contains instruction-like text. It was ignored for matching.")
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
    job.embedding = embed_long_text(f"{job.title}\n{clean}", emb).tolist()
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
