"""Adaptive computer-vision preprocessing + confidence-scored multi-pass OCR.

Pipeline
--------
1. ``preprocess``: grayscale -> Dark-Mode detection (mean luminance) and
   inversion -> adaptive upscaling -> contrast normalisation (+ Otsu
   binarisation when the contrast is low) -> background-matched padding.
2. Several Tesseract page-segmentation modes (PSM 6 / 7 / 11) run concurrently
   through ``image_to_data``; noisy tokens are dropped and the pass with the
   highest *confidence-weighted character mass* wins (not the first non-empty).
3. Words are mapped back to the original pixel space, rebuilt into lines,
   split on very wide gaps (label ... value, item ... shortcut) and merged
   into paragraphs by geometry, while keeping menu items separate.

All geometry functions are pure and are unit-tested with bounding-box fixtures.
"""
from __future__ import annotations

import logging
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageOps

from .text_utils import canonicalize_shortcuts, clean_cjk_spaces, has_trailing_shortcut, is_cjk_char, is_shortcut, join_text

try:  # lazy-friendly: pure functions stay importable without pytesseract
    import pytesseract
    from pytesseract import Output
except ImportError:  # pragma: no cover
    pytesseract = None
    Output = None

log = logging.getLogger(__name__)

DARK_LUMINANCE_THRESHOLD = 110.0   # mean luminance below this => Dark Mode UI
LOW_CONTRAST_SPREAD = 90.0         # (p99.8 - p0.2) below this => normalise + binarise
MIN_WORD_CONF = 35.0               # noise floor used when *scoring* a pass
KEEP_WORD_CONF = 0.0               # weak tokens are kept ...
LINE_GATE_CONF = 35.0              # ... as long as their line holds at least one word >= this
DEFAULT_PSMS = (6, 7, 11)          # superset; the engine picks two of them per image

MIN_WEAK_LINE_ALNUM = 4            # a low-confidence line must spell at least this many characters
SPACE_GAP_RATIO = 0.3              # gap < 0.3 x average glyph width => no space between tokens
FAST_PATH_CONF = 88.0              # first pass at least this confident => skip the other passes

# layout-grouping tunables (all relative to the median line height)
GAP_SPLIT_FACTOR = 3.0             # horizontal gap that separates two UI items on one row
TIGHT_GAP_FACTOR = 0.55            # max vertical gap between wrapped lines of a paragraph
LOOSE_GAP_FACTOR = 0.90            # ... when the next line clearly continues a sentence
MIN_PREV_WIDTH_RATIO = 0.60        # a wrapped line spans most of the paragraph width
HEIGHT_RATIO_RANGE = (0.65, 1.55)  # same font size on both lines
ALIGN_TOLERANCE = 1.5              # left-edge tolerance (x line height)


# ------------------------------------------------------------------ data model
@dataclass(frozen=True)
class OcrWord:
    text: str
    left: int
    top: int
    width: int
    height: int
    conf: float

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def center_y(self) -> float:
        return self.top + self.height / 2.0


@dataclass
class OcrLine:
    words: List[OcrWord]
    text: str = ""

    def __post_init__(self):
        if not self.text:
            self.text = words_to_text(self.words)

    @property
    def left(self) -> int:
        return min(w.left for w in self.words)

    @property
    def right(self) -> int:
        return max(w.right for w in self.words)

    @property
    def top(self) -> int:
        return min(w.top for w in self.words)

    @property
    def bottom(self) -> int:
        return max(w.bottom for w in self.words)

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def center_x(self) -> float:
        return (self.left + self.right) / 2.0

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2.0

    @property
    def conf(self) -> float:
        return sum(w.conf for w in self.words) / len(self.words)


