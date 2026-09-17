"""A short beep when a tracked spell comes back up.

The overlay only helps while you are looking at it, and you are looking at the
game. A cue is the one way this app can tell you something without you having
to check.

winsound.Beep blocks for the length of the tone, so it never runs on the Tk
thread -- a 120ms freeze every time a cooldown ends would be visible.
"""

from __future__ import annotations
import logging
import threading

try:
    import winsound
    HAS_WINSOUND = True
except ImportError:              # not Windows; the rest of the app is anyway
    HAS_WINSOUND = False

log = logging.getLogger("sound")

CUE_FREQ = 880      # A5: audible over game audio without being a klaxon
CUE_MS = 120


class Chime:
    """One beep at a time, on a thread of its own."""

    def __init__(self, enabled: bool = False):
        self.enabled = enabled
        self._playing = threading.Event()

    @property
    def available(self) -> bool:
        return HAS_WINSOUND

    def play(self) -> bool:
        """Queue a beep. Returns whether one was actually started."""
        if not self.enabled or not HAS_WINSOUND:
            return False
        # Five cooldowns can end together. One beep says the same thing as
        # five overlapping ones, and says it more clearly.
        if self._playing.is_set():
            return False
        self._playing.set()
        threading.Thread(target=self._beep, daemon=True, name="chime").start()
        return True

    def _beep(self) -> None:
        try:
            winsound.Beep(CUE_FREQ, CUE_MS)
        except Exception as e:
            log.debug("Beep failed: %s", e)
        finally:
            self._playing.clear()
