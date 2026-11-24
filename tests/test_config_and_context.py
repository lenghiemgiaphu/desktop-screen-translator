"""Config, languages, history, application-context mapping, text utilities, metrics."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import config
from core.context_detector import AppContext, classify, get_foreground_context, process_basename
from core.history import HistoryStore
from core.languages import LANG_MAP, find_language, lang_codes
from core.metrics import cer, edit_distance, wer
from core.text_utils import (canonicalize_shortcuts, clean_cjk_spaces, decompose_ui_text,
                             guess_lang_code, has_trailing_shortcut, is_shortcut, join_text)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_run_creates_the_file_with_defaults(self):
        cfg = config.Config(self.path)
        self.assertTrue(self.path.exists())
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        for key in ("last_target_lang", "hotkey", "tesseract_path", "auto_group_paragraphs", "dark_mode_auto_invert"):
            self.assertIn(key, saved)
        self.assertEqual(cfg.get("hotkey"), "f8")

    def test_settings_persist_across_restarts(self):
        config.Config(self.path).update(last_target_lang="Japanese", hotkey="Ctrl + Shift+T")
        again = config.Config(self.path)
        self.assertEqual(again.get("last_target_lang"), "Japanese")
        self.assertEqual(again.get("hotkey"), "ctrl+shift+t")

    def test_invalid_values_fall_back_to_defaults(self):
        self.path.write_text(json.dumps({"last_target_lang": "Klingon", "hotkey": "ctrl+++",
                                         "auto_group_paragraphs": "yes", "ocr_languages": ["eng", "zzz", 3]}),
                             encoding="utf-8")
        cfg = config.Config(self.path)
        self.assertEqual(cfg.get("last_target_lang"), "Vietnamese")
        self.assertEqual(cfg.get("hotkey"), "f8")
        self.assertTrue(cfg.get("auto_group_paragraphs"))
        self.assertEqual(cfg.get("ocr_languages"), ["eng"])

    def test_corrupt_file_does_not_crash(self):
        self.path.write_text("{oops", encoding="utf-8")
        self.assertEqual(config.Config(self.path).get("hotkey"), "f8")

    def test_hotkey_normalisation(self):
        self.assertEqual(config.normalize_hotkey(" F8 "), "f8")
        self.assertEqual(config.normalize_hotkey("Ctrl + Alt + X"), "ctrl+alt+x")
        self.assertIsNone(config.normalize_hotkey(""))
        self.assertIsNone(config.normalize_hotkey("ctrl+"))
        self.assertIsNone(config.normalize_hotkey(None))


class TesseractDiscoveryTests(unittest.TestCase):
    def test_configured_path_wins(self):
        with mock.patch("os.path.isfile", side_effect=lambda p: p == "C:/custom/tesseract.exe"):
            self.assertEqual(config.find_tesseract("C:/custom/tesseract.exe"), "C:/custom/tesseract.exe")

    def test_falls_back_to_path_lookup(self):
        with mock.patch("os.path.isfile", return_value=False), \
                mock.patch("shutil.which", return_value="/somewhere/tesseract"):
            self.assertEqual(config.find_tesseract("/missing"), "/somewhere/tesseract")

    def test_falls_back_to_standard_install_folders(self):
        target = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        with mock.patch("shutil.which", return_value=None), \
                mock.patch("os.path.isfile", side_effect=lambda p: p == target):
            self.assertEqual(config.find_tesseract(), target)

    def test_returns_none_instead_of_crashing(self):
        with mock.patch("shutil.which", return_value=None), mock.patch("os.path.isfile", return_value=False):
            self.assertIsNone(config.find_tesseract())


class LanguageTests(unittest.TestCase):
    def test_find_language(self):
        self.assertEqual(find_language("japanese"), "Japanese")
        self.assertEqual(find_language("Kor"), "Korean")
        self.assertIsNone(find_language("Ch"))          # ambiguous: two Chinese variants
        self.assertIsNone(find_language("Ja p x"))
        self.assertIsNone(find_language(""))

    def test_partial_typing_never_resolves_to_a_wrong_language(self):
        # Every keystroke of "Vietnamese" must either be unresolved or already Vietnamese.
        for i in range(1, len("Vietnamese") + 1):
            self.assertIn(find_language("Vietnamese"[:i]), (None, "Vietnamese"))

    def test_codes(self):
        self.assertEqual(lang_codes("Chinese (Traditional)"), {"db": "zh", "gt": "zh-TW"})
        self.assertEqual(lang_codes("nonsense"), LANG_MAP["English"])


class HistoryTests(unittest.TestCase):
    def test_add_load_clear_and_cap(self):
        with tempfile.TemporaryDirectory() as d:
            store = HistoryStore(Path(d) / "history.json", max_entries=3)
            self.assertEqual(store.load(), [])
            for i in range(5):
                store.add("Vietnamese", [{"raw": f"r{i}", "translated": "t", "pinyin": "", "source": "Google"}], "Chrome")
            data = store.load()
            self.assertEqual(len(data), 3)
            self.assertEqual(data[0]["items"][0]["raw"], "r4")
            self.assertEqual(data[0]["context"], "Chrome")
            store.clear()
            self.assertEqual(store.load(), [])

    def test_empty_items_are_not_recorded(self):
        with tempfile.TemporaryDirectory() as d:
            store = HistoryStore(Path(d) / "h.json")
            store.add("Vietnamese", [])
            self.assertEqual(store.load(), [])


class ContextDetectorTests(unittest.TestCase):
    def test_known_processes(self):
        cases = {"chrome.exe": "browser", "Code.exe": "ide", "figma.exe": "design",
                 "WINWORD.EXE": "office", "steam.exe": "gaming", "unknown.exe": "general"}
        for exe, domain in cases.items():
            self.assertEqual(classify(exe).domain, domain, exe)

    def test_full_windows_path_is_reduced_to_the_executable(self):
        self.assertEqual(process_basename(r"C:\Program Files\Google\Chrome\Application\Chrome.exe"), "chrome.exe")
        self.assertEqual(classify(r"C:\Users\me\AppData\Local\Programs\Microsoft VS Code\Code.exe").domain, "ide")

    def test_labels(self):
        self.assertEqual(classify("chrome.exe").label, "Chrome (Browser)")
        self.assertEqual(classify("Code.exe").label, "VS Code (IDE)")
        self.assertEqual(classify("explorer.exe").label, "General")
        self.assertEqual(AppContext().label, "General")

    def test_title_hint_for_host_processes(self):
        self.assertEqual(classify("ApplicationFrameHost.exe", "Figma - Home").domain, "design")
        self.assertEqual(classify("explorer.exe", "Figma").domain, "general")   # shell ignored

    def test_non_windows_falls_back_to_general(self):
        with mock.patch("sys.platform", "linux"):
            self.assertEqual(get_foreground_context().domain, "general")

    def test_detector_errors_never_propagate(self):
        with mock.patch("sys.platform", "win32"), \
                mock.patch("core.context_detector._win32_foreground", side_effect=OSError("boom")):
            self.assertEqual(get_foreground_context().domain, "general")


class TextUtilsTests(unittest.TestCase):
    def test_shortcut_detection(self):
        for text in ("New tab Ctrl+T", "Save F8", "Close Alt+F4", "Delete Ctrl+Shift+Del"):
            self.assertTrue(has_trailing_shortcut(text), text)
        for text in ("Hold shift", "Settings", "Press F to pay respects", "Alt"):
            self.assertFalse(has_trailing_shortcut(text), text)

    def test_is_shortcut_accepts_ocr_mangled_ctrl(self):
        for text in ("Ctrl+T", "Cti+F", "Ctril+F", "Ctr+F", "ctrl + shift + n", "F12"):
            self.assertTrue(is_shortcut(text), text)
        for text in ("Settings", "Ctrl", "New+tab"):
            self.assertFalse(is_shortcut(text), text)

    def test_canonicalisation_leaves_prose_alone(self):
        self.assertEqual(canonicalize_shortcuts("Delete Ctri+ Shift+ Del"), "Delete Ctrl+Shift+Del")
        self.assertEqual(canonicalize_shortcuts("hold shift + click"), "hold shift + click")
        self.assertEqual(canonicalize_shortcuts("Press Alt + F4"), "Press Alt+F4")

    def test_decompose(self):
        t = decompose_ui_text("Print...   Ctrl+P")
        self.assertEqual((t.core, t.ellipsis, t.shortcut), ("Print", "\u2026", "Ctrl+P"))
        self.assertEqual(decompose_ui_text("Settings").shortcut, "")

    def test_cjk_helpers(self):
        self.assertEqual(clean_cjk_spaces("设 置 abc"), "设置 abc")
        self.assertEqual(join_text("设置", "页面"), "设置页面")
        self.assertEqual(join_text("New", "tab"), "New tab")

    def test_language_guess(self):
        self.assertEqual(guess_lang_code("設定"), "zh")
        self.assertEqual(guess_lang_code("ひらがな"), "ja")
        self.assertEqual(guess_lang_code("설정"), "ko")
        self.assertEqual(guess_lang_code("Cài đặt hệ thống"), "vi")
        self.assertEqual(guess_lang_code("Настройки"), "ru")
        self.assertEqual(guess_lang_code("Settings"), "en")


class MetricsTests(unittest.TestCase):
    def test_edit_distance(self):
        self.assertEqual(edit_distance("kitten", "sitting"), 3)
        self.assertEqual(edit_distance("", "abc"), 3)
        self.assertEqual(edit_distance(["a", "b"], ["a", "c"]), 1)

    def test_cer_and_wer(self):
        self.assertAlmostEqual(cer("hello world", "hello world"), 0.0)
        self.assertAlmostEqual(cer("abcd", "abxd"), 0.25)
        self.assertAlmostEqual(wer("a b c d", "a x c d"), 0.25)
        self.assertEqual(cer("", ""), 0.0)
        self.assertEqual(cer("", "x"), 1.0)

    def test_whitespace_is_normalised(self):
        self.assertEqual(cer("New  tab\nCtrl+T", "New tab Ctrl+T"), 0.0)


if __name__ == "__main__":
    unittest.main()
