#!/usr/bin/env python3
"""Quantitative OCR benchmark: raw Tesseract vs. prototype pipeline vs. adaptive pipeline.

Generates reproducible synthetic UI screenshots (seeded), runs three methods on
them and prints a Markdown report with Character Error Rate (CER), Word Error
Rate (WER), latency and block-segmentation accuracy.

    python scripts/benchmark.py                      # 8 samples per case
    python scripts/benchmark.py --samples 20 --seed 7 --output docs/benchmark_results.md

Methods
-------
* raw        : ``pytesseract.image_to_string(image)`` with default settings.
* prototype  : the original single-file app's pipeline (2x upscale + padding,
               PSM 6 -> 7 -> 11, first non-empty result wins).
* adaptive   : ``core.ocr_engine.OcrEngine`` (this project).

Caveat: images are *synthetic* clean renders, so absolute numbers are not what
you would get on arbitrary real screenshots. Re-run on your own machine and
fonts before quoting numbers.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import platform
import random
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import PIL  # noqa: E402
import pytesseract  # noqa: E402
from PIL import Image, ImageDraw, ImageFont, ImageOps  # noqa: E402

import config  # noqa: E402
from core.metrics import cer, wer  # noqa: E402
from core.ocr_engine import OcrEngine, available_languages  # noqa: E402
from core.languages import PREFERRED_OCR_LANGS  # noqa: E402
from core.text_utils import clean_cjk_spaces  # noqa: E402

# --------------------------------------------------------------------------- fonts
SANS = ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf", "FreeSans.ttf"]
MONO = ["consola.ttf", "DejaVuSansMono.ttf", "LiberationMono-Regular.ttf", "cour.ttf"]
CJK = ["msyh.ttc", "msyh.ttf", "NotoSansCJK-Regular.ttc", "NotoSansCJKsc-Regular.otf", "simhei.ttf"]
_font_cache: Dict[Tuple[str, int], ImageFont.ImageFont] = {}


def find_font(candidates: List[str], size: int, required: bool = True):
    key = (candidates[0], size)
    if key in _font_cache:
        return _font_cache[key]
    font = None
    for name in candidates:
        try:
            font = ImageFont.truetype(name, size)
            break
        except OSError:
            for root in ("/usr/share/fonts", "/usr/local/share/fonts", "C:/Windows/Fonts"):
                hits = glob.glob(os.path.join(root, "**", name), recursive=True)
                if hits:
                    try:
                        font = ImageFont.truetype(hits[0], size)
                        break
                    except OSError:
                        pass
            if font:
                break
    if font is None and required:
        try:
            font = ImageFont.load_default(size)
        except TypeError:
            font = ImageFont.load_default()
    _font_cache[key] = font
    return font


# --------------------------------------------------------------------------- cases
@dataclass
class Sample:
    image: Image.Image
    expected_lines: List[str]
    expected_blocks: int


MENU = [("New tab", "Ctrl+T"), ("New window", "Ctrl+N"), ("New Incognito window", "Ctrl+Shift+N"),
        ("History", None), ("Downloads", "Ctrl+J"), ("Bookmarks and lists", None),
        ("Extensions", None), ("Delete browsing data", "Ctrl+Shift+Del"), ("Print...", "Ctrl+P"),
        ("Settings", None), ("Find and edit", "Ctrl+F"), ("Zoom", None), ("Help", None)]
CJK_MENU = [("新标签页", "Ctrl+T"), ("新窗口", "Ctrl+N"), ("历史记录", None), ("下载内容", "Ctrl+J"),
            ("设置", None), ("书签和清单", None), ("扩展程序", None), ("打印", "Ctrl+P")]
IDE_LINES = ["EXPLORER", "OPEN EDITORS", "Source Control", "Run and Debug", "Extensions", "Problems",
             "Debug Console", "Terminal", "main.py", "Ln 42, Col 8", "Spaces: 4", "UTF-8",
             "def translate(text, lang):", "import numpy as np", "return glossary.lookup(text)",
             "git checkout -b feature"]
SENTENCES = [
    "Press the hotkey and drag over any text on the screen to read its translation right next to it.",
    "The local glossary is checked first so that interface terms stay consistent and work offline.",
    "Only the lines that cannot be matched are sent to the online service as one combined request.",
    "Dark themes reduce eye strain at night but they often confuse optical character recognition.",
]
LIGHT = [("#ffffff", "#202124"), ("#f3f3f3", "#1f1f1f"), ("#fafafa", "#333333")]
DARK = [("#1e1e1e", "#d4d4d4"), ("#2b2d31", "#dbdee1"), ("#202124", "#e8eaed"), ("#0d1117", "#c9d1d9")]


def _menu_image(rng, items, font, theme, pitch, width=340) -> Image.Image:
    bg, fg = theme
    img = Image.new("RGB", (width, pitch * len(items) + 14), bg)
    draw = ImageDraw.Draw(img)
    for i, (label, shortcut) in enumerate(items):
        y = 8 + i * pitch
        draw.text((12, y), label, font=font, fill=fg)
        if shortcut:
            draw.text((width - 12 - draw.textlength(shortcut, font=font), y), shortcut, font=font, fill=fg)
    return img


def _menu_sample(rng, pool, font_candidates, dark: bool) -> Sample:
    items = rng.sample(pool, rng.randint(4, 6))
    font = find_font(font_candidates, rng.choice([12, 13, 14]))
    theme = rng.choice(DARK if dark else LIGHT)
    img = _menu_image(rng, items, font, theme, pitch=rng.choice([27, 29, 31]))
    lines = [f"{l} {s}" if s else l for l, s in items]
    return Sample(img, lines, len(lines))


def case_light_menu(rng): return _menu_sample(rng, MENU, SANS, dark=False)
def case_dark_menu(rng): return _menu_sample(rng, MENU, SANS, dark=True)


def case_dark_ide(rng) -> Sample:
    lines = rng.sample(IDE_LINES, rng.randint(5, 7))
    font = find_font(MONO, rng.choice([13, 14]))
    bg, fg = rng.choice(DARK)
    pitch = rng.choice([24, 26])
    img = Image.new("RGB", (330, pitch * len(lines) + 14), bg)
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((12, 8 + i * pitch), line, font=font, fill=fg)
    return Sample(img, lines, len(lines))


def _wrap(font, text: str, max_px: int) -> List[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if font.getlength(trial) <= max_px or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    return lines + [cur]


def _paragraph_sample(rng, size, theme, max_px, spacing) -> Sample:
    font = find_font(SANS, size)
    lines = _wrap(font, rng.choice(SENTENCES), max_px)
    bg, fg = theme
    pitch = int(size * spacing)
    img = Image.new("RGB", (max_px + 24, pitch * len(lines) + 16), bg)
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((12, 8 + i * pitch), line, font=font, fill=fg)
    return Sample(img, lines, 1)


def case_small_font(rng): return _paragraph_sample(rng, 10, rng.choice(LIGHT), 280, 1.45)
def case_paragraph(rng): return _paragraph_sample(rng, 14, rng.choice(LIGHT + DARK), 330, 1.45)


def case_cjk(rng) -> Sample:
    items = rng.sample(CJK_MENU, rng.randint(4, 5))
    return _menu_sample_from(rng, items)


def _menu_sample_from(rng, items) -> Sample:
    font = find_font(CJK, 14)
    img = _menu_image(rng, items, font, rng.choice(LIGHT), pitch=30)
    lines = [f"{l} {s}" if s else l for l, s in items]
    return Sample(img, lines, len(lines))


CASES: List[Tuple[str, str, Callable, bool]] = [
    # key, description, generator, dark theme?
    ("light_menu", "Light-mode menu + shortcuts (12-14 px)", case_light_menu, False),
    ("dark_menu", "Dark-mode menu + shortcuts (12-14 px)", case_dark_menu, True),
    ("dark_ide", "Dark-mode IDE labels / code (monospace)", case_dark_ide, True),
    ("small_font", "Small 10 px paragraph (3-4 lines)", case_small_font, False),
    ("paragraph", "14 px wrapped paragraph, light + dark", case_paragraph, False),
]


# --------------------------------------------------------------------------- methods
def raw_tesseract(img: Image.Image, lang: str) -> List[str]:
    text = pytesseract.image_to_string(img.convert("RGB"), lang=lang)
    return [clean_cjk_spaces(l.strip()) for l in text.splitlines() if l.strip()]


def _prototype_prepare(img, scale, pad=24):
    rgb = img.convert("RGB")
    if scale != 1.0:
        rgb = rgb.resize((max(1, int(rgb.width * scale)), max(1, int(rgb.height * scale))),
                         Image.Resampling.LANCZOS)
    return ImageOps.expand(rgb, border=pad, fill=rgb.getpixel((0, 0)))


def prototype_pipeline(img: Image.Image, lang: str) -> List[str]:
    """Faithful re-implementation of the original single-file prototype's OCR step."""
    prepared = _prototype_prepare(img, 2.0 if max(img.size) < 900 else 1.0)
    for psm in ("--psm 6", "--psm 7", "--psm 11"):
        raw = pytesseract.image_to_string(prepared, lang=lang, config=psm).strip()
        if raw:
            return [clean_cjk_spaces(l.strip()) for l in raw.split("\n") if l.strip()]
    return []