@dataclass
class OcrBlock:
    text: str
    lines: List[OcrLine]
    kind: str          # "item" (single line / menu entry) or "paragraph"

    @property
    def conf(self) -> float:
        return sum(l.conf for l in self.lines) / len(self.lines)

    @property
    def bbox(self) -> Tuple[int, int, int, int]:
        l = min(x.left for x in self.lines)
        t = min(x.top for x in self.lines)
        return l, t, max(x.right for x in self.lines) - l, max(x.bottom for x in self.lines) - t


@dataclass
class PassSummary:
    psm: int
    n_words: int
    mean_conf: float
    score: float


@dataclass
class OcrResult:
    blocks: List[OcrBlock] = field(default_factory=list)
    psm: Optional[int] = None
    mean_conf: float = 0.0
    inverted: bool = False
    luminance: float = 0.0
    scale: float = 1.0
    elapsed_ms: float = 0.0
    passes: List[PassSummary] = field(default_factory=list)

    @property
    def texts(self) -> List[str]:
        return [b.text for b in self.blocks]

    @property
    def full_text(self) -> str:
        return "\n".join(self.texts)


# ---------------------------------------------------------------- preprocessing
def mean_luminance(img: Image.Image) -> float:
    return float(np.asarray(img.convert("L"), dtype=np.float64).mean())


def is_dark_background(img: Image.Image, threshold: float = DARK_LUMINANCE_THRESHOLD) -> bool:
    """Dark-Mode UI heuristic: mean luminance below ``threshold`` (0-255)."""
    return mean_luminance(img) < threshold


def otsu_threshold(gray: np.ndarray) -> int:
    """Otsu's method on an 8-bit grayscale array (pure NumPy)."""
    hist = np.bincount(gray.ravel().astype(np.uint8), minlength=256).astype(np.float64)
    total = hist.sum()
    if total == 0:
        return 127
    sum_all = float(np.dot(np.arange(256), hist))
    sum_b = 0.0
    w_b = 0.0
    best_var, best_t = -1.0, 127
    for t in range(256):
        w_b += hist[t]
        if w_b == 0:
            continue
        w_f = total - w_b
        if w_f == 0:
            break
        sum_b += t * hist[t]
        m_b, m_f = sum_b / w_b, (sum_all - sum_b) / w_f
        var = w_b * w_f * (m_b - m_f) ** 2
        if var > best_var:
            best_var, best_t = var, t
    return best_t


def estimate_background(gray: np.ndarray) -> int:
    """Median of the border ring: robust against text touching one edge."""
    h, w = gray.shape
    ring = np.concatenate([gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]]) if h > 1 and w > 1 \
        else gray.ravel()
    return int(np.median(ring))


def choose_scale(width: int, height: int) -> float:
    """Upscale small captures so glyphs reach a size Tesseract handles well."""
    if max(width, height) >= 1600:
        return 1.0
    if height < 48:
        scale = 3.0
    elif max(width, height) < 900:
        scale = 2.0
    else:
        scale = 1.0
    while scale > 1.0 and width * scale > 3600:
        scale -= 0.5
    return max(scale, 1.0)


def estimate_line_count(gray: np.ndarray) -> int:
    """Count text rows with a horizontal projection profile (dark ink on light paper).

    Runs of ink rows separated by a gap smaller than 20 % of the run height are
    merged, so the dot of an "i" does not split a line in two.
    """
    if gray.size == 0:
        return 1
    ink = gray <= otsu_threshold(gray)
    if ink.mean() > 0.5:          # background is not the majority: profile is meaningless
        return 2
    rows = ink.sum(axis=1) >= 2
    runs: List[List[int]] = []
    start = None
    for y, on in enumerate(rows):
        if on and start is None:
            start = y
        elif not on and start is not None:
            runs.append([start, y - 1])
            start = None
    if start is not None:
        runs.append([start, len(rows) - 1])
    merged: List[List[int]] = []
    for run in runs:
        if merged:
            prev = merged[-1]
            gap = run[0] - prev[1] - 1
            if gap <= 0.2 * max(prev[1] - prev[0] + 1, run[1] - run[0] + 1):
                prev[1] = run[1]
                continue
        merged.append(run)
    return max(1, len(merged))


