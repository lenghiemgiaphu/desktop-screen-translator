"""Floating, non-blocking result window with source badges and a one-click "+ Save"."""
from __future__ import annotations

import logging
from typing import List, Optional

from PyQt5.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QGuiApplication
from PyQt5.QtWidgets import (QApplication, QComboBox, QCompleter, QHBoxLayout, QLabel, QLineEdit,
                             QProgressBar, QPushButton, QScrollArea, QSizeGrip, QVBoxLayout, QWidget)

from core.context_detector import GENERAL, GLOBAL_CONTEXT, AppContext
from core.languages import LANG_MAP, find_language, lang_codes
from core.text_utils import guess_lang_code, has_han
from core.translator import Source, TranslationResult
from ui.services import AppServices
from ui.styles import CARD_QSS, COPIED_QSS, OVERLAY_QSS, badge_qss
from ui.workers import PipelineJob, WorkerSignals

log = logging.getLogger(__name__)

POPUP_WIDTH = 445
BADGE_KIND = {Source.GLOSSARY: "local", Source.GOOGLE: "google", Source.CACHE: "cache", Source.ERROR: "error"}


def badge_kind_from_text(text: str) -> str:
    """Map a stored badge string (history) back to a style."""
    for prefix, kind in (("Local", "local"), ("Google", "google"), ("Cached", "cache"), ("Offline", "error")):
        if text.startswith(prefix):
            return kind
    return "history"


