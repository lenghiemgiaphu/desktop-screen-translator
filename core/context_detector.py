"""Detect which application the user is looking at (foreground window).

On Windows the process image name and window title are read with plain
``ctypes`` calls (``GetForegroundWindow``, ``GetWindowThreadProcessId``,
``QueryFullProcessImageNameW``) -- no third-party dependency. The mapping from
process to *domain* is pure Python and therefore unit-testable on any OS.
"""
from __future__ import annotations

import ntpath
import os
import re
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

GENERAL = "general"
GLOBAL_CONTEXT = "global"  # glossary entries valid in every application
DOMAINS = ("browser", "ide", "design", "office", "gaming", GENERAL)
DOMAIN_LABELS = {
    "browser": "Browser", "ide": "IDE", "design": "Design",
    "office": "Office", "gaming": "Gaming", GENERAL: "General",
}

# process image name (lower case) -> (domain, friendly application name)
PROCESS_MAP: Dict[str, Tuple[str, str]] = {
    # browsers
    "chrome.exe": ("browser", "Chrome"), "msedge.exe": ("browser", "Edge"),
    "firefox.exe": ("browser", "Firefox"), "brave.exe": ("browser", "Brave"),
    "opera.exe": ("browser", "Opera"), "vivaldi.exe": ("browser", "Vivaldi"),
    "coccoc.exe": ("browser", "Cốc Cốc"),
    # IDEs / code editors
    "code.exe": ("ide", "VS Code"), "cursor.exe": ("ide", "Cursor"),
    "devenv.exe": ("ide", "Visual Studio"), "pycharm64.exe": ("ide", "PyCharm"),
    "idea64.exe": ("ide", "IntelliJ IDEA"), "clion64.exe": ("ide", "CLion"),
    "webstorm64.exe": ("ide", "WebStorm"), "rider64.exe": ("ide", "Rider"),
    "studio64.exe": ("ide", "Android Studio"), "sublime_text.exe": ("ide", "Sublime Text"),
    "notepad++.exe": ("ide", "Notepad++"), "eclipse.exe": ("ide", "Eclipse"),
    "windowsterminal.exe": ("ide", "Terminal"),
    # design
    "figma.exe": ("design", "Figma"), "photoshop.exe": ("design", "Photoshop"),
    "illustrator.exe": ("design", "Illustrator"), "xd.exe": ("design", "Adobe XD"),
    "blender.exe": ("design", "Blender"), "krita.exe": ("design", "Krita"),
    "inkscape.exe": ("design", "Inkscape"), "canva.exe": ("design", "Canva"),
    "afterfx.exe": ("design", "After Effects"),
    "adobe premiere pro.exe": ("design", "Premiere Pro"),
    # office / documents
    "winword.exe": ("office", "Word"), "excel.exe": ("office", "Excel"),
    "powerpnt.exe": ("office", "PowerPoint"), "outlook.exe": ("office", "Outlook"),
    "onenote.exe": ("office", "OneNote"), "acrobat.exe": ("office", "Acrobat"),
    "acrord32.exe": ("office", "Acrobat Reader"), "soffice.exe": ("office", "LibreOffice"),
    "soffice.bin": ("office", "LibreOffice"), "notion.exe": ("office", "Notion"),
    "obsidian.exe": ("office", "Obsidian"),
    # gaming
    "steam.exe": ("gaming", "Steam"), "epicgameslauncher.exe": ("gaming", "Epic Games"),
    "leagueclient.exe": ("gaming", "League of Legends"),
    "riotclientservices.exe": ("gaming", "Riot Client"),
    "genshinimpact.exe": ("gaming", "Genshin Impact"),
    "valorant-win64-shipping.exe": ("gaming", "Valorant"),
}

# Window-title hints for processes that host many apps (UWP frame host, Electron shells ...)
TITLE_HINTS: List[Tuple["re.Pattern[str]", str, str]] = [
    (re.compile(r"visual studio code|pycharm|intellij|android studio", re.I), "ide", ""),
    (re.compile(r"\bfigma\b|photoshop|illustrator", re.I), "design", ""),
    (re.compile(r"\bword\b|\bexcel\b|powerpoint|google docs|google sheets", re.I), "office", ""),
]

# Shell processes that say nothing about what the user is reading
IGNORED_PROCESSES = {"explorer.exe", "shellexperiencehost.exe", "searchhost.exe",
                     "searchapp.exe", "startmenuexperiencehost.exe", "python.exe",
                     "pythonw.exe"}


@dataclass(frozen=True)
class AppContext:
    process_name: str = ""
    window_title: str = ""
    domain: str = GENERAL
    app_label: str = ""

    @property
    def domain_label(self) -> str:
        return DOMAIN_LABELS.get(self.domain, self.domain.title())

    @property
    def label(self) -> str:
        """Human readable, e.g. ``VS Code (IDE)`` or ``General``."""
        if self.domain == GENERAL:
            return self.app_label or "General"
        if self.app_label and self.app_label != self.domain_label:
            return f"{self.app_label} ({self.domain_label})"
        return self.domain_label


def process_basename(path_or_name: str) -> str:
    """``C:\\Program Files\\Google\\Chrome\\chrome.exe`` -> ``chrome.exe`` (lower case)."""
    return ntpath.basename(path_or_name.replace("/", "\\")).lower()


def classify(process_name: str, window_title: str = "") -> AppContext:
    """Map a process image name (+ window title) to an :class:`AppContext`."""
    exe = process_basename(process_name or "")
    if exe in PROCESS_MAP:
        domain, label = PROCESS_MAP[exe]
        return AppContext(exe, window_title or "", domain, label)
    if exe not in IGNORED_PROCESSES:
        for pattern, domain, label in TITLE_HINTS:
            if pattern.search(window_title or ""):
                return AppContext(exe, window_title or "", domain, label)
    return AppContext(exe, window_title or "", GENERAL, "")


def get_foreground_context() -> AppContext:
    """Return the context of the foreground window; ``general`` when unknown."""
    if sys.platform != "win32":
        return AppContext()
    try:
        return _win32_foreground()
    except Exception:  # never let context detection break a capture
        return AppContext()


def _win32_foreground() -> AppContext:  # pragma: no cover - Windows only
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return AppContext()

    length = user32.GetWindowTextLengthW(hwnd)
    title_buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, title_buf, length + 1)

    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == os.getpid():
        return AppContext(window_title=title_buf.value)

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    image = ""
    if handle:
        try:
            size = wintypes.DWORD(32768)
            buf = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                image = buf.value
        finally:
            kernel32.CloseHandle(handle)
    return classify(image, title_buf.value)