def choose_psms(n_lines: int) -> Tuple[int, ...]:
    """Single line: PSM 7 (+6 as safety net). Several lines: PSM 6 (+11 for sparse layouts)."""
    return (7, 6) if n_lines <= 1 else (6, 11)


@dataclass
class Preprocessed:
    image: Image.Image
    inverted: bool
    luminance: float
    scale: float
    pad: int
    binarized: bool = False
    n_lines: int = 1


def preprocess(img: Image.Image, *, invert_dark: bool = True, pad: int = 24,
               scale: Optional[float] = None) -> Preprocessed:
    rgb = img.convert("RGB")
    gray = rgb.convert("L")
    lum = float(np.asarray(gray, dtype=np.float64).mean())
    inverted = bool(invert_dark and lum < DARK_LUMINANCE_THRESHOLD)
    if inverted:
        gray = ImageOps.invert(gray)

    if scale is None:
        scale = choose_scale(gray.width, gray.height)
    if scale != 1.0:
        gray = gray.resize((max(1, int(round(gray.width * scale))),
                            max(1, int(round(gray.height * scale)))), Image.Resampling.LANCZOS)

    arr = np.asarray(gray)
    lo, hi = np.percentile(arr, (0.2, 99.8))   # robust to sparse text (a few % of pixels)
    binarized = False
    if (hi - lo) < LOW_CONTRAST_SPREAD:
        gray = ImageOps.autocontrast(gray, cutoff=1)
        arr = np.asarray(gray)
        t = otsu_threshold(arr)
        gray = Image.fromarray(np.where(arr > t, 255, 0).astype(np.uint8), mode="L")
        binarized = True

    n_lines = estimate_line_count(np.asarray(gray))
    bg = estimate_background(np.asarray(gray))
    gray = ImageOps.expand(gray, border=pad, fill=bg)
    return Preprocessed(gray, inverted, lum, scale, pad, binarized, n_lines)


# ---------------------------------------------------------------- word handling
def words_from_tesseract_data(data: Dict[str, list], min_conf: float = MIN_WORD_CONF) -> List[OcrWord]:
    """Convert ``image_to_data`` output into clean :class:`OcrWord` objects."""
    words: List[OcrWord] = []
    n = len(data.get("text", []))
    for i in range(n):
        text = str(data["text"][i]).strip()
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            continue
        if not text or conf < min_conf:   # conf == -1 marks structural (non-word) rows
            continue
        if len(text) <= 2 and not any(c.isalnum() for c in text) and conf < 70:
            continue                       # stray borders / underlines read as "|" or "_"
        words.append(OcrWord(text, int(data["left"][i]), int(data["top"][i]),
                             int(data["width"][i]), int(data["height"][i]), conf))
    return words


def pass_score(words: Sequence[OcrWord], min_conf: float = MIN_WORD_CONF) -> float:  # noqa: D401
    """Confidence-weighted character mass: sum(len * (conf - tau) / (100 - tau)).

    Plain mean confidence rewards a pass that reads *less* (one confident word
    beats six slightly less confident ones). Weighting by characters rewards
    coverage, and subtracting the noise floor keeps low-confidence garbage from
    inflating a pass.
    """
    span = 100.0 - min_conf
    return sum(len(w.text) * max(0.0, w.conf - min_conf) / span for w in words)


def _map_back(words: Sequence[OcrWord], pre: Preprocessed) -> List[OcrWord]:
    s, p = pre.scale, pre.pad
    return [OcrWord(w.text, int(round((w.left - p) / s)), int(round((w.top - p) / s)),
                    max(1, int(round(w.width / s))), max(1, int(round(w.height / s))), w.conf)
            for w in words]


