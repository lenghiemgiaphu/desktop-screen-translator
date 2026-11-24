"""Contextual UI Screen Translator - application entry point.

    python main.py            start the tray application
    python main.py --check    print an environment diagnosis and exit
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import platform
import shutil
import sys

import config as cfg_mod
from core.context_detector import get_foreground_context
from core.glossary import Glossary
from core.history import HistoryStore
from core.languages import PREFERRED_OCR_LANGS
from core.ocr_engine import OcrEngine, available_languages
from core.translator import HybridTranslator

log = logging.getLogger("app")


def setup_logging() -> None:
    handlers = [logging.StreamHandler()]
    try:
        handlers.append(logging.handlers.RotatingFileHandler(
            cfg_mod.data_dir() / "app.log", maxBytes=200_000, backupCount=1, encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=handlers)


def run_check() -> int:
    """Environment diagnosis, handy before the manual QA checklist."""
    config = cfg_mod.Config()
    print(f"Python        : {platform.python_version()} on {platform.system()} {platform.release()}")
    for module in ("PyQt5", "pytesseract", "PIL", "numpy", "deep_translator", "keyboard", "thefuzz", "rapidfuzz"):
        try:
            __import__(module)
            status = "ok"
        except ImportError:
            status = "MISSING"
        print(f"  {module:<16}{status}")
    tess = cfg_mod.find_tesseract(config.get("tesseract_path"))
    print(f"Tesseract     : {tess or 'NOT FOUND (install it or set the path in Settings)'}")
    if tess:
        OcrEngine.set_tesseract(tess)
        import pytesseract
        print(f"  version     : {pytesseract.get_tesseract_version()}")
        print(f"  OCR langs   : {available_languages(PREFERRED_OCR_LANGS)}")
    glossary = Glossary(cfg_mod.data_dir() / "database.json")
    print(f"Glossary      : {len(glossary)} entries ({glossary.path})")
    print(f"Config        : {config.path}\n  values      : {config.values}")
    print(f"Foreground    : {get_foreground_context()}")
    return 0 if tess else 1


def build_app():
    """Qt-dependent part, kept out of module scope so --check works without PyQt5."""
    from PyQt5.QtCore import QObject, Qt, QThreadPool, QUrl, pyqtSignal
    from PyQt5.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPixmap
    from PyQt5.QtNetwork import QLocalServer, QLocalSocket
    from PyQt5.QtWidgets import QAction, QApplication, QMenu, QSystemTrayIcon

    from ui.overlay import MultiLineOverlay
    from ui.services import AppServices
    from ui.settings_dialog import SettingsDialog
    from ui.snipper import SnippingWidget

    def make_icon() -> QIcon:
        pm = QPixmap(64, 64)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor("#2563EB"))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(4, 4, 56, 56, 14, 14)
        p.setPen(QColor("white"))
        p.setFont(QFont("Segoe UI", 30, QFont.Bold))
        p.drawText(pm.rect(), Qt.AlignCenter, "T")
        p.end()
        return QIcon(pm)

    class AppController(QObject):
        snip_requested = pyqtSignal()      # emitted from the keyboard-hook thread

        def __init__(self, app: QApplication):
            super().__init__()
            self.app = app
            self.config = cfg_mod.Config()
            data = cfg_mod.data_dir()
            glossary_path = data / "database.json"
            bundled = cfg_mod.BASE_DIR / "database.json"
            if not glossary_path.exists() and bundled.exists():
                shutil.copy(bundled, glossary_path)
            self.glossary = Glossary(glossary_path)
            self.history = HistoryStore(data / "history.json", cfg_mod.MAX_HISTORY)
            self.ocr = OcrEngine()
            self._apply_ocr_settings()
            self.services = AppServices(self.config, self.glossary, HybridTranslator(self.glossary),
                                        self.ocr, self.history, QThreadPool.globalInstance())

            self.snipper = SnippingWidget()
            self.snipper.captured.connect(self.on_captured)
            self.overlay = None
            self.hotkey_handle = None

            self.tray = QSystemTrayIcon(make_icon())
            self.menu = QMenu()
            self.act_capture = QAction("", self.app)
            self.act_capture.triggered.connect(self.request_snip)
            act_history = QAction("Translation History (Last 100)", self.app)
            act_history.triggered.connect(self.open_history)
            act_db = QAction("Open database.json", self.app)
            act_db.triggered.connect(self.open_glossary)
            act_settings = QAction("Settings\u2026", self.app)
            act_settings.triggered.connect(self.open_settings)
            act_quit = QAction("Exit", self.app)
            act_quit.triggered.connect(self.app.quit)
            for act in (self.act_capture, act_history, act_db, act_settings):
                self.menu.addAction(act)
            self.menu.addSeparator()
            self.menu.addAction(act_quit)
            self.tray.setContextMenu(self.menu)
            self.tray.show()

            self.snip_requested.connect(self.request_snip)
            self.register_hotkey()
            self.app.aboutToQuit.connect(self.shutdown)
            if not OcrEngine.is_available():
                self.tray.showMessage(cfg_mod.APP_NAME, "Tesseract OCR was not found. Open Settings to set its path.",
                                      QSystemTrayIcon.Warning, 8000)

        # ----------------------------------------------------------- settings
        def _apply_ocr_settings(self) -> None:
            tess = cfg_mod.find_tesseract(self.config.get("tesseract_path"))
            if tess:
                OcrEngine.set_tesseract(tess)
            preferred = self.config.get("ocr_languages") or PREFERRED_OCR_LANGS
            self.ocr.lang = available_languages(preferred) if tess else "eng"
            self.ocr.dark_mode_auto_invert = self.config.get("dark_mode_auto_invert")
            self.ocr.auto_group_paragraphs = self.config.get("auto_group_paragraphs")

        def open_settings(self) -> None:
            installed: list = []
            try:
                import pytesseract
                installed = list(pytesseract.get_languages(config=""))
            except Exception:  # noqa: BLE001
                pass
            dialog = SettingsDialog(self.config, installed)
            if dialog.exec_():
                self.config.update(**dialog.values())
                self._apply_ocr_settings()
                self.register_hotkey()

        # ------------------------------------------------------------- hotkey
        def register_hotkey(self) -> bool:
            self.unregister_hotkey()
            hotkey = self.config.get("hotkey")
            label = hotkey.upper()
            self.act_capture.setText(f"Capture Screen to Translate ({label})")
            self.tray.setToolTip(f"{cfg_mod.APP_NAME} ({label})")
            try:
                import keyboard
                self.hotkey_handle = keyboard.add_hotkey(hotkey, lambda: self.snip_requested.emit())
                log.info("hotkey %s registered", hotkey)
                return True
            except Exception as exc:  # noqa: BLE001 - invalid combo, missing privileges, missing module
                log.warning("hotkey registration failed: %s", exc)
                self.tray.showMessage(
                    cfg_mod.APP_NAME,
                    f"Could not register the hotkey '{label}': {exc}\nUse the tray menu or pick another key in Settings.",
                    QSystemTrayIcon.Warning, 8000)
                return False

        def unregister_hotkey(self) -> None:
            if self.hotkey_handle is not None:
                try:
                    import keyboard
                    keyboard.remove_hotkey(self.hotkey_handle)
                except Exception:  # noqa: BLE001
                    pass
                self.hotkey_handle = None

        # ------------------------------------------------------------ actions
        def request_snip(self) -> None:
            # The foreground window must be sampled *before* the snipping overlay appears.
            self.snipper.start(get_foreground_context())

        def on_captured(self, image, rect, context) -> None:
            if self.overlay is not None:
                self.overlay.close()
            self.overlay = MultiLineOverlay(self.services, rect=rect, image=image, context=context)
            self.overlay.show()

        def open_history(self) -> None:
            if self.overlay is not None:
                self.overlay.close()
            self.overlay = MultiLineOverlay(self.services, start_in_history=True)
            self.overlay.show()

        def open_glossary(self) -> None:
            if self.glossary.path and self.glossary.path.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.glossary.path)))

        def shutdown(self) -> None:
            self.unregister_hotkey()
            self.services.pool.waitForDone(2000)

    def replace_existing_instance(app: QApplication):
        """Ask a running instance to quit so that only one tray icon / hotkey exists."""
        socket = QLocalSocket()
        socket.connectToServer(cfg_mod.APP_SERVER_NAME)
        if socket.waitForConnected(250):
            socket.write(b"QUIT")
            socket.flush()
            socket.waitForBytesWritten(250)
            socket.disconnectFromServer()
        QLocalServer.removeServer(cfg_mod.APP_SERVER_NAME)
        server = QLocalServer(app)
        server.listen(cfg_mod.APP_SERVER_NAME)

        def handle_connection():
            client = server.nextPendingConnection()
            if client and client.waitForReadyRead(250) and client.readAll().data() == b"QUIT":
                app.quit()
        server.newConnection.connect(handle_connection)
        return server

    # High-DPI attributes must be set before the QApplication is created.
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    if hasattr(Qt, "HighDpiScaleFactorRoundingPolicy"):        # Qt >= 5.14: exact 125 % / 150 %
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    server = replace_existing_instance(app)
    controller = AppController(app)
    return app, server, controller


def main() -> int:
    parser = argparse.ArgumentParser(description="Contextual UI Screen Translator")
    parser.add_argument("--check", action="store_true", help="print an environment diagnosis and exit")
    args = parser.parse_args()
    setup_logging()
    if args.check:
        return run_check()
    app, _server, controller = build_app()
    log.info("running; OCR languages: %s; hotkey: %s", controller.ocr.lang, controller.config.get("hotkey"))
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
