"""OCR pipeline: dark-mode detection, preprocessing, pass selection, bounding-box grouping."""
import shutil
import unittest
from unittest import mock

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from core import ocr_engine as oe
from core.metrics import cer
from core.ocr_engine import (OcrEngine, OcrLine, OcrWord, choose_psms, choose_scale,
                             estimate_line_count, group_words_into_lines, is_dark_background,
                             layout_blocks, looks_like_code, mean_luminance, merge_lines_into_blocks,
                             otsu_threshold, pass_score, preprocess, should_merge,
                             split_line_on_gaps, words_from_tesseract_data, words_to_text)


def W(text, left, top, width=None, height=14, conf=90.0):
    return OcrWord(text, left, top, width if width is not None else 8 * len(text), height, conf)


def line_of(text, left, top, width=None, height=14):
    return OcrLine([W(text, left, top, width, height)])


def synthetic(text_lines, bg, fg, size=(300, None), pitch=28):
    h = pitch * len(text_lines) + 16
    img = Image.new("RGB", (size[0], h), bg)
    draw = ImageDraw.Draw(img)
    for i, t in enumerate(text_lines):
        draw.text((12, 8 + i * pitch), t, fill=fg)
    return img


class DarkModeTests(unittest.TestCase):
    def test_luminance_detection(self):
        light = Image.new("RGB", (50, 50), "#f5f5f5")
        dark = Image.new("RGB", (50, 50), "#1e1e1e")
        self.assertGreater(mean_luminance(light), 200)
        self.assertLess(mean_luminance(dark), 40)
        self.assertFalse(is_dark_background(light))
        self.assertTrue(is_dark_background(dark))

    def test_dark_image_is_inverted_so_text_is_dark_on_light(self):
        pre = preprocess(synthetic(["Settings"], "#1e1e1e", "#d4d4d4"))
        self.assertTrue(pre.inverted)
        self.assertGreater(mean_luminance(pre.image), 150)

    def test_light_image_is_left_alone(self):
        pre = preprocess(synthetic(["Settings"], "#ffffff", "#202020"))
        self.assertFalse(pre.inverted)

    def test_auto_invert_can_be_disabled(self):
        pre = preprocess(synthetic(["Settings"], "#1e1e1e", "#d4d4d4"), invert_dark=False)
        self.assertFalse(pre.inverted)
        self.assertLess(mean_luminance(pre.image), 110)

    def test_padding_and_scale_are_applied(self):
        img = synthetic(["Settings"], "#ffffff", "#202020", size=(100, 0))
        pre = preprocess(img, pad=10)
        self.assertEqual(pre.image.width, int(round(img.width * pre.scale)) + 20)

    def test_low_contrast_image_is_binarised(self):
        pre = preprocess(synthetic(["Settings"], "#808080", "#8c8c8c"))
        self.assertTrue(pre.binarized)
        values = set(np.unique(np.asarray(pre.image)))
        self.assertTrue(values <= {0, 255} or len(values) <= 3)

    def test_high_contrast_image_is_not_binarised(self):
        self.assertFalse(preprocess(synthetic(["Settings"], "#ffffff", "#000000")).binarized)

    def test_rgba_input_is_accepted(self):
        pre = preprocess(Image.new("RGBA", (40, 20), (255, 255, 255, 255)))
        self.assertEqual(pre.image.mode, "L")


class ImageMathTests(unittest.TestCase):
    def test_otsu_separates_a_bimodal_histogram(self):
        arr = np.array([20] * 500 + [220] * 500, dtype=np.uint8)
        self.assertTrue(20 <= otsu_threshold(arr) < 220)

    def test_choose_scale(self):
        self.assertEqual(choose_scale(2000, 1000), 1.0)
        self.assertEqual(choose_scale(300, 30), 3.0)
        self.assertEqual(choose_scale(300, 200), 2.0)
        self.assertEqual(choose_scale(1200, 300), 1.0)
        self.assertLessEqual(1500 * choose_scale(1500, 30), 3600)

    def test_line_count_from_projection_profile(self):
        def rows(n):
            arr = np.full((20 * n + 20, 200), 255, dtype=np.uint8)
            for i in range(n):
                arr[10 + i * 20: 22 + i * 20, 10:150] = 0     # a 12 px tall text bar
            return arr
        self.assertEqual(estimate_line_count(rows(1)), 1)
        self.assertEqual(estimate_line_count(rows(3)), 3)

    def test_psm_choice_follows_line_count(self):
        self.assertEqual(choose_psms(1), (7, 6))
        self.assertEqual(choose_psms(4), (6, 11))


