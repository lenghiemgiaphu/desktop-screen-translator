"""Edit-distance metrics (CER / WER) used by the benchmark and the tests."""
from __future__ import annotations

from typing import Sequence


def edit_distance(a: Sequence, b: Sequence) -> int:
    """Levenshtein distance (insert / delete / substitute, all cost 1)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def normalize_ws(text: str) -> str:
    return " ".join(text.split())


def cer(reference: str, hypothesis: str) -> float:
    """Character error rate on whitespace-normalised text (can exceed 1.0)."""
    ref, hyp = normalize_ws(reference), normalize_ws(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    return edit_distance(ref, hyp) / len(ref)


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate on whitespace-split tokens."""
    ref, hyp = reference.split(), hypothesis.split()
    if not ref:
        return 0.0 if not hyp else 1.0
    return edit_distance(ref, hyp) / len(ref)