def words_to_text(words: Sequence[OcrWord]) -> str:
    """Join words; a space is only inserted when the pixel gap really looks like one.

    ``image_to_data`` sometimes splits ``glossary.lookup`` into two tokens with no
    visible gap, which would otherwise surface as a spurious space.
    """
    text = ""
    prev: Optional[OcrWord] = None
    for w in sorted(words, key=lambda x: x.left):
        if prev is not None:
            gap = w.left - prev.right
            char_w = (prev.width / max(len(prev.text), 1) + w.width / max(len(w.text), 1)) / 2.0
            if gap < SPACE_GAP_RATIO * char_w and not (is_cjk_char(prev.text[-1:]) or is_cjk_char(w.text[:1])):
                text += w.text
                prev = w
                continue
        text = join_text(text, w.text)
        prev = w
    return canonicalize_shortcuts(clean_cjk_spaces(text)).strip()


# ------------------------------------------------------------------- layout
def group_words_into_lines(words: Sequence[OcrWord]) -> List[OcrLine]:
    """Cluster words into text lines by vertical overlap (reading order preserved)."""
    ordered = sorted(words, key=lambda w: (w.center_y, w.left))
    clusters: List[List[OcrWord]] = []
    for w in ordered:
        if clusters:
            current = clusters[-1]
            top = min(x.top for x in current)
            bottom = max(x.bottom for x in current)
            overlap = min(bottom, w.bottom) - max(top, w.top)
            if overlap >= 0.5 * min(w.height, bottom - top):
                current.append(w)
                continue
        clusters.append([w])
    return [OcrLine(sorted(c, key=lambda x: x.left)) for c in clusters]


def median_line_height(lines: Sequence[OcrLine]) -> float:
    return float(statistics.median(l.height for l in lines)) if lines else 0.0


def split_line_on_gaps(line: OcrLine, ref_height: float,
                       gap_factor: float = GAP_SPLIT_FACTOR) -> List[OcrLine]:
    """Split a row into separate items at very wide horizontal gaps.

    A trailing keyboard shortcut (``Ctrl+T``) stays attached to its menu item
    even when it is right-aligned far away from the label.
    """
    if len(line.words) < 2 or ref_height <= 0:
        return [line]
    groups: List[List[OcrWord]] = [[line.words[0]]]
    for prev, word in zip(line.words, line.words[1:]):
        if word.left - prev.right > gap_factor * ref_height:
            groups.append([word])
        else:
            groups[-1].append(word)
    segments = [OcrLine(g) for g in groups]
    merged = [segments[0]]
    for seg in segments[1:]:
        if is_shortcut(seg.text):
            merged[-1] = OcrLine(merged[-1].words + seg.words)
        else:
            merged.append(seg)
    return merged


_CODE_SYMBOLS = set("(){}[];=<>_\\/")
_CODE_START = ("def ", "class ", "import ", "from ", "return ", "git ", "for ", "while ", "if ", "#include")


def looks_like_code(text: str) -> bool:
    """Source code lines are separate statements, never wrapped prose."""
    t = text.strip()
    return (sum(c in _CODE_SYMBOLS for c in t) >= 2 or t.startswith(_CODE_START)
            or t.endswith((":", ";", "{", "}")))


def _continues(prev: OcrLine, nxt: OcrLine) -> bool:
    """Does ``nxt`` read like the continuation of ``prev``'s sentence?"""
    first = nxt.text[:1]
    if first and (first.islower() or is_cjk_char(first)):
        return True
    return prev.text.rstrip().endswith((",", ";", "-"))


