"""Hybrid translation pipeline: Glossary -> LRU cache -> batched Google Translate.

The class is Qt-free and the network backend is injectable, so the whole
routing logic (glossary hit, cache hit, batching, per-line fallback, offline
handling) is unit-tested with a fake backend.
"""
from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .context_detector import AppContext, GENERAL
from .glossary import Glossary
from .languages import lang_codes
from .text_utils import clean_cjk_spaces, collapse_ws, decompose_ui_text, has_han

log = logging.getLogger(__name__)

Backend = Callable[[str, str, str], str]  # (text, source_lang, target_lang) -> translation


class Source(str, Enum):
    GLOSSARY = "glossary"
    GOOGLE = "google"
    CACHE = "cache"
    ERROR = "error"
    EMPTY = "empty"


@dataclass
class TranslationResult:
    source_text: str
    translated: str
    source: Source
    pinyin: str = ""
    score: Optional[int] = None
    entry_context: str = ""     # glossary entry's domain label when it matched the active app
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.source in (Source.GLOSSARY, Source.GOOGLE, Source.CACHE)

    @property
    def badge(self) -> str:
        if self.source is Source.GLOSSARY:
            tail = f" \u2022 {self.entry_context}" if self.entry_context else ""
            return f"Local DB \u2022 {self.score}%{tail}"
        if self.source is Source.GOOGLE:
            return "Google"
        if self.source is Source.CACHE:
            return "Cached"
        if self.source is Source.ERROR:
            return "Offline / Error"
        return ""

    def as_tuple(self) -> Tuple[str, str, str, Optional[int]]:
        return self.translated, self.badge, self.pinyin, self.score

    def to_history_item(self) -> dict:
        return {"raw": self.source_text, "translated": self.translated,
                "pinyin": self.pinyin, "source": self.badge}


class LRUCache:
    """Small thread-safe LRU cache."""

    def __init__(self, maxsize: int = 512):
        self.maxsize = maxsize
        self._data: "OrderedDict[tuple, str]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            if key not in self._data:
                return None
            self._data.move_to_end(key)
            return self._data[key]

    def put(self, key, value: str) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key) -> bool:
        return key in self._data


def run_with_timeout(fn: Callable, timeout: float, *args):
    """Run ``fn(*args)`` in a daemon thread; raise ``TimeoutError`` after ``timeout`` s.

    ``deep-translator`` issues HTTP requests without a timeout, which could
    leave the loading state spinning forever on a black-holed network.
    """
    box: Dict[str, object] = {}

    def target():
        try:
            box["value"] = fn(*args)
        except BaseException as exc:  # noqa: BLE001 - re-raised in caller
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"translation backend timed out after {timeout:.0f}s")
    if "error" in box:
        raise box["error"]  # type: ignore[misc]
    return box.get("value")


def google_backend(text: str, source: str, target: str) -> str:
    """Default backend: Google Translate through ``deep-translator`` (lazy import)."""
    from deep_translator import GoogleTranslator
    return GoogleTranslator(source=source, target=target).translate(text) or ""


def _source_bucket(text: str, gt_target: str) -> str:
    """Short CJK strings are mis-detected by 'auto'; pin them to Chinese like the prototype."""
    return "zh-CN" if (has_han(text) and gt_target not in ("zh-CN", "zh-TW")) else "auto"


