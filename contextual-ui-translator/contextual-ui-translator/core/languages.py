"""Supported languages and helpers to resolve user-typed language names."""
from __future__ import annotations

from typing import Dict, Optional

DEFAULT_TARGET_LANG = "Vietnamese"

# Display name -> glossary code ("db") and Google Translate code ("gt")
LANG_MAP: Dict[str, Dict[str, str]] = {
    "English": {"db": "en", "gt": "en"},
    "Vietnamese": {"db": "vi", "gt": "vi"},
    "Chinese (Simplified)": {"db": "zh", "gt": "zh-CN"},
    "Chinese (Traditional)": {"db": "zh", "gt": "zh-TW"},
    "Japanese": {"db": "ja", "gt": "ja"},
    "Korean": {"db": "ko", "gt": "ko"},
    "French": {"db": "fr", "gt": "fr"},
    "German": {"db": "de", "gt": "de"},
    "Spanish": {"db": "es", "gt": "es"},
    "Russian": {"db": "ru", "gt": "ru"},
    "Thai": {"db": "th", "gt": "th"},
}

# Tesseract language packs the app tries to load when they are installed
PREFERRED_OCR_LANGS = ["eng", "vie", "chi_sim", "chi_tra", "jpn", "kor",
                       "fra", "deu", "rus", "spa", "tha"]


def lang_codes(name: str) -> Dict[str, str]:
    """Return {"db": ..., "gt": ...} for a display name (English as fallback)."""
    return LANG_MAP.get(name, LANG_MAP["English"])


def find_language(text: str) -> Optional[str]:
    """Resolve what a user typed into the language box to a canonical name.

    Accepts an exact (case-insensitive) match or a *unique* prefix
    ("jap" -> "Japanese"). Returns None while the text is not a confirmed
    language, which is what lets the UI avoid translating on every keystroke.
    """
    query = (text or "").strip().casefold()
    if not query:
        return None
    for name in LANG_MAP:
        if name.casefold() == query:
            return name
    matches = [n for n in LANG_MAP if n.casefold().startswith(query)]
    return matches[0] if len(matches) == 1 else None
