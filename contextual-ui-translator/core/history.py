"""Persistent translation history (last N captures) stored as JSON."""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import List

log = logging.getLogger(__name__)


class HistoryStore:
    def __init__(self, path: os.PathLike, max_entries: int = 100):
        self.path = Path(path)
        self.max_entries = max_entries
        self._lock = threading.Lock()

    def load(self) -> List[dict]:
        if not self.path.exists():
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, list) else []
        except (OSError, ValueError) as exc:
            log.warning("Could not read history %s: %s", self.path, exc)
            return []

    def add(self, target_lang: str, items: List[dict], context: str = "") -> None:
        if not items:
            return
        with self._lock:
            history = self.load()
            history.insert(0, {"time": datetime.now().strftime("%d/%m %H:%M"),
                               "target_lang": target_lang, "context": context, "items": items})
            self._write(history[: self.max_entries])

    def clear(self) -> None:
        with self._lock:
            self._write([])

    def _write(self, data: list) -> None:
        try:
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError as exc:
            log.warning("Could not write history %s: %s", self.path, exc)
