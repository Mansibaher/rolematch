"""Download the all-MiniLM-L6-v2 weights used for semantic matching.

    python scripts/fetch_model.py [--dest ../models/all-MiniLM-L6-v2] [--revision main]

Fetches config.json, tokenizer.json and model.safetensors from the official Hugging Face repository
sentence-transformers/all-MiniLM-L6-v2 (Apache-2.0). Safetensors files contain only tensors (no pickled code).
Prints SHA-256 digests; set ROLEMATCH_MODEL_SHA256 to the digest you trust to enforce it on later builds.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

import httpx

REPO = "sentence-transformers/all-MiniLM-L6-v2"
FILES = ["config.json", "tokenizer.json", "model.safetensors"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=str(Path(__file__).resolve().parents[2] / "models" / "all-MiniLM-L6-v2"))
    ap.add_argument("--revision", default=os.environ.get("ROLEMATCH_MODEL_REVISION", "main"))
    args = ap.parse_args()
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    with httpx.Client(follow_redirects=True, timeout=120) as c:
        for name in FILES:
            url = f"https://huggingface.co/{REPO}/resolve/{args.revision}/{name}"
            r = c.get(url)
            r.raise_for_status()
            (dest / name).write_bytes(r.content)
            print(f"{name}: {len(r.content):,} bytes sha256={hashlib.sha256(r.content).hexdigest()}")
    expected = os.environ.get("ROLEMATCH_MODEL_SHA256")
    if expected:
        got = hashlib.sha256((dest / "model.safetensors").read_bytes()).hexdigest()
        if got != expected:
            sys.exit(f"model.safetensors digest {got} does not match ROLEMATCH_MODEL_SHA256")
    print(f"Model files written to {dest}")


if __name__ == "__main__":
    main()
