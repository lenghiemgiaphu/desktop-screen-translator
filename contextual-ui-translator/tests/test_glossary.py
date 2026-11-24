"""Glossary: shortcut stripping, CJK normalisation, context-weighted scoring, persistence."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from core.glossary import (CONTEXT_BOOST, CONTEXT_PENALTY, Glossary, context_adjustment,
                           indel_ratio, parse_contexts)
from core.text_utils import normalize_for_match


def make(data):
    return Glossary(data=data)


class SimilarityTests(unittest.TestCase):
    def test_indel_ratio_matches_known_fuzz_values(self):
        self.assertEqual(indel_ratio("settings", "setting"), 93)
        self.assertEqual(indel_ratio("new tab", "new tab"), 100)
        self.assertEqual(indel_ratio("abc", "xyz"), 0)
        self.assertEqual(indel_ratio("", "abc"), 0)


class NormalisationTests(unittest.TestCase):
    def test_trailing_shortcut_is_stripped(self):
        g = make({"A": {"en": "New tab", "vi": "Thẻ mới"}})
        self.assertEqual(g.lookup("New tab Ctrl+T", "vi").translated, "Thẻ mới")
        self.assertEqual(g.lookup("New tab   ctrl + shift + t", "vi").translated, "Thẻ mới")

    def test_ellipsis_and_case_are_ignored(self):
        g = make({"A": {"en": "Print", "vi": "In"}})
        for text in ("Print...", "PRINT…", "print."):
            self.assertEqual(g.lookup(text, "vi").translated, "In", text)

    def test_prose_ending_in_shift_is_not_mangled(self):
        self.assertEqual(normalize_for_match("Hold shift"), "hold shift")

    def test_cjk_spaces_and_fullwidth_forms_are_normalised(self):
        g = make({"A": {"en": "Settings", "zh": "设置", "vi": "Cài đặt"}})
        self.assertEqual(g.lookup("设 置", "vi").translated, "Cài đặt")
        self.assertEqual(normalize_for_match("Ｓｅｔｔｉｎｇｓ"), "settings")

    def test_target_language_is_required(self):
        g = make({"A": {"en": "Settings", "vi": "Cài đặt"}})
        self.assertIsNone(g.lookup("Settings", "ja"))

    def test_below_threshold_is_not_a_match(self):
        g = make({"A": {"en": "Settings", "vi": "Cài đặt"}})
        self.assertIsNone(g.lookup("Sett", "vi"))
        self.assertIsNone(g.lookup("Downloads", "vi"))


class ContextWeightingTests(unittest.TestCase):
    DATA = {
        "IDE": {"en": "Branch", "vi": "Nhánh mã nguồn", "context": "ide"},
        "OFF": {"en": "Branch", "vi": "Chi nhánh", "context": "office"},
        "GLO": {"en": "File", "vi": "Tệp tin", "context": "global"},
        "OLD": {"en": "Save", "vi": "Lưu"},               # legacy entry, no context
    }

    def setUp(self):
        self.g = make(self.DATA)

    def test_active_domain_selects_the_matching_entry(self):
        self.assertEqual(self.g.lookup("Branch", "vi", "ide").translated, "Nhánh mã nguồn")
        self.assertEqual(self.g.lookup("Branch", "vi", "office").translated, "Chi nhánh")

    def test_boost_is_applied_and_capped_at_100(self):
        m = self.g.lookup("Branch", "vi", "ide")
        self.assertEqual(m.base_score, 100)
        self.assertEqual(m.score, 100)      # 100 + 10 capped
        self.assertTrue(m.context_matched)
        m2 = self.g.lookup("Branc", "vi", "ide")      # 91 base -> boosted to 100
        self.assertEqual((m2.base_score, m2.score), (91, 100))

    def test_conflicting_domain_is_penalised(self):
        m = self.g.lookup("Branch", "vi", "browser")
        self.assertEqual(m.score, 100 - CONTEXT_PENALTY)
        self.assertFalse(m.context_matched)

    def test_penalty_can_reject_a_borderline_match(self):
        g = make({"A": {"en": "Branch", "vi": "Nhánh", "context": "ide"}})
        self.assertIsNotNone(g.lookup("Branc", "vi", "general"))        # 91, no adjustment
        self.assertIsNone(g.lookup("Bran", "vi", "office"))              # 89 - 10 < 80
        self.assertIsNotNone(g.lookup("Bran", "vi", "ide"))              # 89 + 10

    def test_boost_never_creates_a_match_on_its_own(self):
        g = make({"A": {"en": "Settings", "vi": "Cài đặt", "context": "ide"}})
        self.assertLess(indel_ratio("sett", "settings"), 80)
        self.assertIsNone(g.lookup("Sett", "vi", "ide"))

    def test_legacy_entries_without_context_are_neutral(self):
        for domain in ("ide", "office", "general"):
            m = self.g.lookup("Save", "vi", domain)
            self.assertEqual((m.translated, m.score), ("Lưu", 100))
            self.assertEqual(m.context_label, "")

    def test_general_domain_applies_no_adjustment(self):
        self.assertEqual(context_adjustment(frozenset({"ide"}), "general"), 0)
        self.assertEqual(context_adjustment(frozenset({"ide"}), "ide"), CONTEXT_BOOST)
        self.assertEqual(context_adjustment(frozenset(), "ide"), 0)

    def test_context_may_be_a_list(self):
        g = make({"A": {"en": "Extensions", "vi": "Tiện ích", "context": ["browser", "ide"]}})
        self.assertEqual(g.lookup("Extensions", "vi", "ide").score, 100)
        self.assertEqual(g.lookup("Extensions", "vi", "office").score, 90)
        self.assertEqual(parse_contexts(["Browser", " IDE "]), frozenset({"browser", "ide"}))
        self.assertEqual(parse_contexts(["browser", "global"]), frozenset())

    def test_tuple_format_for_the_ui(self):
        text, badge, pinyin, score = self.g.lookup("Branch", "vi", "ide").as_tuple()
        self.assertEqual((text, score), ("Nhánh mã nguồn", 100))
        self.assertEqual(badge, "Local DB (100% \u2022 IDE)")
        self.assertEqual(pinyin, "")


class PinyinTests(unittest.TestCase):
    DATA = {"A": {"en": "Settings", "zh": "设置", "pinyin": "shè zhì", "vi": "Cài đặt"}}

    def test_pinyin_for_chinese_input(self):
        self.assertEqual(make(self.DATA).lookup("设置", "vi").pinyin, "shè zhì")

    def test_pinyin_for_chinese_target(self):
        self.assertEqual(make(self.DATA).lookup("Settings", "zh").pinyin, "shè zhì")

    def test_no_pinyin_otherwise(self):
        self.assertEqual(make(self.DATA).lookup("Settings", "vi").pinyin, "")


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "database.json"
        self.path.write_text(json.dumps(
            {"UI_001": {"en": "Settings", "vi": "Cài đặt", "custom": "keep me"}}), encoding="utf-8")
        self.g = Glossary(self.path)

    def tearDown(self):
        self.dir.cleanup()

    def test_add_entry_persists_and_becomes_searchable(self):
        entry_id = self.g.add_entry("Dark mode", "en", "vi", "Chế độ tối", context="browser")
        self.assertEqual(entry_id, "UI_002")
        reloaded = Glossary(self.path)
        m = reloaded.lookup("dark mode", "vi", "browser")
        self.assertEqual((m.translated, m.score), ("Chế độ tối", 100))
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["UI_002"]["context"], "browser")
        self.assertEqual(saved["UI_001"]["custom"], "keep me")      # unknown fields survive

    def test_existing_entry_is_extended_not_duplicated(self):
        entry_id = self.g.add_entry("Settings", "en", "ja", "設定")
        self.assertEqual(entry_id, "UI_001")
        self.assertEqual(len(self.g), 1)
        self.assertEqual(self.g.lookup("Settings", "ja").translated, "設定")

    def test_existing_translation_is_never_overwritten(self):
        self.g.add_entry("Settings", "en", "vi", "Thiết lập")
        self.assertEqual(self.g.lookup("Settings", "vi").translated, "Cài đặt")

    def test_invalid_input_is_rejected(self):
        with self.assertRaises(ValueError):
            self.g.add_entry("", "en", "vi", "x")
        with self.assertRaises(ValueError):
            self.g.add_entry("a", "en", "en", "b")

    def test_external_edits_are_picked_up(self):
        self.assertIsNone(self.g.lookup("Downloads", "vi"))
        time.sleep(0.02)
        self.path.write_text(json.dumps({"X": {"en": "Downloads", "vi": "Tải xuống"}}), encoding="utf-8")
        os.utime(self.path, (time.time() + 5, time.time() + 5))
        self.assertTrue(self.g.refresh_if_changed())
        self.assertEqual(self.g.lookup("Downloads", "vi").translated, "Tải xuống")

    def test_corrupt_file_yields_empty_glossary(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(len(Glossary(self.path)), 0)


class ShippedDatabaseTests(unittest.TestCase):
    def test_bundled_database_is_valid_and_backward_compatible(self):
        path = Path(__file__).resolve().parent.parent / "database.json"
        g = Glossary(path)
        self.assertGreaterEqual(len(g), 14)
        # original prototype entries still resolve
        self.assertEqual(g.lookup("New tab Ctrl+T", "vi", "browser").translated, "Thẻ mới")
        self.assertEqual(g.lookup("设置", "vi").pinyin, "shè zhì")
        self.assertEqual(g.lookup("Branch", "vi", "ide").translated, "Nhánh mã nguồn")
        self.assertEqual(g.lookup("Branch", "vi", "office").translated, "Chi nhánh")


if __name__ == "__main__":
    unittest.main()