class WordFilteringTests(unittest.TestCase):
    DATA = {
        "text": ["", "Hello", "world", "|", "x", "Ctri+J", "_"],
        "conf": ["-1", "96.5", "20", "40", "88", "5", "90"],
        "left": [0, 10, 60, 5, 100, 120, 3],
        "top": [0, 10, 10, 10, 10, 10, 10],
        "width": [200, 40, 40, 4, 8, 50, 6],
        "height": [30, 12, 12, 12, 12, 12, 12],
    }

    def test_structure_rows_and_empty_text_are_dropped(self):
        words = words_from_tesseract_data(self.DATA, min_conf=0)
        self.assertNotIn("", [w.text for w in words])

    def test_symbol_noise_is_dropped_but_confident_symbols_survive(self):
        texts = [w.text for w in words_from_tesseract_data(self.DATA, min_conf=0)]
        self.assertNotIn("|", texts)     # conf 40 < 70
        self.assertIn("_", texts)        # conf 90

    def test_confidence_threshold(self):
        texts = [w.text for w in words_from_tesseract_data(self.DATA, min_conf=35)]
        self.assertEqual(texts, ["Hello", "x", "_"])


class PassSelectionTests(unittest.TestCase):
    def test_score_rewards_coverage_not_just_mean_confidence(self):
        one_word = [W("Settings", 0, 0, conf=99)]
        six_words = [W("Settings", 0, 0, conf=90)] + [W("History", 0, 30, conf=88), W("Help", 0, 60, conf=88)]
        self.assertGreater(pass_score(six_words), pass_score(one_word))

    def test_noise_floor_contributes_nothing(self):
        self.assertEqual(pass_score([W("abcdef", 0, 0, conf=35)]), 0.0)

    def test_first_pass_wins_ties_and_small_margins(self):
        a = [W("Settings", 0, 0, conf=90)]
        b = [W("Settings", 0, 0, conf=90.5)]
        (psm, _), _ = OcrEngine._select([(6, a), (11, b)], 35)
        self.assertEqual(psm, 6)

    def test_clearly_better_later_pass_wins(self):
        a = [W("Set", 0, 0, conf=60)]
        b = [W("Settings", 0, 0, conf=95)]
        (psm, _), _ = OcrEngine._select([(6, a), (11, b)], 35)
        self.assertEqual(psm, 11)


class LineGroupingTests(unittest.TestCase):
    def test_words_cluster_into_lines_in_reading_order(self):
        words = [W("world", 60, 12), W("Hello", 10, 10), W("Second", 10, 40), W("line", 70, 41)]
        lines = group_words_into_lines(words)
        self.assertEqual([l.text for l in lines], ["Hello world", "Second line"])

    def test_cjk_words_are_joined_without_spaces(self):
        self.assertEqual(words_to_text([W("设", 10, 10, 14), W("置", 26, 10, 14)]), "设置")

    def test_touching_tokens_do_not_gain_a_space(self):
        text = words_to_text([W("glossary.", 10, 10, 72), W("lookup(text)", 83, 10, 96)])
        self.assertEqual(text, "glossary.lookup(text)")

    def test_real_gaps_keep_their_space(self):
        self.assertEqual(words_to_text([W("New", 10, 10, 24), W("tab", 44, 10, 24)]), "New tab")

    def test_shortcut_spacing_is_repaired(self):
        text = words_to_text([W("Delete", 10, 10, 48), W("Ctri+", 70, 10, 40), W("Shift+", 115, 10, 48), W("Del", 168, 10, 24)])
        self.assertEqual(text, "Delete Ctrl+Shift+Del")


class GapSplitTests(unittest.TestCase):
    def test_shortcut_stays_attached_even_when_far_away(self):
        line = OcrLine([W("New", 10, 10, 24), W("tab", 40, 10, 24), W("Ctrl+T", 250, 10, 48)])
        parts = split_line_on_gaps(line, ref_height=14)
        self.assertEqual([p.text for p in parts], ["New tab Ctrl+T"])

    def test_label_and_value_columns_are_separated(self):
        line = OcrLine([W("Volume", 10, 10, 48), W("80%", 260, 10, 24)])
        self.assertEqual([p.text for p in split_line_on_gaps(line, 14)], ["Volume", "80%"])

    def test_normal_word_spacing_does_not_split(self):
        line = OcrLine([W("Delete", 10, 10, 48), W("browsing", 66, 10, 64), W("data", 138, 10, 32)])
        self.assertEqual(len(split_line_on_gaps(line, 14)), 1)

    def test_mangled_shortcut_is_still_attached(self):
        line = OcrLine([W("Find", 10, 10, 32), W("Cti+F", 250, 10, 40)])
        self.assertEqual(len(split_line_on_gaps(line, 14)), 1)