def timed(fn: Callable[[], List[str]]) -> Tuple[List[str], float]:
    t0 = time.perf_counter()
    out = fn()
    return out, (time.perf_counter() - t0) * 1000.0


# --------------------------------------------------------------------------- runner
METHODS = ["raw", "prototype", "adaptive"]
METHOD_LABELS = {"raw": "Raw Tesseract", "prototype": "Prototype pipeline", "adaptive": "Adaptive pipeline"}


def run(samples: int, seed: int, lang: str, engine: OcrEngine, cases) -> Dict[str, Dict[str, dict]]:
    results: Dict[str, Dict[str, dict]] = {}
    for key, _desc, gen, _dark in cases:
        rng = random.Random(f"{seed}:{key}")
        acc = {m: {"cer": [], "wer": [], "ms": [], "blocks_ok": 0} for m in METHODS}
        for _ in range(samples):
            s = gen(rng)
            reference = " ".join(s.expected_lines)
            outputs = {
                "raw": timed(lambda: raw_tesseract(s.image, lang)),
                "prototype": timed(lambda: prototype_pipeline(s.image, lang)),
                "adaptive": timed(lambda: engine.recognize(s.image).texts),
            }
            for m, (lines, ms) in outputs.items():
                hyp = " ".join(lines)
                acc[m]["cer"].append(cer(reference, hyp))
                acc[m]["wer"].append(wer(reference, hyp))
                acc[m]["ms"].append(ms)
                acc[m]["blocks_ok"] += int(len(lines) == s.expected_blocks)
        results[key] = {m: {"cer": statistics.mean(a["cer"]), "wer": statistics.mean(a["wer"]),
                            "ms": statistics.median(a["ms"]), "blocks_ok": a["blocks_ok"] / samples,
                            "n": samples} for m, a in acc.items()}
    return results


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def reduction(old: float, new: float) -> str:
    if old <= 0:
        return "n/a" if new <= 0 else "worse"
    return f"{(old - new) / old * 100:.0f}%"


