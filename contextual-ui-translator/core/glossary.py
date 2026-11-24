"""Context-weighted fuzzy glossary backed by ``database.json``.

* 100 % backward compatible: entries without a ``"context"`` field behave as
  before (neutral, valid everywhere).
* An entry may carry ``"context": "ide"`` (or a list such as
  ``["browser", "ide"]``). When the active application's domain matches, the
  fuzzy score gets a **+10** boost (capped at 100); when it conflicts, the
  score gets a **-10** penalty.
* A boost never creates a match on its own: an entry must reach the similarity
  threshold on text alone (``base_score >= threshold``). The boost only
  re-ranks candidates; the penalty can reject a borderline match.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Tuple

from .context_detector import GENERAL, GLOBAL_CONTEXT
from .text_utils import has_han, normalize_for_match

log = logging.getLogger(__name__)

CONTEXT_BOOST = 10
CONTEXT_PENALTY = 10
DEFAULT_THRESHOLD = 80
_RESERVED_KEYS = {"pinyin", "context"}


def indel_ratio(a: str, b: str) -> int:
    """Pure-Python equivalent of ``fuzz.ratio`` (0-100, indel-distance based)."""
    if a == b:
        return 100
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0
    prev = [0] * (lb + 1)
    for ca in a:
        cur = [0]
        for j, cb in enumerate(b, 1):
            cur.append(prev[j - 1] + 1 if ca == cb else max(prev[j], cur[j - 1]))
        prev = cur
    lcs = prev[-1]
    return int(round(100.0 * 2 * lcs / (la + lb)))


try:  # prefer the fast C implementation when installed (thefuzz depends on it)
    from rapidfuzz import fuzz as _rf_fuzz

    def ratio(a: str, b: str) -> int:
        return int(round(_rf_fuzz.ratio(a, b)))
except ImportError:  # pragma: no cover - depends on the environment
    try:
        from thefuzz import fuzz as _tf_fuzz

        def ratio(a: str, b: str) -> int:
            return int(_tf_fuzz.ratio(a, b))
    except ImportError:
        ratio = indel_ratio


def parse_contexts(raw) -> FrozenSet[str]:
    """Normalise an entry's ``context`` value. Empty set means *global*."""
    if raw is None:
        return frozenset()
    values = [raw] if isinstance(raw, str) else list(raw)
    cleaned = {str(v).strip().lower() for v in values if str(v).strip()}
    if not cleaned or cleaned & {GLOBAL_CONTEXT, GENERAL}:
        return frozenset()
    return frozenset(cleaned)


def context_adjustment(entry_contexts: FrozenSet[str], active_domain: Optional[str]) -> int:
    """+10 when the entry targets the active domain, -10 when it targets others."""
    if not entry_contexts or not active_domain or active_domain == GENERAL:
        return 0
    return CONTEXT_BOOST if active_domain in entry_contexts else -CONTEXT_PENALTY


@dataclass(frozen=True)
class GlossaryMatch:
    translated: str
    score: int                 # after context adjustment (what the UI shows)
    base_score: int            # pure text similarity
    pinyin: str = ""
    entry_id: str = ""
    contexts: FrozenSet[str] = frozenset()
    context_matched: bool = False
    matched_term: str = ""

    @property
    def context_label(self) -> str:
        """Short tag shown in the badge, e.g. ``IDE``; empty for global entries."""
        if not self.contexts:
            return ""
        from .context_detector import DOMAIN_LABELS
        first = sorted(self.contexts)[0]
        return DOMAIN_LABELS.get(first, first.upper())

    def as_tuple(self) -> Tuple[str, str, str, int]:
        """``(translated_text, source_badge, pinyin, confidence_score)``."""
        badge = f"Local DB ({self.score}%" + (f" \u2022 {self.context_label})" if self.context_matched else ")")
        return self.translated, badge, self.pinyin, self.score


@dataclass
class _Term:
    norm: str
    lang: str
    entry_id: str
    translations: Dict[str, str]
    pinyin: str
    contexts: FrozenSet[str]