class HybridTranslator:
    def __init__(self, glossary: Glossary, backend: Optional[Backend] = None,
                 cache: Optional[LRUCache] = None, timeout: float = 8.0,
                 batch_char_limit: int = 3500):
        self.glossary = glossary
        self.backend = backend or google_backend
        self.cache = cache if cache is not None else LRUCache()
        self.timeout = timeout
        self.batch_char_limit = batch_char_limit

    # ----------------------------------------------------------------- API
    def translate_texts(self, texts: Sequence[str], target_lang_name: str,
                        context: Optional[AppContext] = None) -> List[TranslationResult]:
        codes = lang_codes(target_lang_name)
        db_code, gt_code = codes["db"], codes["gt"]
        domain = context.domain if context else GENERAL
        self.glossary.refresh_if_changed()

        results: List[Optional[TranslationResult]] = [None] * len(texts)
        pending: Dict[str, List[Tuple[int, str]]] = {}   # source bucket -> [(index, text)]

        for i, raw in enumerate(texts):
            text = collapse_ws(clean_cjk_spaces(raw or ""))
            if not text:
                results[i] = TranslationResult(raw or "", "", Source.EMPTY)
                continue
            match = self.glossary.lookup(text, db_code, domain)
            if match:
                parts = decompose_ui_text(text)
                translated = match.translated + parts.ellipsis
                if parts.shortcut:
                    translated += f"  ({parts.shortcut})"
                results[i] = TranslationResult(
                    text, translated, Source.GLOSSARY, pinyin=match.pinyin, score=match.score,
                    entry_context=match.context_label if match.context_matched else "")
                continue
            cached = self.cache.get((text, gt_code))
            if cached is not None:
                results[i] = TranslationResult(text, cached, Source.CACHE)
                continue
            pending.setdefault(_source_bucket(text, gt_code), []).append((i, text))

        for bucket, items in pending.items():
            self._translate_pending(bucket, items, gt_code, results)

        return [r for r in results if r is not None]  # all slots are filled by now

    def translate_one(self, text: str, target_lang_name: str,
                      context: Optional[AppContext] = None) -> TranslationResult:
        return self.translate_texts([text], target_lang_name, context)[0]

    # ------------------------------------------------------------ internals
    def _chunks(self, items: List[Tuple[int, str]]) -> List[List[Tuple[int, str]]]:
        chunks, current, size = [], [], 0
        for item in items:
            cost = len(item[1]) + 1
            if current and size + cost > self.batch_char_limit:
                chunks.append(current)
                current, size = [], 0
            current.append(item)
            size += cost
        if current:
            chunks.append(current)
        return chunks

    def _call(self, text: str, source: str, target: str) -> str:
        return run_with_timeout(self.backend, self.timeout, text, source, target)

    def _translate_pending(self, source: str, items: List[Tuple[int, str]], gt_code: str,
                           results: List[Optional[TranslationResult]]) -> None:
        for chunk in self._chunks(items):
            translations = self._translate_chunk(source, [t for _, t in chunk], gt_code)
            for (idx, text), outcome in zip(chunk, translations):
                if isinstance(outcome, Exception):
                    results[idx] = TranslationResult(
                        text, "", Source.ERROR, error=str(outcome)[:120] or type(outcome).__name__)
                else:
                    self.cache.put((text, gt_code), outcome)
                    results[idx] = TranslationResult(text, outcome, Source.GOOGLE)

    def _translate_chunk(self, source: str, texts: List[str], gt_code: str):
        """Return one ``str`` (or ``Exception``) per input text."""
        if len(texts) > 1:
            try:
                joined = self._call("\n".join(texts), source, gt_code) or ""
                parts = [p.strip() for p in joined.replace("\r", "").split("\n")]
                if len(parts) == len(texts) and all(parts):
                    return parts
                log.info("batch split mismatch (%d != %d); falling back to per-line",
                         len(parts), len(texts))
            except Exception as exc:  # noqa: BLE001
                log.info("batch translation failed (%s); falling back to per-line", exc)
        outcomes: list = []
        failure: Optional[Exception] = None
        for text in texts:
            if failure is not None:   # circuit breaker: assume the network is down
                outcomes.append(failure)
                continue
            try:
                value = (self._call(text, source, gt_code) or "").strip()
                outcomes.append(value if value else RuntimeError("empty translation"))
            except Exception as exc:  # noqa: BLE001
                failure = exc
                outcomes.append(exc)
        return outcomes