class ParagraphMergingTests(unittest.TestCase):
    PARAGRAPH = [
        line_of("The quick brown fox jumps over the lazy dog while the", 10, 10, 330),
        line_of("sun sets slowly behind the distant hills and the", 10, 30, 310),
        line_of("river keeps flowing toward the sea.", 10, 50, 230),
    ]

    def test_wrapped_lines_become_one_paragraph(self):
        blocks = merge_lines_into_blocks(self.PARAGRAPH)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0].kind, "paragraph")
        self.assertTrue(blocks[0].text.startswith("The quick brown fox") and blocks[0].text.endswith("the sea."))

    def test_grouping_can_be_disabled(self):
        self.assertEqual(len(merge_lines_into_blocks(self.PARAGRAPH, merge_paragraphs=False)), 3)

    def test_menu_items_stay_separate(self):
        menu = [line_of("New tab", 10, 10, 56), line_of("History", 10, 42, 56),
                line_of("Downloads", 10, 74, 72), line_of("Settings", 10, 106, 64)]
        self.assertEqual(len(merge_lines_into_blocks(menu)), 4)

    def test_tightly_packed_menu_with_shortcuts_stays_separate(self):
        menu = [OcrLine([W("New tab", 10, 10, 56), W("Ctrl+T", 250, 10, 48)]),
                OcrLine([W("New window", 10, 28, 80), W("Ctrl+N", 250, 28, 48)]),
                OcrLine([W("Print…", 10, 46, 48), W("Ctrl+P", 250, 46, 48)])]
        blocks = layout_blocks([w for l in menu for w in l.words])
        self.assertEqual([b.text for b in blocks], ["New tab Ctrl+T", "New window Ctrl+N", "Print… Ctrl+P"])

    def test_title_above_body_is_not_merged(self):
        title = line_of("Privacy settings", 10, 10, 130, height=22)
        body = line_of("Choose what data is stored on this device", 10, 36, 330)
        self.assertFalse(should_merge(title, body, 14, 330))

    def test_code_lines_are_not_merged(self):
        a = line_of("def translate(text, lang):", 10, 10, 210)
        b = line_of("return glossary.lookup(text)", 10, 28, 215)
        self.assertTrue(looks_like_code(a.text) and looks_like_code(b.text))
        self.assertFalse(should_merge(a, b, 14, 215))

    def test_side_by_side_columns_are_not_merged(self):
        left = line_of("Name", 10, 10, 160)
        right = line_of("Value", 200, 12, 160)
        self.assertFalse(should_merge(left, right, 14, 360))

    def test_centered_lines_can_merge(self):
        a = line_of("A very long centered headline that wraps", 40, 10, 300)
        b = line_of("onto a second centered line", 80, 30, 220)
        self.assertTrue(should_merge(a, b, 14, 300))

    def test_cjk_paragraph_lines_join_without_spaces(self):
        a = OcrLine([W("这是一个很长的句子用来测试", 10, 10, 330)])
        b = OcrLine([W("段落合并功能", 10, 30, 160)])
        self.assertEqual(merge_lines_into_blocks([a, b])[0].text, "这是一个很长的句子用来测试段落合并功能")


class LineGatingTests(unittest.TestCase):
    def test_weak_token_survives_inside_a_confident_line(self):
        words = [W("Downloads", 10, 10, 72, conf=95), W("Ctri+J", 250, 10, 48, conf=10)]
        self.assertEqual([b.text for b in layout_blocks(words)], ["Downloads Ctrl+J"])

    def test_isolated_short_noise_line_is_dropped(self):
        words = [W("Settings", 10, 10, 64, conf=95), W("oO", 10, 60, 16, conf=12)]
        self.assertEqual([b.text for b in layout_blocks(words)], ["Settings"])

    def test_isolated_weak_but_long_line_is_kept(self):
        words = [W("Settings", 10, 10, 64, conf=95), W("main.py", 10, 60, 56, conf=0)]
        self.assertEqual([b.text for b in layout_blocks(words)], ["Settings", "main.py"])


