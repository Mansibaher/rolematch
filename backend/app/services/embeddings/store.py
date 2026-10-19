"""Compact storage of small embedding sets inside JSONB (float16, base64)."""

from __future__ import annotations

import base64

import numpy as np


def pack(vec: np.ndarray) -> str:
    return base64.b64encode(np.asarray(vec, dtype=np.float16).tobytes()).decode()


def unpack(s: str) -> np.ndarray:
    v = np.frombuffer(base64.b64decode(s), dtype=np.float16).astype(np.float32)
    n = np.linalg.norm(v)
    return v / n if n else v
