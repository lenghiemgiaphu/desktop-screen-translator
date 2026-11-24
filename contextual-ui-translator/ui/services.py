"""Application services shared by the UI components (composition root lives in main.py)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from config import Config
from core.glossary import Glossary
from core.history import HistoryStore
from core.ocr_engine import OcrEngine
from core.translator import HybridTranslator


@dataclass
class AppServices:
    config: Config
    glossary: Glossary
    translator: HybridTranslator
    ocr: OcrEngine
    history: HistoryStore
    pool: Any            # QThreadPool