class EngineWithMockedTesseractTests(unittest.TestCase):
    def _data(self, words):
        return {"text": [w[0] for w in words], "conf": [w[1] for w in words],
                "left": [w[2] for w in words], "top": [w[3] for w in words],
                "width": [w[4] for w in words], "height": [14] * len(words)}

    def _engine_with(self, side_effect, **kw):
        fake = mock.MagicMock()
        fake.image_to_data.side_effect = side_effect
        fake.Output.DICT = "dict"
        patcher = mock.patch.object(oe, "pytesseract", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        patch2 = mock.patch.object(oe, "Output", fake.Output)
        patch2.start()
        self.addCleanup(patch2.stop)
        return OcrEngine(lang="eng", **kw), fake

    def test_confident_first_pass_takes_the_fast_path(self):
        data = self._data([("Settings", 96, 30, 30, 64)])
        engine, fake = self._engine_with(lambda *a, **k: data)
        result = engine.recognize(synthetic(["Settings"], "#fff", "#000"))
        self.assertEqual(fake.image_to_data.call_count, 1)
        self.assertEqual(result.texts, ["Settings"])

    def test_weak_first_pass_triggers_remaining_passes_and_best_wins(self):
        weak = self._data([("Set", 50, 30, 30, 24)])
        strong = self._data([("Settings", 90, 30, 30, 64), ("History", 90, 30, 70, 56)])

        def fake_pass(image, lang=None, config="", output_type=None):
            return weak if "--psm 6" in config or "--psm 7" in config else strong
        engine, fake = self._engine_with(fake_pass, parallel=False)
        result = engine.recognize(synthetic(["Settings", "History"], "#fff", "#000"))
        self.assertGreaterEqual(fake.image_to_data.call_count, 2)
        self.assertEqual(result.psm, 11)
        self.assertEqual(result.texts, ["Settings", "History"])
        self.assertTrue(result.passes)

    def test_empty_result_retries_at_other_scale_and_returns_empty(self):
        empty = self._data([])
        engine, fake = self._engine_with(lambda *a, **k: empty, parallel=False)
        result = engine.recognize(synthetic(["x"], "#fff", "#000"))
        self.assertEqual(result.texts, [])
        self.assertGreater(fake.image_to_data.call_count, 2)

    def test_coordinates_are_mapped_back_to_the_original_image(self):
        img = synthetic(["Settings"], "#fff", "#000", size=(200, 0))
        pre = preprocess(img)
        x = pre.pad + int(10 * pre.scale)
        data = self._data([("Settings", 95, x, pre.pad + int(8 * pre.scale), int(64 * pre.scale))])
        engine, _ = self._engine_with(lambda *a, **k: data)
        block = engine.recognize(img).blocks[0]
        self.assertAlmostEqual(block.bbox[0], 10, delta=1)

    def test_available_languages_helper(self):
        self.assertEqual(oe.available_languages(["eng", "vie", "jpn"], installed=["eng", "jpn", "osd"]), "eng+jpn")
        self.assertEqual(oe.available_languages(["vie"], installed=["osd"]), "eng")


@unittest.skipUnless(shutil.which("tesseract"), "Tesseract is not installed")
class RealTesseractIntegrationTests(unittest.TestCase):
    """End-to-end: synthetic light and dark UIs must be read nearly perfectly."""

    @classmethod
    def setUpClass(cls):
        cls.font = None
        for name in ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
            try:
                cls.font = ImageFont.truetype(name, 14)
                break
            except OSError:
                continue
        if cls.font is None:
            raise unittest.SkipTest("no TrueType font available")

    def _render(self, lines, bg, fg):
        img = Image.new("RGB", (330, 30 * len(lines) + 14), bg)
        d = ImageDraw.Draw(img)
        for i, t in enumerate(lines):
            d.text((12, 8 + i * 30), t, font=self.font, fill=fg)
        return img

    def test_light_and_dark_ui_text_are_recognised(self):
        lines = ["New window", "History", "Downloads", "Settings"]
        engine = OcrEngine(lang="eng")
        for bg, fg in (("#ffffff", "#202124"), ("#1e1e1e", "#d4d4d4")):
            result = engine.recognize(self._render(lines, bg, fg))
            self.assertLess(cer(" ".join(lines), " ".join(result.texts)), 0.1, (bg, result.texts))
            self.assertEqual(result.inverted, bg == "#1e1e1e")

    def test_wrapped_paragraph_is_grouped_into_one_block(self):
        lines = ["The quick brown fox jumps over the lazy dog while the",
                 "sun sets slowly behind the distant hills and the",
                 "river keeps flowing toward the sea."]
        img = Image.new("RGB", (470, 20 * 3 + 20), "white")
        d = ImageDraw.Draw(img)
        for i, t in enumerate(lines):
            d.text((10, 10 + i * 20), t, font=self.font, fill="black")
        result = OcrEngine(lang="eng").recognize(img)
        self.assertEqual(len(result.blocks), 1)
        self.assertLess(cer(" ".join(lines), result.texts[0]), 0.1)


if __name__ == "__main__":
    unittest.main()
