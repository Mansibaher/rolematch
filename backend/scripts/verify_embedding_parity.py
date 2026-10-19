"""Compare RoleMatch's NumPy MiniLM encoder with the reference sentence-transformers implementation.

    pip install sentence-transformers   # pulls in PyTorch; not needed by RoleMatch itself
    python scripts/verify_embedding_parity.py

Passes when every sentence's cosine similarity between the two implementations is >= 0.999.
(Not run in the original build environment, which could not reach Hugging Face; run it on a networked machine.)
"""

from __future__ import annotations

import sys

import numpy as np

SENTENCES = [
    "Designed and shipped REST APIs in Python (FastAPI) backed by PostgreSQL.",
    "Experience with Kubernetes is a plus.",
    "Benchmarked time-series classification models on wearable sensor data.",
    "We are unable to sponsor visas for this role.",
    "a",
]


def main() -> None:
    from sentence_transformers import SentenceTransformer

    sys.path.insert(0, ".")
    from app.services.embeddings.backend import get_embedder

    ref = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2").encode(SENTENCES, normalize_embeddings=True)
    ours = get_embedder().embed(SENTENCES)
    cos = (ref * ours).sum(axis=1)
    for s, c in zip(SENTENCES, cos, strict=True):
        print(f"{c:.5f}  {s}")
    if np.min(cos) < 0.999:
        sys.exit("parity check FAILED")
    print("parity check passed")


if __name__ == "__main__":
    main()