def should_merge(prev: OcrLine, nxt: OcrLine, ref_height: float, ref_width: float) -> bool:
    """Decide whether two consecutive lines belong to the same paragraph."""
    if ref_height <= 0:
        return False
    if prev.text.rstrip().endswith(("\u2026", "...")) or has_trailing_shortcut(prev.text):
        return False                                  # menu item (opens dialog / has shortcut)
    if looks_like_code(prev.text) or looks_like_code(nxt.text):
        return False                                  # code lines are separate statements
    gap = nxt.top - prev.bottom
    if gap < -0.3 * ref_height:
        return False                                  # same row, different column
    ratio = nxt.height / max(prev.height, 1)
    if not (HEIGHT_RATIO_RANGE[0] <= ratio <= HEIGHT_RATIO_RANGE[1]):
        return False                                  # different font size (title vs body)
    aligned = (abs(nxt.left - prev.left) <= ALIGN_TOLERANCE * ref_height
               or abs(nxt.center_x - prev.center_x) <= ref_height)
    if not aligned:
        return False
    if ref_width > 0 and prev.width < MIN_PREV_WIDTH_RATIO * ref_width:
        return False                                  # a wrapped line runs close to full width
    limit = LOOSE_GAP_FACTOR if _continues(prev, nxt) else TIGHT_GAP_FACTOR
    return gap <= limit * ref_height


def merge_lines_into_blocks(lines: Sequence[OcrLine], *, merge_paragraphs: bool = True) -> List[OcrBlock]:
    if not lines:
        return []
    ref_h = median_line_height(lines)
    ref_w = float(max(l.width for l in lines))
    groups: List[List[OcrLine]] = [[lines[0]]]
    for nxt in lines[1:]:
        if merge_paragraphs and should_merge(groups[-1][-1], nxt, ref_h, ref_w):
            groups[-1].append(nxt)
        else:
            groups.append([nxt])
    blocks = []
    for g in groups:
        text = ""
        for line in g:
            text = join_text(text, line.text)
        blocks.append(OcrBlock(clean_cjk_spaces(text).strip(), g, "paragraph" if len(g) > 1 else "item"))
    return [b for b in blocks if b.text]


def layout_blocks(words: Sequence[OcrWord], *, group_paragraphs: bool = True,
                  gate_conf: Optional[float] = LINE_GATE_CONF) -> List[OcrBlock]:
    """Words -> lines -> (gap-split) items -> paragraphs / menu entries.

    ``gate_conf`` implements line-level gating: a weak token such as the
    ``Ctri+J`` of a shortcut survives when its line contains a confident word.
    A line made only of weak tokens is kept when it still spells at least
    ``MIN_WEAK_LINE_ALNUM`` letters/digits (e.g. ``main.py`` in a monospace
    font, which Tesseract reads with confidence ~0) and dropped otherwise
    (icons and borders usually decode to 1-3 stray characters).
    """
    lines = group_words_into_lines(words)
    if gate_conf is not None:
        lines = [l for l in lines
                 if max(w.conf for w in l.words) >= gate_conf
                 or sum(c.isalnum() for c in l.text) >= MIN_WEAK_LINE_ALNUM]
    if not lines:
        return []
    ref_h = median_line_height(lines)
    items: List[OcrLine] = []
    for line in lines:
        items.extend(split_line_on_gaps(line, ref_h))
    return merge_lines_into_blocks(items, merge_paragraphs=group_paragraphs)


# --------------------------------------------------------------------- engine
def available_languages(preferred: Sequence[str], installed: Optional[Sequence[str]] = None) -> str:
    """Join the preferred Tesseract packs that are installed (``eng`` as fallback)."""
    if installed is None:
        try:
            installed = pytesseract.get_languages(config="")
        except Exception:  # noqa: BLE001
            return "eng"
    matched = [l for l in preferred if l in installed]
    return "+".join(matched) if matched else "eng"


