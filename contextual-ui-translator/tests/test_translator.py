"""Hybrid translation routing, caching, batching and offline handling (mocked backend)."""
import threading
import time
import unittest

from core.context_detector import classify
from core.glossary import Glossary
from core.translator import (HybridTranslator, LRUCache, Source, TranslationResult,
                             run_with_timeout)

GLOSSARY = {
    "A": {"en": "Settings", "vi": "Cài đặt", "ja": "設定"},
    "B": {"en": "Print", "vi": "In"},
    "C": {"en": "Branch", "vi": "Nhánh mã nguồn", "context": "ide"},
    "D": {"en": "Settings", "zh": "设置", "vi": "Cài đặt", "pinyin": "shè zhì"},
}


class FakeBackend:
    """Echoes ``VI:<text>`` per line and records every call."""

    def __init__(self, fail=None, mangle_batches=False):
        self.calls = []
        self.fail = fail
        self.mangle_batches = mangle_batches

    def __call__(self, text, source, target):
        self.calls.append((text, source, target))
        if self.fail:
            raise self.fail
        lines = text.split("\n")
        if self.mangle_batches and len(lines) > 1:
            return " ".join("VI:" + l for l in lines)           # newlines lost
        return "\n".join("VI:" + l for l in lines)


def make(backend=None, **kw):
    backend = backend or FakeBackend()
    return HybridTranslator(Glossary(data=GLOSSARY), backend=backend, **kw), backend


class RoutingTests(unittest.TestCase):
    def test_glossary_hit_never_touches_the_network(self):
        tr, be = make()
        r = tr.translate_one("Settings", "Vietnamese")
        self.assertEqual((r.source, r.translated, r.score), (Source.GLOSSARY, "Cài đặt", 100))
        self.assertEqual(be.calls, [])

    def test_unknown_text_goes_to_google(self):
        tr, be = make()
        r = tr.translate_one("hello world", "Vietnamese")
        self.assertEqual((r.source, r.translated, r.badge), (Source.GOOGLE, "VI:hello world", "Google"))
        self.assertEqual(be.calls, [("hello world", "auto", "vi")])

    def test_second_request_is_served_from_the_cache(self):
        tr, be = make()
        tr.translate_one("hello world", "Vietnamese")
        r = tr.translate_one("hello world", "Vietnamese")
        self.assertEqual((r.source, r.badge, r.translated), (Source.CACHE, "Cached", "VI:hello world"))
        self.assertEqual(len(be.calls), 1)

    def test_cache_is_keyed_by_target_language(self):
        tr, be = make()
        tr.translate_one("hello world", "Vietnamese")
        tr.translate_one("hello world", "French")
        self.assertEqual([c[2] for c in be.calls], ["vi", "fr"])

    def test_chinese_target_uses_the_google_region_code(self):
        tr, be = make()
        tr.translate_one("hello world", "Chinese (Traditional)")
        self.assertEqual(be.calls[0][2], "zh-TW")

    def test_han_text_pins_the_source_language(self):
        tr, be = make()
        tr.translate_one("你好世界", "Vietnamese")
        self.assertEqual(be.calls[0][1], "zh-CN")
        tr.translate_one("你好世界吗", "Chinese (Simplified)")
        self.assertEqual(be.calls[1][1], "auto")

    def test_empty_text_is_skipped(self):
        tr, be = make()
        r = tr.translate_texts(["", "   "], "Vietnamese")
        self.assertEqual([x.source for x in r], [Source.EMPTY, Source.EMPTY])
        self.assertEqual(be.calls, [])

    def test_order_is_preserved_for_mixed_sources(self):
        tr, _ = make()
        tr.translate_one("cached line", "Vietnamese")
        out = tr.translate_texts(["Settings", "new line", "cached line", "Print"], "Vietnamese")
        self.assertEqual([r.source for r in out],
                         [Source.GLOSSARY, Source.GOOGLE, Source.CACHE, Source.GLOSSARY])
        self.assertEqual([r.source_text for r in out], ["Settings", "new line", "cached line", "Print"])


class ContextTests(unittest.TestCase):
    def test_active_application_changes_the_glossary_choice(self):
        tr, _ = make()
        r = tr.translate_one("Branch", "Vietnamese", classify("Code.exe"))
        self.assertEqual((r.translated, r.entry_context, r.badge), ("Nhánh mã nguồn", "IDE", "Local DB \u2022 100% \u2022 IDE"))
        r2 = tr.translate_one("Branch", "Vietnamese", classify("chrome.exe"))
        self.assertEqual(r2.score, 90)
        self.assertEqual(r2.entry_context, "")

    def test_shortcut_and_ellipsis_are_reattached_to_glossary_hits(self):
        tr, _ = make()
        self.assertEqual(tr.translate_one("Print...  Ctrl+P", "Vietnamese").translated, "In…  (Ctrl+P)")
        self.assertEqual(tr.translate_one("Settings Ctrl+,", "Vietnamese").translated, "Cài đặt  (Ctrl+,)")

    def test_pinyin_is_forwarded(self):
        tr, _ = make()
        self.assertEqual(tr.translate_one("设置", "Vietnamese").pinyin, "shè zhì")