class ResultCard(QWidget):
    save_requested = pyqtSignal(object)      # emits the TranslationResult to store

    def __init__(self, raw_text: str, translated_text: str, pinyin: str = "", badge_text: str = "",
                 badge_kind: str = "history", context_text: str = "", error_text: str = "",
                 meta_tag: str = "", result: Optional[TranslationResult] = None, can_save: bool = False):
        super().__init__()
        self.raw_text = raw_text
        self.translated_text = translated_text
        self.result = result
        self.setObjectName("ResultCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(CARD_QSS)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 8, 10, 8)
        layout.setSpacing(6)

        if meta_tag:
            meta = QLabel(meta_tag)
            meta.setStyleSheet("color: #6B7280; font-size: 8pt; background: transparent;")
            layout.addWidget(meta)

        if badge_text or context_text or can_save:
            pills = QHBoxLayout()
            pills.setSpacing(6)
            if badge_text:
                badge = QLabel(badge_text)
                badge.setStyleSheet(badge_qss(badge_kind))
                pills.addWidget(badge)
            if context_text:
                ctx = QLabel(context_text)
                ctx.setStyleSheet(badge_qss("context"))
                pills.addWidget(ctx)
            pills.addStretch()
            self.save_btn: Optional[QPushButton] = None
            if can_save:
                self.save_btn = QPushButton("+ Save")
                self.save_btn.setObjectName("SaveBtn")
                self.save_btn.setFixedHeight(20)
                self.save_btn.setCursor(Qt.PointingHandCursor)
                self.save_btn.setToolTip("Save this translation to the local glossary (database.json)")
                self.save_btn.clicked.connect(lambda: self.save_requested.emit(self.result))
                pills.addWidget(self.save_btn)
            layout.addLayout(pills)
        else:
            self.save_btn = None

        has_cjk_raw = has_han(raw_text)
        has_cjk_trans = has_han(translated_text)
        display_raw, display_trans = raw_text, translated_text
        if pinyin and has_cjk_raw:
            display_raw = f"{raw_text}  ({pinyin})"
        elif pinyin and has_cjk_trans:
            display_trans = f"{translated_text}  ({pinyin})"

        raw_row = QHBoxLayout()
        raw_row.setSpacing(8)
        self.raw_label = QLabel(display_raw)
        self.raw_label.setFont(QFont("Segoe UI", 9))
        self.raw_label.setStyleSheet("color: #9CA3AF; background: transparent;")
        self.raw_label.setWordWrap(True)
        self.raw_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.btn_copy_raw = self._copy_button("CopyRawBtn", 22, "Copy original text", raw_text)
        raw_row.addWidget(self.raw_label, stretch=1)
        raw_row.addWidget(self.btn_copy_raw, alignment=Qt.AlignTop)

        trans_row = QHBoxLayout()
        trans_row.setSpacing(8)
        if error_text:
            shown = "\u26a0 Translation unavailable. Check your connection and try again."
            self.trans_label = QLabel(shown)
            self.trans_label.setToolTip(error_text)
            self.trans_label.setStyleSheet("color: #FBBF24; background: transparent; font-size: 9pt;")
        else:
            self.trans_label = QLabel(display_trans)
            self.trans_label.setFont(QFont("Segoe UI", 10, QFont.DemiBold))
            self.trans_label.setStyleSheet("color: #F3F4F6; background: transparent;")
        self.trans_label.setWordWrap(True)
        self.trans_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.btn_copy_trans = self._copy_button("CopyTransBtn", 23, "Copy translation", translated_text)
        self.btn_copy_trans.setEnabled(bool(translated_text) and not error_text)
        trans_row.addWidget(self.trans_label, stretch=1)
        trans_row.addWidget(self.btn_copy_trans, alignment=Qt.AlignTop)

        layout.addLayout(raw_row)
        layout.addLayout(trans_row)

    @classmethod
    def from_result(cls, result: TranslationResult, context_label: str = "") -> "ResultCard":
        failed = result.source is Source.ERROR
        return cls(result.source_text, result.translated, pinyin=result.pinyin, badge_text=result.badge,
                   badge_kind=BADGE_KIND.get(result.source, "history"),
                   context_text=f"Context: {context_label}" if context_label else "",
                   error_text=result.error or "error" if failed else "", result=result,
                   can_save=result.source in (Source.GOOGLE, Source.CACHE))

    def _copy_button(self, name: str, height: int, tip: str, text: str) -> QPushButton:
        btn = QPushButton("Copy")
        btn.setObjectName(name)
        btn.setFixedSize(46, height)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip(tip)
        btn.clicked.connect(lambda: self._copy(text, btn))
        return btn

    def _copy(self, text: str, btn: QPushButton) -> None:
        QApplication.clipboard().setText(text)
        btn.setText("\u2713")
        btn.setStyleSheet(COPIED_QSS)
        QTimer.singleShot(1000, lambda: (btn.setText("Copy"), btn.setStyleSheet("")))

    def mark_saved(self) -> None:
        if self.save_btn:
            self.save_btn.setText("\u2713 Saved")
            self.save_btn.setEnabled(False)

    def mark_save_failed(self, reason: str) -> None:
        if self.save_btn:
            self.save_btn.setText("Not saved")
            self.save_btn.setToolTip(reason)


class MultiLineOverlay(QWidget):
    def __init__(self, services: AppServices, rect=None, image=None,
                 context: Optional[AppContext] = None, start_in_history: bool = False):
        super().__init__()
        self.services = services
        self.context = context
        self.has_capture = image is not None
        self.target_lang = services.config.get("last_target_lang")
        self.texts: List[str] = []
        self.results: List[TranslationResult] = []
        self.job_id = 0
        self.loading = False
        self.loading_label: Optional[QLabel] = None
        self.history_mode = start_in_history
        self.old_pos: Optional[QPoint] = None
        self._sized_to_content = False

        self.signals = WorkerSignals(self)
        self.signals.stage.connect(self.on_stage)
        self.signals.ocr_done.connect(self.on_ocr_done)
        self.signals.finished.connect(self.on_finished)
        self.signals.failed.connect(self.on_failed)

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(360, 140)
        self._place(rect, rows=4 if start_in_history else 2)

        self.container = QWidget(self)
        self.container.setObjectName("MainContainer")
        self.container.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(OVERLAY_QSS)
        self._build_ui()

        window_layout = QVBoxLayout(self)
        window_layout.setContentsMargins(0, 0, 0, 0)
        window_layout.addWidget(self.container)

        self.update_view_state(initial=True)
        if image is not None:
            self.start_job(image=image)

    # ------------------------------------------------------------------ layout
    def _context_label(self) -> str:
        return self.context.label if self.context else ""

    def _place(self, rect, rows: int) -> None:
        screen = QGuiApplication.screenAt(rect.center()) if rect is not None else None
        screen = screen or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry()
        height = min(max(rows * 82 + 72, 160), 520)
        if rect is not None:
            x = rect.right() + 12
            if x + POPUP_WIDTH > avail.right():
                x = max(avail.left() + 12, rect.left() - POPUP_WIDTH - 12)
            y = min(rect.top(), avail.bottom() - height - 20)
            y = max(avail.top() + 12, y)
        else:
            x = avail.left() + (avail.width() - POPUP_WIDTH) // 2
            y = avail.top() + (avail.height() - height) // 2
        self.setGeometry(x, y, POPUP_WIDTH, height)

    def _build_ui(self) -> None:
        main = QVBoxLayout(self.container)
        main.setContentsMargins(12, 10, 12, 12)
        main.setSpacing(9)

        header = QHBoxLayout()
        header.setSpacing(7)
        self.title_lbl = QLabel("Translate to:")
        self.title_lbl.setObjectName("HeaderTitle")

        names = list(LANG_MAP.keys())
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(names)
        self.lang_combo.setEditable(True)
        self.lang_combo.setInsertPolicy(QComboBox.NoInsert)
        self.lang_combo.lineEdit().setPlaceholderText("Search language...")
        self.lang_combo.setCurrentText(self.target_lang)
        completer = QCompleter(names, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        self.lang_combo.setCompleter(completer)
        # NOTE: deliberately NOT connected to currentTextChanged. That signal fires on every
        # keystroke while filtering, which triggered translations (and history writes) for
        # half-typed text. Only a *confirmed* choice reaches _commit_language.
        self.lang_combo.activated[int].connect(lambda i: self._commit_language(self.lang_combo.itemText(i)))
        completer.activated[str].connect(self._commit_language)
        self.lang_combo.lineEdit().editingFinished.connect(
            lambda: self._commit_language(self.lang_combo.currentText()))

        self.history_btn = QPushButton("History")
        self.history_btn.setObjectName("HeaderBtn")
        self.history_btn.setCursor(Qt.PointingHandCursor)
        self.history_btn.setToolTip("View last 100 translations")
        self.history_btn.clicked.connect(self.toggle_history_mode)

        close_btn = QPushButton("\u00d7")
        close_btn.setObjectName("CloseBtn")
        close_btn.setFixedSize(26, 26)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setToolTip("Close (Esc)")
        close_btn.clicked.connect(self.close)

        header.addWidget(self.title_lbl)
        header.addWidget(self.lang_combo)
        header.addStretch()
        header.addWidget(self.history_btn)
        header.addWidget(close_btn)
        main.addLayout(header)

        self.history_toolbar = QWidget()
        bar = QHBoxLayout(self.history_toolbar)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(6)
        self.history_search = QLineEdit()
        self.history_search.setObjectName("HistorySearch")
        self.history_search.setPlaceholderText("Search translation history...")
        self.history_search.textChanged.connect(self.render_history)
        self.clear_hist_btn = QPushButton("Clear All")
        self.clear_hist_btn.setObjectName("DangerBtn")
        self.clear_hist_btn.setCursor(Qt.PointingHandCursor)
        self.clear_hist_btn.clicked.connect(self.handle_clear_history)
        bar.addWidget(self.history_search, stretch=1)
        bar.addWidget(self.clear_hist_btn)
        main.addWidget(self.history_toolbar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.viewport().setStyleSheet("background: transparent;")
        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("ScrollContent")
        self.scroll_content.setStyleSheet("QWidget#ScrollContent { background: transparent; }")
        self.lines_layout = QVBoxLayout(self.scroll_content)
        self.lines_layout.setContentsMargins(0, 0, 2, 0)
        self.lines_layout.setSpacing(7)
        self.scroll.setWidget(self.scroll_content)
        main.addWidget(self.scroll)

        self.sizegrip = QSizeGrip(self)
        self.sizegrip.setStyleSheet("background: transparent;")

    # ------------------------------------------------------------ window events
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.sizegrip.move(self.width() - 16, self.height() - 16)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.old_pos = event.globalPos()

    def mouseMoveEvent(self, event):
        if self.old_pos is not None:
            delta = QPoint(event.globalPos() - self.old_pos)
            self.move(self.x() + delta.x(), self.y() + delta.y())
            self.old_pos = event.globalPos()

    def mouseReleaseEvent(self, event):
        self.old_pos = None

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()

    # ------------------------------------------------------------- card helpers
    def clear_cards(self) -> None:
        self.loading_label = None
        while self.lines_layout.count():
            item = self.lines_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _hint(self, text: str, error: bool = False) -> QLabel:
        label = QLabel(text)
        label.setObjectName("ErrorHint" if error else "Hint")
        label.setWordWrap(True)
        return label

    # ------------------------------------------------------------ language box
    def _set_combo_text(self, name: str) -> None:
        blocked = self.lang_combo.blockSignals(True)
        self.lang_combo.setCurrentText(name)
        self.lang_combo.blockSignals(blocked)

    def _commit_language(self, text: str) -> None:
        """Called only for a confirmed choice (pick, Enter or focus-out), never per keystroke."""
        name = find_language(text)
        self._set_combo_text(name or self.target_lang)      # invalid text reverts silently
        if name is None or name == self.target_lang:
            return
        self.target_lang = name
        self.services.config.update(last_target_lang=name)
        if not self.history_mode and (self.texts or self.results):
            self.start_job(texts=self.texts)

    # ----------------------------------------------------------------- jobs
    def start_job(self, image=None, texts: Optional[List[str]] = None) -> None:
        self.job_id += 1
        self.loading = True
        self.show_loading("Recognizing text\u2026" if image is not None else "Translating\u2026")
        job = PipelineJob(self.job_id, self.services, self.signals, self.target_lang, self.context,
                          image=image, texts=texts)
        self.services.pool.start(job)

    def show_loading(self, message: str) -> None:
        if self.history_mode:
            return
        self.clear_cards()
        self.loading_label = self._hint(message)
        bar = QProgressBar()
        bar.setRange(0, 0)
        bar.setTextVisible(False)
        self.lines_layout.addWidget(self.loading_label)
        self.lines_layout.addWidget(bar)
        self.lines_layout.addStretch()

    def on_stage(self, job_id: int, message: str) -> None:
        if job_id == self.job_id and self.loading_label is not None:
            self.loading_label.setText(message)

    def on_ocr_done(self, job_id: int, ocr) -> None:
        if job_id == self.job_id:
            self.texts = list(ocr.texts)

    def on_finished(self, job_id: int, results) -> None:
        if job_id != self.job_id:
            return                                  # a newer job superseded this one
        self.loading = False
        self.results = [r for r in results if r.source is not Source.EMPTY]
        stored = [r.to_history_item() for r in self.results if r.ok]
        if stored:
            self.services.history.add(self.target_lang, stored, self._context_label())
        if not self.history_mode:
            self.render_results()

    def on_failed(self, job_id: int, kind: str, message: str) -> None:
        if job_id != self.job_id:
            return
        self.loading = False
        if self.history_mode:
            return
        self.clear_cards()
        if kind == "tesseract":
            text = ("Tesseract OCR was not found. Install it, then set its path under "
                    "Settings in the tray menu.")
        else:
            text = f"Something went wrong: {message[:160]}"
        self.lines_layout.addWidget(self._hint(text, error=True))
        self.lines_layout.addStretch()

    # --------------------------------------------------------------- rendering
    def render_results(self) -> None:
        self.clear_cards()
        if not self.results:
            msg = ("No text detected in the captured area. Press the hotkey and try a slightly wider selection."
                   if self.has_capture else "No recent capture. Press the hotkey to select a screen area.")
            self.lines_layout.addWidget(self._hint(msg))
        else:
            for result in self.results:
                card = ResultCard.from_result(result, self._context_label())
                card.save_requested.connect(lambda res, c=card: self.save_to_glossary(res, c))
                self.lines_layout.addWidget(card)
            self._fit_height(len(self.results))
        self.lines_layout.addStretch()

    def _fit_height(self, rows: int) -> None:
        if self._sized_to_content:
            return
        self._sized_to_content = True
        target = min(max(rows * 96 + 72, 160), 520)
        screen = QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()
        bottom = screen.availableGeometry().bottom()
        y = min(self.y(), bottom - target - 12)
        self.setGeometry(self.x(), max(screen.availableGeometry().top() + 8, y), self.width(), target)

    def save_to_glossary(self, result: TranslationResult, card: ResultCard) -> None:
        target_code = lang_codes(self.target_lang)["db"]
        source_code = guess_lang_code(result.source_text)
        if source_code == target_code:
            card.mark_save_failed("Source and target language look identical")
            return
        domain = self.context.domain if self.context else GENERAL
        try:
            self.services.glossary.add_entry(result.source_text, source_code, target_code,
                                             result.translated,
                                             context=GLOBAL_CONTEXT if domain == GENERAL else domain)
            card.mark_saved()
        except Exception as exc:  # noqa: BLE001
            log.exception("could not save glossary entry")
            card.mark_save_failed(str(exc))

    def render_history(self) -> None:
        self.clear_cards()
        keyword = self.history_search.text().strip().lower()
        shown = 0
        for entry in self.services.history.load():
            time_str = entry.get("time", "")
            lang_str = entry.get("target_lang", "")
            ctx = entry.get("context", "")
            for item in entry.get("items", []):
                raw, trans = item.get("raw", ""), item.get("translated", "")
                if keyword and keyword not in raw.lower() and keyword not in trans.lower():
                    continue
                meta = f"{time_str}  \u2022  \u2192 {lang_str}"
                badge = item.get("source", "")
                card = ResultCard(raw, trans, pinyin=item.get("pinyin", ""), meta_tag=meta,
                                  badge_text=badge, badge_kind=badge_kind_from_text(badge),
                                  context_text=f"Context: {ctx}" if ctx else "")
                self.lines_layout.addWidget(card)
                shown += 1
        if shown == 0:
            msg = "No matching results found." if keyword else \
                "History is empty (stores up to the 100 most recent translations)."
            self.lines_layout.addWidget(self._hint(msg))
        self.lines_layout.addStretch()

    def handle_clear_history(self) -> None:
        self.services.history.clear()
        self.render_history()

    # ------------------------------------------------------------- view state
    def toggle_history_mode(self) -> None:
        self.history_mode = not self.history_mode
        self.update_view_state()

    def update_view_state(self, initial: bool = False) -> None:
        self.history_btn.setProperty("active", "true" if self.history_mode else "false")
        self.history_btn.style().unpolish(self.history_btn)
        self.history_btn.style().polish(self.history_btn)
        if self.history_mode:
            self.title_lbl.hide()
            self.lang_combo.hide()
            self.history_toolbar.show()
            self.history_btn.setText("Back")
            self.render_history()
        else:
            self.title_lbl.show()
            self.lang_combo.show()
            self.history_toolbar.hide()
            self.history_btn.setText("History")
            if initial:
                return                                # the first job paints its own loading state
            # Returning from history must neither re-translate nor write history again.
            if self.loading:
                self.show_loading("Translating\u2026")
            else:
                self.render_results()