class OcrEngine:
    def __init__(self, lang: str = "eng", tesseract_cmd: Optional[str] = None,
                 psms: Optional[Sequence[int]] = None, min_conf: float = MIN_WORD_CONF,
                 keep_conf: float = KEEP_WORD_CONF, fast_path_conf: Optional[float] = FAST_PATH_CONF,
                 dark_mode_auto_invert: bool = True, auto_group_paragraphs: bool = True,
                 parallel: bool = True):
        self.lang = lang
        self.psms = tuple(psms) if psms else None   # None = choose from the estimated line count
        self.min_conf = min_conf
        self.keep_conf = keep_conf
        self.fast_path_conf = fast_path_conf
        self.dark_mode_auto_invert = dark_mode_auto_invert
        self.auto_group_paragraphs = auto_group_paragraphs
        self.parallel = parallel
        if tesseract_cmd:
            self.set_tesseract(tesseract_cmd)

    @staticmethod
    def set_tesseract(path: str) -> None:
        if pytesseract is None:
            raise RuntimeError("pytesseract is not installed")
        pytesseract.pytesseract.tesseract_cmd = path

    @staticmethod
    def is_available() -> bool:
        if pytesseract is None:
            return False
        try:
            pytesseract.get_tesseract_version()
            return True
        except Exception:  # noqa: BLE001
            return False

    # one OCR pass -----------------------------------------------------------
    def _run_pass(self, image: Image.Image, psm: int) -> Tuple[int, List[OcrWord]]:
        data = pytesseract.image_to_data(image, lang=self.lang, config=f"--psm {psm}",
                                         output_type=Output.DICT)
        return psm, words_from_tesseract_data(data, self.keep_conf)

    def _run_passes(self, image: Image.Image, psms: Sequence[int]) -> List[Tuple[int, List[OcrWord]]]:
        """Run the primary pass; only when it is not clearly good run the others (in parallel)."""
        first = self._run_pass(image, psms[0])
        words = first[1]
        if self.fast_path_conf is not None and words and \
                sum(w.conf for w in words) / len(words) >= self.fast_path_conf:
            return [first]
        rest = list(psms[1:])
        if not rest:
            return [first]
        if self.parallel and len(rest) > 1:
            with ThreadPoolExecutor(max_workers=len(rest)) as pool:
                return [first] + list(pool.map(lambda p: self._run_pass(image, p), rest))
        return [first] + [self._run_pass(image, p) for p in rest]

    @staticmethod
    def _select(passes: List[Tuple[int, List[OcrWord]]], min_conf: float):
        """Pick the best pass; a later pass must beat the incumbent by >3 % to win."""
        best = None
        best_score = 0.0
        for psm, words in passes:
            score = pass_score(words, min_conf)
            if best is None or score > best_score * 1.03:
                best, best_score = (psm, words), score
        return best, best_score

    # public -----------------------------------------------------------------
    def recognize(self, image: Image.Image) -> OcrResult:
        if pytesseract is None:
            raise RuntimeError("pytesseract is not installed")
        t0 = time.perf_counter()
        pre = preprocess(image, invert_dark=self.dark_mode_auto_invert)
        passes = self._run_passes(pre.image, self.psms or choose_psms(pre.n_lines))
        best, score = self._select(passes, self.min_conf)

        if score <= 0:   # nothing found: retry once at the opposite scale (huge / tiny text)
            alt = 1.0 if pre.scale > 1.0 else 0.5
            pre = preprocess(image, invert_dark=self.dark_mode_auto_invert, scale=alt)
            passes = self._run_passes(pre.image, (7, 6))
            best, score = self._select(passes, self.min_conf)

        summaries = [PassSummary(p, len(w), (sum(x.conf for x in w) / len(w)) if w else 0.0,
                                 pass_score(w, self.min_conf)) for p, w in passes]
        psm, words = best if best else (None, [])
        mapped = _map_back(words, pre)
        blocks = layout_blocks(mapped, group_paragraphs=self.auto_group_paragraphs,
                               gate_conf=self.min_conf)
        mean_conf = (sum(w.conf for w in words) / len(words)) if words else 0.0
        return OcrResult(blocks=blocks, psm=psm, mean_conf=mean_conf, inverted=pre.inverted,
                         luminance=pre.luminance, scale=pre.scale,
                         elapsed_ms=(time.perf_counter() - t0) * 1000.0, passes=summaries)