class BatchingTests(unittest.TestCase):
    def test_uncached_lines_are_sent_as_one_request(self):
        tr, be = make()
        out = tr.translate_texts(["alpha one", "beta two", "gamma three"], "Vietnamese")
        self.assertEqual(len(be.calls), 1)
        self.assertEqual(be.calls[0][0], "alpha one\nbeta two\ngamma three")
        self.assertEqual([r.translated for r in out], ["VI:alpha one", "VI:beta two", "VI:gamma three"])

    def test_split_mismatch_falls_back_to_per_line(self):
        tr, be = make(FakeBackend(mangle_batches=True))
        out = tr.translate_texts(["alpha one", "beta two"], "Vietnamese")
        self.assertEqual(len(be.calls), 3)        # 1 batch + 2 single
        self.assertEqual([r.translated for r in out], ["VI:alpha one", "VI:beta two"])

    def test_large_inputs_are_chunked(self):
        tr, be = make(batch_char_limit=30)
        tr.translate_texts(["aaaaaaaaaa 1", "bbbbbbbbbb 2", "cccccccccc 3", "dddddddddd 4"], "Vietnamese")
        self.assertEqual(len(be.calls), 2)

    def test_cjk_and_latin_lines_are_batched_separately(self):
        tr, be = make()
        tr.translate_texts(["hello there", "你好世界", "good day"], "Vietnamese")
        self.assertEqual(sorted(c[1] for c in be.calls), ["auto", "zh-CN"])


class OfflineHandlingTests(unittest.TestCase):
    def test_failure_is_tagged_not_disguised_as_a_translation(self):
        tr, _ = make(FakeBackend(fail=ConnectionError("no network")))
        r = tr.translate_one("hello world", "Vietnamese")
        self.assertEqual(r.source, Source.ERROR)
        self.assertEqual(r.badge, "Offline / Error")
        self.assertEqual(r.translated, "")
        self.assertFalse(r.ok)
        self.assertIn("no network", r.error)

    def test_errors_are_not_cached(self):
        be = FakeBackend(fail=ConnectionError("down"))
        tr, _ = make(be)
        tr.translate_one("hello world", "Vietnamese")
        be.fail = None
        self.assertEqual(tr.translate_one("hello world", "Vietnamese").source, Source.GOOGLE)

    def test_circuit_breaker_limits_retries_when_the_network_is_down(self):
        be = FakeBackend(fail=ConnectionError("down"))
        tr, _ = make(be)
        out = tr.translate_texts(["one a", "two b", "three c", "four d", "five e"], "Vietnamese")
        self.assertTrue(all(r.source is Source.ERROR for r in out))
        self.assertLessEqual(len(be.calls), 2)     # 1 batch + 1 probe, not 6

    def test_glossary_hits_still_work_offline(self):
        tr, _ = make(FakeBackend(fail=ConnectionError("down")))
        out = tr.translate_texts(["Settings", "unknown phrase"], "Vietnamese")
        self.assertEqual([r.source for r in out], [Source.GLOSSARY, Source.ERROR])

    def test_hanging_backend_times_out(self):
        gate = threading.Event()

        def hang(text, s, t):
            gate.wait(5)
            return text
        tr = HybridTranslator(Glossary(data={}), backend=hang, timeout=0.1)
        start = time.perf_counter()
        r = tr.translate_one("hello world", "Vietnamese")
        gate.set()
        self.assertEqual(r.source, Source.ERROR)
        self.assertIn("timed out", r.error)
        self.assertLess(time.perf_counter() - start, 2)


class SupportTests(unittest.TestCase):
    def test_lru_evicts_least_recently_used(self):
        c = LRUCache(2)
        c.put("a", "1")
        c.put("b", "2")
        c.get("a")
        c.put("c", "3")
        self.assertIn("a", c)
        self.assertNotIn("b", c)
        self.assertEqual(len(c), 2)

    def test_run_with_timeout_propagates_exceptions_and_values(self):
        self.assertEqual(run_with_timeout(lambda x: x * 2, 1, 21), 42)
        with self.assertRaises(ValueError):
            run_with_timeout(lambda: (_ for _ in ()).throw(ValueError("boom")), 1)

    def test_result_helpers(self):
        r = TranslationResult("x", "y", Source.GOOGLE)
        self.assertEqual(r.as_tuple(), ("y", "Google", "", None))
        self.assertEqual(r.to_history_item()["source"], "Google")
        self.assertTrue(r.ok)


if __name__ == "__main__":
    unittest.main()