class Glossary:
    def __init__(self, path: Optional[os.PathLike] = None, threshold: int = DEFAULT_THRESHOLD,
                 data: Optional[dict] = None):
        self.path = Path(path) if path else None
        self.threshold = threshold
        self._lock = threading.RLock()
        self._raw: Dict[str, dict] = {}
        self._terms: List[_Term] = []
        self._mtime: Optional[float] = None
        if data is not None:
            self._raw = dict(data)
            self._rebuild()
        else:
            self.reload()

    # ------------------------------------------------------------------ io
    def reload(self) -> None:
        raw: Dict[str, dict] = {}
        mtime = None
        if self.path and self.path.exists():
            try:
                mtime = self.path.stat().st_mtime
                with open(self.path, "r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict):
                    raw = loaded
                else:
                    log.warning("%s must contain a JSON object; ignoring it", self.path)
            except (OSError, ValueError) as exc:
                log.warning("Could not read glossary %s: %s", self.path, exc)
        with self._lock:
            self._raw = raw
            self._mtime = mtime
            self._rebuild()

    def refresh_if_changed(self) -> bool:
        """Reload when the file changed on disk (lets users edit it while running)."""
        if not self.path:
            return False
        try:
            mtime = self.path.stat().st_mtime if self.path.exists() else None
        except OSError:
            return False
        if mtime != self._mtime:
            self.reload()
            return True
        return False

    def save(self) -> None:
        if not self.path:
            return
        with self._lock:
            payload = json.dumps(self._raw, ensure_ascii=False, indent=2)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(payload)
                os.replace(tmp, self.path)
            except Exception:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
            self._mtime = self.path.stat().st_mtime

    def _rebuild(self) -> None:
        terms: List[_Term] = []
        for entry_id, entry in self._raw.items():
            if not isinstance(entry, dict):
                continue
            translations = {k: v for k, v in entry.items()
                            if isinstance(v, str) and k not in _RESERVED_KEYS and v.strip()}
            pinyin = entry.get("pinyin", "") if isinstance(entry.get("pinyin", ""), str) else ""
            contexts = parse_contexts(entry.get("context"))
            for lang, text in translations.items():
                norm = normalize_for_match(text)
                if norm:
                    terms.append(_Term(norm, lang, str(entry_id), translations, pinyin, contexts))
        self._terms = terms

    def __len__(self) -> int:
        return len(self._raw)

    # -------------------------------------------------------------- lookup
    def lookup(self, text: str, target_code: str,
               active_domain: Optional[str] = GENERAL) -> Optional[GlossaryMatch]:
        query = normalize_for_match(text)
        if not query:
            return None
        terms = self._terms  # snapshot (list is replaced atomically on rebuild)
        best: Optional[GlossaryMatch] = None
        best_key: Tuple[int, int, int] = (-1, -1, -1)
        lq = len(query)
        for term in terms:
            if target_code not in term.translations:
                continue
            lt = len(term.norm)
            # cheap upper bound: ratio <= 2*min/(la+lb)
            if 200 * min(lq, lt) < self.threshold * (lq + lt):
                continue
            base = ratio(query, term.norm)
            if base < self.threshold:
                continue
            adjusted = max(0, min(100, base + context_adjustment(term.contexts, active_domain)))
            if adjusted < self.threshold:
                continue
            matched = bool(term.contexts) and active_domain in term.contexts
            key = (adjusted, base, int(matched))
            if key > best_key:
                best_key = key
                pinyin = term.pinyin if (target_code == "zh" or has_han(text)) else ""
                best = GlossaryMatch(
                    translated=term.translations[target_code], score=adjusted, base_score=base,
                    pinyin=pinyin, entry_id=term.entry_id, contexts=term.contexts,
                    context_matched=matched, matched_term=term.norm)
        return best

    # ---------------------------------------------------------- persistence
    def add_entry(self, source_text: str, source_lang: str, target_lang: str,
                  translated: str, context: str = GLOBAL_CONTEXT, pinyin: str = "") -> str:
        """Store a translation the user approved ("+ Save"). Returns the entry id.

        If an entry with the same source text already exists, the target
        language is added to it (an existing translation is never overwritten).
        """
        source_text, translated = source_text.strip(), translated.strip()
        if not source_text or not translated:
            raise ValueError("source text and translation must not be empty")
        if source_lang == target_lang:
            raise ValueError("source and target language are identical")
        norm = normalize_for_match(source_text)
        with self._lock:
            for entry_id, entry in self._raw.items():
                if not isinstance(entry, dict):
                    continue
                for key, value in entry.items():
                    if key in _RESERVED_KEYS or not isinstance(value, str):
                        continue
                    if normalize_for_match(value) == norm:
                        entry.setdefault(target_lang, translated)
                        if pinyin and not entry.get("pinyin"):
                            entry["pinyin"] = pinyin
                        self._rebuild()
                        self.save()
                        return str(entry_id)
            entry: Dict[str, str] = {source_lang: source_text, target_lang: translated}
            if pinyin:
                entry["pinyin"] = pinyin
            entry["context"] = context or GLOBAL_CONTEXT
            entry_id = self._next_id()
            self._raw[entry_id] = entry
            self._rebuild()
            self.save()
            return entry_id

    def _next_id(self) -> str:
        nums = [int(m.group(1)) for k in self._raw
                if (m := re.fullmatch(r"UI_(\d+)", str(k)))]
        return f"UI_{(max(nums) if nums else 0) + 1:03d}"