def aggregate(results, keys, field_name):
    return {m: statistics.mean(results[k][m][field_name] for k in keys) for m in METHODS}


def markdown(results, cases, args, env: List[str]) -> str:
    desc = {k: d for k, d, *_ in cases}
    dark_keys = [k for k, _, _, dark in cases if dark and k in results]
    out = ["### Experimental setup", ""] + [f"- {line}" for line in env] + [""]
    out += ["### OCR accuracy and latency", "",
            "| Case | Method | CER | WER | Latency (ms, median) |", "| :-- | :-- | --: | --: | --: |"]
    for k in results:
        for m in METHODS:
            r = results[k][m]
            out.append(f"| {desc[k]} | {METHOD_LABELS[m]} | {pct(r['cer'])} | {pct(r['wer'])} | {r['ms']:.0f} |")
    out += ["", "### Summary (mean over cases)", "",
            "| Subset | Method | CER | WER | Latency (ms) |", "| :-- | :-- | --: | --: | --: |"]
    subsets = [("All cases", list(results))]
    if dark_keys:
        subsets.append(("Dark-mode cases", dark_keys))
    for name, keys in subsets:
        c, w, l = (aggregate(results, keys, f) for f in ("cer", "wer", "ms"))
        for m in METHODS:
            out.append(f"| {name} | {METHOD_LABELS[m]} | {pct(c[m])} | {pct(w[m])} | {l[m]:.0f} |")
        out.append(f"| {name} | **Error reduction of adaptive vs raw / vs prototype** | "
                   f"{reduction(c['raw'], c['adaptive'])} / {reduction(c['prototype'], c['adaptive'])} | "
                   f"{reduction(w['raw'], w['adaptive'])} / {reduction(w['prototype'], w['adaptive'])} | |")
    out += ["", "### Structure: output blocks == expected blocks", "",
            "Menus should come out as one block per item (shortcut attached); a wrapped paragraph as one block.", "",
            "| Case | Raw | Prototype | Adaptive |", "| :-- | --: | --: | --: |"]
    for k in results:
        out.append(f"| {desc[k]} | " + " | ".join(pct(results[k][m]["blocks_ok"]) for m in METHODS) + " |")
    out += ["", f"_{args.samples} generated samples per case, seed {args.seed}. Error reduction = (old - new) / old; negative means the adaptive pipeline is worse._"]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--samples", type=int, default=8, help="generated images per case (default 8)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--output", help="also write the Markdown report to this file")
    ap.add_argument("--json", help="also write raw metrics as JSON")
    args = ap.parse_args()

    tess = config.find_tesseract()
    if not tess:
        print("Tesseract not found. Install it or put it on PATH.", file=sys.stderr)
        return 1
    OcrEngine.set_tesseract(tess)
    langs = available_languages(PREFERRED_OCR_LANGS)
    installed = set(pytesseract.get_languages(config=""))
    cases = list(CASES)
    notes = []
    if "chi_sim" in installed and find_font(CJK, 14, required=False):
        cases.append(("cjk_menu", "Chinese menu + shortcuts (14 px)", case_cjk, False))
    else:
        notes.append("CJK case skipped (needs the `chi_sim` Tesseract pack and a CJK font)")
    lang = langs
    engine = OcrEngine(lang=lang)

    try:
        tess_version = str(pytesseract.get_tesseract_version())
    except Exception:  # noqa: BLE001
        tess_version = "unknown"
    env = [f"Date: {date.today().isoformat()}", f"Platform: {platform.system()} {platform.release()}, "
           f"Python {platform.python_version()}, Pillow {PIL.__version__}",
           f"Tesseract {tess_version}, OCR languages: `{lang}`",
           "Images: synthetic anti-aliased renders (not real screenshots)"] + notes

    print(f"Running {len(cases)} cases x {args.samples} samples x 3 methods ...", file=sys.stderr)
    results = run(args.samples, args.seed, lang, engine, cases)
    report = markdown(results, cases, args, env)
    print(report)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(report + "\n", encoding="utf-8")
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
