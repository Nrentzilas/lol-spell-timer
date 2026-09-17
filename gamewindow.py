"""Locating the League game window and working out how it is presenting."""

from __future__ import annotations
import ctypes
import logging
from ctypes import wintypes
from typing import Optional, Tuple

log = logging.getLogger("gamewin")

LEAGUE_CLASS = "RiotWindowClass"

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008

FULLSCREEN = "fullscreen"
BORDERLESS = "borderless"
WINDOWED = "windowed"


def find() -> int:
    try:
        return ctypes.windll.user32.FindWindowW(LEAGUE_CLASS, None) or 0
    except Exception:
        return 0


def is_foreground(hwnd: Optional[int] = None) -> bool:
    h = find() if hwnd is None else hwnd
    if not h:
        return False
    try:
        return ctypes.windll.user32.GetForegroundWindow() == h
    except Exception:
        return False


def rect(hwnd: int) -> Optional[Tuple[int, int, int, int]]:
    try:
        r = wintypes.RECT()
        if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return None
        return r.left, r.top, r.right - r.left, r.bottom - r.top
    except Exception:
        return None


def mode() -> Optional[str]:
    h = find()
    if not h:
        return None
    box = rect(h)
    if not box:
        return None
    _, _, w, hgt = box
    try:
        u = ctypes.windll.user32
        sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
        topmost = bool(u.GetWindowLongW(h, GWL_EXSTYLE) & WS_EX_TOPMOST)
    except Exception:
        return None
    covers = w >= sw and hgt >= sh
    if covers and topmost:
        return FULLSCREEN
    return BORDERLESS if covers else WINDOWED
