"""Sentence embeddings.

Two backends share one interface and both produce 384-dim, L2-normalised vectors:

- ``minilm``  – all-MiniLM-L6-v2 (sentence-transformers) run with a small NumPy implementation of the BERT
                encoder + mean pooling. No PyTorch dependency; weights are loaded from a local directory
                containing ``config.json``, ``tokenizer.json`` and ``model.safetensors`` (fp32) or
                ``model.fp16.safetensors``. See scripts/fetch_model.py.
- ``hashing`` – deterministic feature-hashing of word unigrams/bigrams. NOT a semantic model; it exists only
                so unit tests can run without model weights. It is never used for reported evaluation numbers.

Semantic similarity is always used as one ranking signal, never as proof that a qualification is met.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import numpy as np

from app.core.config import get_settings


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str]) -> np.ndarray: ...


def _l2(x: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(x, axis=-1, keepdims=True)
    return x / np.maximum(n, 1e-12)


class HashingEmbedder:
    name = "hashing-v1"

    def __init__(self, dim: int = 384):
        self.dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            toks = re.findall(r"[a-z0-9+#]+", t.lower())
            feats = toks + [a + "_" + b for a, b in zip(toks, toks[1:], strict=False)]
            for f in feats:
                h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "little")
                out[i, h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        return _l2(out)


def _erf_approx(x: np.ndarray) -> np.ndarray:
    # Abramowitz–Stegun 7.1.26 (max abs error 1.5e-7).
    sign = np.sign(x)
    a = np.abs(x)
    t = 1.0 / (1.0 + 0.3275911 * a)
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * np.exp(-a * a)
    return (sign * y).astype(np.float32)


def _layernorm(x: np.ndarray, w: np.ndarray, b: np.ndarray, eps: float) -> np.ndarray:
    mu = x.mean(-1, keepdims=True)
    var = ((x - mu) ** 2).mean(-1, keepdims=True)
    return (x - mu) / np.sqrt(var + eps) * w + b


class MiniLMEmbedder:
    """NumPy forward pass of sentence-transformers/all-MiniLM-L6-v2 (BERT, 6 layers, 384 hidden, mean pooling)."""

    name = "all-MiniLM-L6-v2"
    max_tokens = 256  # sentence-transformers max_seq_length for this model

    def __init__(self, model_dir: Path):
        from safetensors.numpy import load_file
        from tokenizers import Tokenizer

        cfg = json.loads((model_dir / "config.json").read_text())
        self.dim = int(cfg["hidden_size"])
        self.heads = int(cfg["num_attention_heads"])
        self.layers = int(cfg["num_hidden_layers"])
        self.eps = float(cfg.get("layer_norm_eps", 1e-12))
        weights_path = model_dir / "model.safetensors"
        if not weights_path.exists():
            weights_path = model_dir / "model.fp16.safetensors"
        raw = load_file(str(weights_path))
        self.w = {k.removeprefix("bert."): v.astype(np.float32) for k, v in raw.items() if v.dtype != np.int64}
        self.tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tok.enable_truncation(max_length=self.max_tokens)
        self.tok.no_padding()
        self._lock = threading.Lock()

    def _encode_one(self, ids: list[int]) -> np.ndarray:
        w = self.w
        n = len(ids)
        x = w["embeddings.word_embeddings.weight"][ids]
        x = x + w["embeddings.position_embeddings.weight"][:n]
        x = x + w["embeddings.token_type_embeddings.weight"][0]
        x = _layernorm(x, w["embeddings.LayerNorm.weight"], w["embeddings.LayerNorm.bias"], self.eps)
        hd = self.dim // self.heads
        for i in range(self.layers):
            p = f"encoder.layer.{i}."
            q = x @ w[p + "attention.self.query.weight"].T + w[p + "attention.self.query.bias"]
            k = x @ w[p + "attention.self.key.weight"].T + w[p + "attention.self.key.bias"]
            v = x @ w[p + "attention.self.value.weight"].T + w[p + "attention.self.value.bias"]
            q = q.reshape(n, self.heads, hd).transpose(1, 0, 2)
            k = k.reshape(n, self.heads, hd).transpose(1, 0, 2)
            v = v.reshape(n, self.heads, hd).transpose(1, 0, 2)
            att = (q @ k.transpose(0, 2, 1)) / math.sqrt(hd)
            att = att - att.max(-1, keepdims=True)
            att = np.exp(att)
            att = att / att.sum(-1, keepdims=True)
            ctx = (att @ v).transpose(1, 0, 2).reshape(n, self.dim)
            a = ctx @ w[p + "attention.output.dense.weight"].T + w[p + "attention.output.dense.bias"]
            x = _layernorm(a + x, w[p + "attention.output.LayerNorm.weight"], w[p + "attention.output.LayerNorm.bias"], self.eps)
            h = _erf_gelu(x @ w[p + "intermediate.dense.weight"].T + w[p + "intermediate.dense.bias"])
            o = h @ w[p + "output.dense.weight"].T + w[p + "output.dense.bias"]
            x = _layernorm(o + x, w[p + "output.LayerNorm.weight"], w[p + "output.LayerNorm.bias"], self.eps)
        return x.mean(0)  # no padding, so mean over all tokens == masked mean pooling

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        with self._lock:
            encs = self.tok.encode_batch([t if t.strip() else "." for t in texts])
            for i, enc in enumerate(encs):
                out[i] = self._encode_one(enc.ids)
        return _l2(out)


def _erf_gelu(x: np.ndarray) -> np.ndarray:
    return (0.5 * x * (1.0 + _erf_approx(x / math.sqrt(2.0)))).astype(np.float32)


@lru_cache
def get_embedder() -> Embedder:
    s = get_settings()
    if s.embedding_backend == "hashing":
        return HashingEmbedder(s.embedding_dim)
    if s.embedding_backend == "minilm":
        if not (s.embedding_model_dir / "tokenizer.json").exists():
            raise RuntimeError(
                f"Embedding model not found in {s.embedding_model_dir}. Run `python scripts/fetch_model.py` "
                "or set ROLEMATCH_EMBEDDING_BACKEND=hashing for tests (non-semantic)."
            )
        return MiniLMEmbedder(s.embedding_model_dir)
    raise ValueError(f"unknown embedding backend {s.embedding_backend!r}")


def embed_long_text(text: str, embedder: Embedder | None = None, chunk_chars: int = 900) -> np.ndarray:
    """Embed text longer than the model window by averaging paragraph-aligned chunks."""
    emb = embedder or get_embedder()
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n(?=[-•*])", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(buf) + len(p) > chunk_chars and buf:
            chunks.append(buf)
            buf = ""
        buf = (buf + "\n" + p).strip()
    if buf:
        chunks.append(buf)
    if not chunks:
        chunks = [text[:chunk_chars] or "."]
    vecs = emb.embed(chunks[:24])
    return _l2(vecs.mean(0, keepdims=True))[0]
