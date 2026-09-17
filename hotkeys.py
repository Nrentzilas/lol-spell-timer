"""System-wide hotkeys for arming timers."""

from __future__ import annotations
import ctypes
import logging
import queue
import threading
import time
from ctypes import wintypes
from typing import List, Optional, Tuple

log = logging.getLogger("hotkeys")

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
PM_REMOVE = 0x0001

NAME_TO_MOD = {
    "alt": MOD_ALT,
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "win": MOD_WIN, "super": MOD_WIN,
}

SLOTS = 5
BASE_ID = 0xB000


def parse_modifier(spec: str) -> Tuple[int, Optional[str]]:
    spec = (spec or "").strip().lower()
    if not spec:
        return 0, "empty"
    mods = 0
    for part in spec.replace(" ", "").split("+"):
        if not part:
            continue
        if part == "shift":
            return 0, "shift is reserved for the second spell"
        if part not in NAME_TO_MOD:
            return 0, f"unknown modifier '{part}'"
        mods |= NAME_TO_MOD[part]
    if not mods:
        return 0, "no modifier given"
    return mods, None


def describe(spec: str) -> str:
    mods, err = parse_modifier(spec)
    if err:
        return "off"
    pretty = "+".join(p.capitalize() for p in spec.strip().lower().split("+"))
    return f"{pretty}+1-5"


class HotkeyManager:
    def __init__(self, spec: str = ""):
        self.spec = spec or ""
        self.mods, self.error = parse_modifier(self.spec)
        self.events: "queue.Queue[Tuple[int, int]]" = queue.Queue()
        self.registered: List[int] = []
        self.failed: List[str] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def enabled(self) -> bool:
        return bool(self.mods)

    def start(self):
        if not self.enabled:
            if self.spec:
                log.warning(f"Disabled: {self.error}.")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.5)
        self._thread = None

    def poll(self) -> List[Tuple[int, int]]:
        out = []
        while True:
            try:
                out.append(self.events.get_nowait())
            except queue.Empty:
                break
        return out


    def _run(self):
        user32 = ctypes.windll.user32
        for slot in range(SLOTS):
            vk = 0x31 + slot
            for spell in (0, 1):
                hk_id = BASE_ID + slot * 2 + spell
                mods = self.mods | MOD_NOREPEAT | (MOD_SHIFT if spell else 0)
                if user32.RegisterHotKey(None, hk_id, mods, vk):
                    self.registered.append(hk_id)
                else:
                    combo = f"{self.spec}{'+shift' if spell else ''}+{slot + 1}"
                    self.failed.append(combo)

        if self.failed:
            log.warning(f"Already taken by another app, skipped: {', '.join(self.failed)}")
        if not self.registered:
            log.warning("Nothing could be registered.")
            return
        log.info(f"{len(self.registered)} bound ({describe(self.spec)}, "
                 f"add Shift for the second spell).")

        msg = wintypes.MSG()
        try:
            while not self._stop.is_set():
                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                    if msg.message == WM_HOTKEY:
                        offset = int(msg.wParam) - BASE_ID
                        if 0 <= offset < SLOTS * 2:
                            self.events.put((offset // 2, offset % 2))
                time.sleep(0.02)
        finally:
            for hk_id in self.registered:
                try:
                    user32.UnregisterHotKey(None, hk_id)
                except Exception:
                    pass
            self.registered.clear()
