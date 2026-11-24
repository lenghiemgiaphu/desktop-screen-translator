"""Background jobs: OCR + translation run off the GUI thread (QRunnable on QThreadPool)."""
from __future__ import annotations

import logging
from typing import List, Optional

from PyQt5.QtCore import QObject, QRunnable, pyqtSignal

from core.context_detector import AppContext
from ui.services import AppServices

log = logging.getLogger(__name__)


class WorkerSignals(QObject):
    """Signals are emitted from worker threads and delivered queued to the GUI thread."""
    stage = pyqtSignal(int, str)          # job id, status text
    ocr_done = pyqtSignal(int, object)    # job id, OcrResult
    finished = pyqtSignal(int, object)    # job id, List[TranslationResult]
    failed = pyqtSignal(int, str, str)    # job id, kind ("tesseract" | "error"), message


class PipelineJob(QRunnable):
    """Either OCR + translate a captured image, or just re-translate known texts."""

    def __init__(self, job_id: int, services: AppServices, signals: WorkerSignals, target_lang: str,
                 context: Optional[AppContext], image=None, texts: Optional[List[str]] = None):
        super().__init__()
        self.job_id = job_id
        self.services = services
        self.signals = signals
        self.target_lang = target_lang
        self.context = context
        self.image = image
        self.texts = texts or []

    def run(self) -> None:
        try:
            texts = self.texts
            if self.image is not None:
                self.signals.stage.emit(self.job_id, "Recognizing text\u2026")
                ocr = self.services.ocr.recognize(self.image)
                self.signals.ocr_done.emit(self.job_id, ocr)
                texts = ocr.texts
            results = []
            if texts:
                self.signals.stage.emit(self.job_id, "Translating\u2026")
                results = self.services.translator.translate_texts(texts, self.target_lang, self.context)
            self.signals.finished.emit(self.job_id, results)
        except Exception as exc:  # noqa: BLE001 - report every failure to the UI
            log.exception("pipeline job %s failed", self.job_id)
            kind = "tesseract" if type(exc).__name__ == "TesseractNotFoundError" else "error"
            self.signals.failed.emit(self.job_id, kind, str(exc))
