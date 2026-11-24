"""Settings dialog: hotkey, Tesseract path, OCR language packs, layout toggles."""
from __future__ import annotations

import os
from typing import Dict, List

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QGridLayout, QHBoxLayout,
                             QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout)

from config import Config, find_tesseract, normalize_hotkey
from core.languages import PREFERRED_OCR_LANGS
from ui.styles import DIALOG_QSS


class SettingsDialog(QDialog):
    def __init__(self, config: Config, installed_langs: List[str], parent=None):
        super().__init__(parent)
        self.config = config
        self.installed = [l for l in PREFERRED_OCR_LANGS if l in installed_langs]
        self.setWindowTitle("Settings \u2013 Contextual UI Screen Translator")
        self.setStyleSheet(DIALOG_QSS)
        self.setMinimumWidth(460)

        root = QVBoxLayout(self)
        root.setSpacing(10)

        root.addWidget(self._section("Capture hotkey"))
        self.hotkey_edit = QLineEdit(config.get("hotkey"))
        self.hotkey_edit.setPlaceholderText("e.g. f8 or ctrl+shift+t")
        root.addWidget(self.hotkey_edit)

        root.addWidget(self._section("Tesseract OCR executable"))
        row = QHBoxLayout()
        self.tess_edit = QLineEdit(config.get("tesseract_path"))
        self.tess_edit.setPlaceholderText("Leave empty to auto-detect")
        browse = QPushButton("Browse\u2026")
        browse.clicked.connect(self._browse)
        row.addWidget(self.tess_edit, stretch=1)
        row.addWidget(browse)
        root.addLayout(row)
        self.tess_status = QLabel()
        self.tess_status.setObjectName("Status")
        root.addWidget(self.tess_status)
        self.tess_edit.textChanged.connect(self._refresh_status)
        self._refresh_status()

        root.addWidget(self._section("OCR language packs (installed)"))
        chosen = config.get("ocr_languages") or self.installed
        self.lang_boxes: Dict[str, QCheckBox] = {}
        grid = QGridLayout()
        for i, lang in enumerate(self.installed):
            box = QCheckBox(lang)
            box.setChecked(lang in chosen)
            self.lang_boxes[lang] = box
            grid.addWidget(box, i // 4, i % 4)
        if self.installed:
            root.addLayout(grid)
        else:
            root.addWidget(QLabel("No supported packs found. Check the Tesseract path above."))

        root.addWidget(self._section("Recognition"))
        self.group_cb = QCheckBox("Merge wrapped lines into paragraphs before translating")
        self.group_cb.setChecked(config.get("auto_group_paragraphs"))
        self.invert_cb = QCheckBox("Automatically invert Dark Mode screenshots")
        self.invert_cb.setChecked(config.get("dark_mode_auto_invert"))
        root.addWidget(self.group_cb)
        root.addWidget(self.invert_cb)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _section(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("Section")
        return label

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select tesseract executable", "",
                                              "Executable (tesseract.exe tesseract);;All files (*)")
        if path:
            self.tess_edit.setText(path)

    def _refresh_status(self) -> None:
        found = find_tesseract(self.tess_edit.text().strip())
        self.tess_status.setText(f"Detected: {found}" if found else "Tesseract was not found.")

    def accept(self) -> None:
        if normalize_hotkey(self.hotkey_edit.text()) is None:
            QMessageBox.warning(self, "Invalid hotkey", "Use letters, digits and '+', e.g. f8 or ctrl+shift+t.")
            return
        path = self.tess_edit.text().strip()
        if path and not os.path.isfile(path):
            QMessageBox.warning(self, "Tesseract not found", "That file does not exist.")
            return
        if self.lang_boxes and not any(b.isChecked() for b in self.lang_boxes.values()):
            QMessageBox.warning(self, "OCR languages", "Select at least one language pack.")
            return
        super().accept()

    def values(self) -> dict:
        checked = [l for l, b in self.lang_boxes.items() if b.isChecked()]
        return {
            "hotkey": normalize_hotkey(self.hotkey_edit.text()),
            "tesseract_path": self.tess_edit.text().strip(),
            # empty list = "use every installed pack" (auto)
            "ocr_languages": [] if set(checked) == set(self.installed) else checked,
            "auto_group_paragraphs": self.group_cb.isChecked(),
            "dark_mode_auto_invert": self.invert_cb.isChecked(),
        }
