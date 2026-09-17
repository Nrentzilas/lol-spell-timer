"""Win32 shims for a borderless, never-focused, always-on-top overlay."""

from __future__ import annotations
import ctypes
import logging
from typing import Optional, Tuple

log = logging.getLogger("win32")

ERROR_ALREADY_EXISTS = 183


class SingleInstanceChecker:
    """A named mutex, so a second launch quietly gives up.

    The handle is kept on the instance on purpose: letting it be collected
    releases the mutex and lets a second copy start.
    """

    def __init__(self, app_name: str = r"Global\LoLSpellTimer_v9_5"):
        self.mutex_name = app_name
        self.mutex = None

    def is_already_running(self) -> bool:
        kernel32 = ctypes.windll.kernel32
        self.mutex = kernel32.CreateMutexW(None, False, self.mutex_name)
        return kernel32.GetLastError() == ERROR_ALREADY_EXISTS


class Win32Utils:
    GWL_EXSTYLE = -20
    WS_EX_NOACTIVATE = 0x08000000
    WS_EX_TOPMOST = 0x00000008

    HWND_TOPMOST = -1
    SW_SHOWNOACTIVATE = 4
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040

    SM_XVIRTUALSCREEN = 76
    SM_YVIRTUALSCREEN = 77
    SM_CXVIRTUALSCREEN = 78
    SM_CYVIRTUALSCREEN = 79

    @staticmethod
    def virtual_screen() -> Tuple[int, int, int, int]:
        try:
            get = ctypes.windll.user32.GetSystemMetrics
            w = get(Win32Utils.SM_CXVIRTUALSCREEN)
            h = get(Win32Utils.SM_CYVIRTUALSCREEN)
            if w and h:
                return (get(Win32Utils.SM_XVIRTUALSCREEN),
                        get(Win32Utils.SM_YVIRTUALSCREEN), w, h)
        except Exception:
            pass
        return (0, 0, 1920, 1080)

    @staticmethod
    def set_no_focus(hwnd: int) -> None:
        try:
            style = ctypes.windll.user32.GetWindowLongW(hwnd, Win32Utils.GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(
                hwnd,
                Win32Utils.GWL_EXSTYLE,
                style | Win32Utils.WS_EX_NOACTIVATE | Win32Utils.WS_EX_TOPMOST
            )
        except Exception:
            pass

    @staticmethod
    def hwnd_of(window) -> int:
        window.update_idletasks()
        return ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()

    @staticmethod
    def show_no_activate(hwnd: int) -> None:
        try:
            u = ctypes.windll.user32
            u.ShowWindow(hwnd, Win32Utils.SW_SHOWNOACTIVATE)
            u.SetWindowPos(hwnd, Win32Utils.HWND_TOPMOST, 0, 0, 0, 0,
                           Win32Utils.SWP_NOMOVE | Win32Utils.SWP_NOSIZE |
                           Win32Utils.SWP_NOACTIVATE | Win32Utils.SWP_SHOWWINDOW)
        except Exception:
            pass

    @staticmethod
    def raise_topmost(hwnd: int) -> None:
        try:
            ctypes.windll.user32.SetWindowPos(
                hwnd, Win32Utils.HWND_TOPMOST, 0, 0, 0, 0,
                Win32Utils.SWP_NOMOVE | Win32Utils.SWP_NOSIZE | Win32Utils.SWP_NOACTIVATE)
        except Exception:
            pass

    @staticmethod
    def foreground_window() -> int:
        try:
            return ctypes.windll.user32.GetForegroundWindow() or 0
        except Exception:
            return 0


class TopmostKeeper:
    """Re-asserts topmost only when something else has taken the foreground.

    Another window coming up is the only thing that puts the overlay behind
    League, so watching the foreground handle costs one cheap call per tick
    instead of a SetWindowPos several times a second.
    """

    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        self._last_foreground: Optional[int] = None

    def reset(self) -> None:
        self._last_foreground = None

    def tick(self) -> bool:
        """True if topmost was re-asserted on this call."""
        current = Win32Utils.foreground_window()
        if current == self._last_foreground:
            return False
        self._last_foreground = current
        Win32Utils.raise_topmost(self.hwnd)
        return True
