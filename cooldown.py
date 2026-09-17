"""The summoner-spell cooldown state machine, with no UI attached.

Kept free of tkinter so the haste arithmetic -- the fiddliest part of the app,
and the part that has to stay right when Cosmic Insight is flagged halfway
through a timer -- can be tested without a display.
"""

from __future__ import annotations
import math
import time
from typing import Callable, Optional

# What an unknown spell falls back to. Flash, because it is the one people
# actually track and a wrong 5:00 is less misleading than a wrong 0:15.
DEFAULT_COOLDOWN = 300

# Deadline arithmetic is `now + duration`, and reading it back is
# `deadline - now`. In floating point that round trip can land a hair *above*
# the duration -- (60.0006 + 254.0) - 60.0006 is 254.00000000000003 -- and
# ceil() turns 3e-14 into a whole extra second on the overlay. Whether it
# overshoots depends on the magnitude of the clock, so it shows up on a
# freshly booted machine and not on one that has been up for hours. Rounding
# to microseconds first costs nothing a cooldown timer can measure.
PRECISION = 6


def _seconds(value: float) -> float:
    return round(value, PRECISION)


def apply_haste(base_cd: int, haste: int) -> int:
    """Summoner spell haste, the way League computes it."""
    if haste <= 0:
        return int(base_cd)
    return int(base_cd * (100 / (100 + haste)))


class CooldownTimer:
    """One spell's countdown.

    ``base_cd`` is the cooldown before haste. It is what makes ``recalculate``
    possible, so a timer adopted from a duo partner carries it across the wire
    rather than arriving as a bare number of seconds.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self.is_active = False
        self.base_cd: Optional[int] = None
        self.haste = 0
        self._deadline = 0.0

    # -- queries ---------------------------------------------------------

    @property
    def remaining_exact(self) -> float:
        if not self.is_active:
            return 0.0
        return max(0.0, self._deadline - self._clock())

    @property
    def remaining(self) -> int:
        """Seconds left, rounded up, which is what the overlay draws."""
        return math.ceil(_seconds(self.remaining_exact))

    def duration(self) -> Optional[int]:
        """The full length of this cooldown, or None if it was not told."""
        if self.base_cd is None:
            return None
        return apply_haste(self.base_cd, self.haste)

    # -- transitions -----------------------------------------------------

    def start(self, base_cd: int, haste: int = 0) -> int:
        """Begin a fresh cooldown. Returns the hasted duration."""
        self.base_cd = int(base_cd)
        self.haste = max(0, int(haste))
        duration = apply_haste(self.base_cd, self.haste)
        self._deadline = self._clock() + duration
        self.is_active = True
        return duration

    def adopt(self, remaining: int, base_cd: Optional[int] = None,
              haste: int = 0) -> bool:
        """Take on a timer someone else started.

        ``remaining`` is already hasted; ``base_cd`` and ``haste`` are carried
        so a later Cosmic Insight flag can still correct it.
        """
        try:
            remaining = int(remaining)
        except (TypeError, ValueError):
            return False
        if remaining <= 0:
            return False
        self.base_cd = int(base_cd) if base_cd else None
        self.haste = max(0, int(haste or 0))
        self._deadline = self._clock() + remaining
        self.is_active = True
        return True

    def set_remaining(self, remaining: int, clamp: bool = False) -> bool:
        """Move the deadline. False means the timer ran out and was reset."""
        if not self.is_active:
            return False
        try:
            remaining = int(remaining)
        except (TypeError, ValueError):
            return True
        if clamp:
            cap = self.duration()
            if cap is not None:
                remaining = min(remaining, cap)
        if remaining <= 0:
            self.reset()
            return False
        self._deadline = self._clock() + remaining
        return True

    def nudge(self, delta: int) -> bool:
        """Correct a timer by a few seconds, never past its full length."""
        if not self.is_active:
            return False
        return self.set_remaining(self.remaining + delta, clamp=True)

    def recalculate(self, new_haste: int) -> bool:
        """Re-apply the cooldown at a different haste, keeping elapsed time.

        False means the spell is already back up at the new haste.
        """
        new_haste = max(0, int(new_haste))
        if not self.is_active or self.base_cd is None:
            return self.is_active
        if new_haste == self.haste:
            return True

        # Clamp: a partner with a shorter view of the cooldown can hand us a
        # remaining that exceeds our own duration, which would read as
        # negative elapsed time and hand back more than a full cooldown.
        elapsed = max(0.0, _seconds(apply_haste(self.base_cd, self.haste)
                                    - self.remaining_exact))
        self.haste = new_haste
        new_remaining = _seconds(apply_haste(self.base_cd, new_haste) - elapsed)
        if new_remaining <= 0:
            self.reset()
            return False
        self._deadline = self._clock() + new_remaining
        return True

    def reset(self) -> None:
        self.is_active = False
        self.base_cd = None
        self.haste = 0
        self._deadline = 0.0
