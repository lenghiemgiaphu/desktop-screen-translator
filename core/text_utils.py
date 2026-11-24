"""Dependency-free text helpers shared by the OCR, glossary and translation layers."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# CJK ideographs, kana, hangul, CJK punctuation and full-width forms
CJK_CHAR_CLASS = r"\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af\u3000-\u303f\uff00-\uffef"
_CJK_GAP_RE = re.compile(rf"(?<=[{CJK_CHAR_CLASS}])\s+(?=[{CJK_CHAR_CLASS}])")
_CJK_CHAR_RE = re.compile(rf"[{CJK_CHAR_CLASS}]")
_HAN_RE = re.compile(r"[\u4e00-\u9fff]")
_KANA_RE = re.compile(r"[\u3040-\u30ff]")
_HANGUL_RE = re.compile(r"[\uac00-\ud7af]")
_THAI_RE = re.compile(r"[\u0e00-\u0e7f]")
_CYRILLIC_RE = re.compile(r"[\u0400-\u04ff]")
# letters that (practically) only occur in Vietnamese
_VIET_RE = re.compile(
    "[ăđơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]", re.IGNORECASE)

_KEY = r"(?:f\d{1,2}|[a-z0-9]|enter|esc|tab|del|delete|space|home|end|pgup|pgdn|[+\-=,./\[\]])"
# "Ctrl" is frequently mangled by OCR into Ctri / Ctril / Cti / Ctr, so accept those forms
_CTRL = r"c[tl]r?[l1i|]*"
_MOD = rf"(?:{_CTRL}|control|alt|shift|cmd|win|meta)"
_SHORTCUT_RE = re.compile(
    rf"\s+(?:(?:{_MOD}\s*\+\s*)+{_KEY}|f\d{{1,2}})\s*$", re.IGNORECASE)
_PURE_SHORTCUT_RE = re.compile(
    rf"(?:(?:{_MOD}\s*\+\s*)+{_KEY}|f\d{{1,2}})", re.IGNORECASE)
_ELLIPSIS_RE = re.compile(r"(?:\.{2,}|\u2026+)\s*$")


def is_cjk_char(ch: str) -> bool:
    return bool(ch) and bool(_CJK_CHAR_RE.match(ch))


def has_cjk(text: str) -> bool:
    return bool(_CJK_CHAR_RE.search(text))


def has_han(text: str) -> bool:
    """True when the text contains Chinese ideographs (used for Pinyin display)."""
    return bool(_HAN_RE.search(text))


def clean_cjk_spaces(text: str) -> str:
    """Remove the spurious spaces Tesseract inserts between CJK characters."""
    return _CJK_GAP_RE.sub("", text)


def join_text(left: str, right: str) -> str:
    """Join two text fragments, without a space when both sides are CJK."""
    if not left:
        return right
    if not right:
        return left
    if is_cjk_char(left[-1]) and is_cjk_char(right[0]):
        return left + right
    return f"{left} {right}"


def collapse_ws(text: str) -> str:
    return " ".join(text.split())


def nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


@dataclass(frozen=True)
class UiText:
    """A UI string split into its translatable core and decorations."""
    core: str
    ellipsis: str = ""   # "…" if the item opens a dialog
    shortcut: str = ""   # e.g. "Ctrl+T"


def has_trailing_shortcut(text: str) -> bool:
    return bool(_SHORTCUT_RE.search(nfkc(text)))


_MOD_FIX_RE = re.compile(
    rf"(?<![A-Za-z])({_CTRL}|control|alt|shift|win|cmd|meta)\s*\+\s*"
    rf"(?=(?:f\d{{1,2}}|[a-z0-9]|enter|esc|tab|del|delete|space|home|end|pgup|pgdn|{_CTRL}|alt|shift)(?![a-z0-9]))",
    re.IGNORECASE)
_CANON_MOD = {"alt": "Alt", "shift": "Shift", "win": "Win", "cmd": "Cmd", "meta": "Meta", "control": "Ctrl"}


def canonicalize_shortcuts(text: str) -> str:
    """Repair OCR slips in keyboard shortcuts: ``Ctri+ Shift+ Del`` -> ``Ctrl+Shift+Del``.

    Only fires when a modifier is followed by ``+`` and then something that
    looks like a key, so prose such as "shift + click" is left untouched.
    """
    def fix(m: "re.Match[str]") -> str:
        return _CANON_MOD.get(m.group(1).lower(), "Ctrl") + "+"
    return _MOD_FIX_RE.sub(fix, text)


def is_shortcut(text: str) -> bool:
    """True when the whole string is a keyboard shortcut such as ``Ctrl+Shift+T``."""
    return bool(_PURE_SHORTCUT_RE.fullmatch(collapse_ws(nfkc(text))))


def decompose_ui_text(text: str) -> UiText:
    """Split "Print...   Ctrl+P" into core="Print", ellipsis="…", shortcut="Ctrl+P"."""
    work = collapse_ws(clean_cjk_spaces(nfkc(text)))
    shortcut = ""
    m = _SHORTCUT_RE.search(work)
    if m:
        shortcut = canonicalize_shortcuts(collapse_ws(m.group(0))).replace(" ", "")
        work = work[:m.start()]
    ellipsis = ""
    m = _ELLIPSIS_RE.search(work)
    if m:
        ellipsis = "\u2026"
        work = work[:m.start()]
    return UiText(core=work.rstrip(" .\u00b7").strip(), ellipsis=ellipsis, shortcut=shortcut)


def normalize_for_match(text: str) -> str:
    """Canonical form used for glossary lookup (NFKC, no shortcut/ellipsis, lower-case)."""
    return decompose_ui_text(text).core.casefold()


def guess_lang_code(text: str) -> str:
    """Best-effort source-language guess, only used to label glossary entries."""
    if _KANA_RE.search(text):
        return "ja"
    if _HANGUL_RE.search(text):
        return "ko"
    if _HAN_RE.search(text):
        return "zh"
    if _THAI_RE.search(text):
        return "th"
    if _CYRILLIC_RE.search(text):
        return "ru"
    if _VIET_RE.search(text):
        return "vi"
    return "en"
