"""Persistent user settings (``config.json``) and Tesseract auto-discovery."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from core.languages import DEFAULT_TARGET_LANG, LANG_MAP, PREFERRED_OCR_LANGS  # noqa: F401 (re-export)

log = logging.getLogger(__name__)

APP_NAME = "Contextual UI Screen Translator"
APP_SERVER_NAME = "ContextualUITranslatorSingleInstance_v3"
MAX_HISTORY = 100
BASE_DIR = Path(__file__).resolve().parent

DEFAULTS: Dict[str, object] = {
    "last_target_lang": DEFAULT_TARGET_LANG,
    "hotkey": "f8",
    "tesseract_path": "",
    "auto_group_paragraphs": True,
    "dark_mode_auto_invert": True,
    "ocr_languages": [],          # empty = every installed pack from PREFERRED_OCR_LANGS
}
_HOTKEY_RE = re.compile(r"^[a-z0-9]+(\+[a-z0-9]+)*$")


def data_dir() -> Path:
    """Folder holding config/history/glossary (override with ``CUT_DATA_DIR``)."""
    override = os.environ.get("CUT_DATA_DIR")
    path = Path(override) if override else BASE_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def normalize_hotkey(value: object) -> Optional[str]:
    """``' Ctrl + Shift+T '`` -> ``'ctrl+shift+t'``; ``None`` if it is not a valid combo."""
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r"\s*\+\s*", "+", value.strip().lower())
    return cleaned if _HOTKEY_RE.match(cleaned) else None


def sanitize(raw: dict) -> dict:
    """Merge ``raw`` over the defaults, discarding values of the wrong type."""
    out = dict(DEFAULTS)
    out["ocr_languages"] = []
    if raw.get("last_target_lang") in LANG_MAP:
        out["last_target_lang"] = raw["last_target_lang"]
    hotkey = normalize_hotkey(raw.get("hotkey"))
    if hotkey:
        out["hotkey"] = hotkey
    if isinstance(raw.get("tesseract_path"), str):
        out["tesseract_path"] = raw["tesseract_path"].strip()
    for key in ("auto_group_paragraphs", "dark_mode_auto_invert"):
        if isinstance(raw.get(key), bool):
            out[key] = raw[key]
    langs = raw.get("ocr_languages")
    if isinstance(langs, list):
        out["ocr_languages"] = [l for l in langs if isinstance(l, str) and l in PREFERRED_OCR_LANGS]
    return out


class Config:
    def __init__(self, path: Optional[os.PathLike] = None):
        self.path = Path(path) if path else data_dir() / "config.json"
        self.values: dict = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        raw: dict = {}
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    loaded = json.load(fh)
                raw = loaded if isinstance(loaded, dict) else {}
            except (OSError, ValueError) as exc:
                log.warning("Ignoring unreadable config %s: %s", self.path, exc)
        self.values = sanitize(raw)
        if not self.path.exists():
            self.save()          # auto-create on first run

    def save(self) -> None:
        try:
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.values, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError as exc:
            log.warning("Could not save config: %s", exc)

    def get(self, key: str):
        return self.values[key]

    def update(self, **changes) -> None:
        self.values = sanitize({**self.values, **changes})
        self.save()


def tesseract_candidates() -> List[str]:
    candidates: List[str] = []
    for env in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env)
        if base:
            candidates.append(os.path.join(base, "Tesseract-OCR", "tesseract.exe"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(os.path.join(local, "Programs", "Tesseract-OCR", "tesseract.exe"))
    candidates += [r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                   r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                   "/usr/bin/tesseract", "/usr/local/bin/tesseract", "/opt/homebrew/bin/tesseract"]
    return candidates


def find_tesseract(configured: str = "") -> Optional[str]:
    """Locate tesseract: user setting -> ``PATH`` -> well-known install folders."""
    if configured and os.path.isfile(configured):
        return configured
    found = shutil.which("tesseract")
    if found:
        return found
    for candidate in tesseract_candidates():
        if os.path.isfile(candidate):
            return candidate
    return None
