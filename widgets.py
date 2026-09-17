"""The overlay's two canvas widgets: an enemy portrait and a spell timer.

These draw a CooldownTimer and translate clicks; the arithmetic lives in
cooldown.py. Everything the app does to a widget from outside goes through the
public methods here -- arm, adopt, reset, nudge, recalculate, cancel -- so the
broadcast rules stay in one place.
"""

from __future__ import annotations
import logging
import tkinter as tk
from typing import Optional, Tuple

from assets import AssetManager
from config import Config, base_spell_name
from cooldown import DEFAULT_COOLDOWN, CooldownTimer

log = logging.getLogger("widgets")

# Eight offsets around the glyph: cheaper than a real stroke and readable on
# top of any splash art.
_OUTLINE_OFFSETS = ((-1, -1), (0, -1), (1, -1), (-1, 0),
                    (1, 0), (-1, 1), (0, 1), (1, 1))


class ChampionIcon(tk.Canvas):
    def __init__(self, parent, champ_name: str, app_ref, image):
        size = Config.icon_size()
        super().__init__(parent, width=size, height=size,
                         bg=Config.COLOR_BG, highlightthickness=0)
        self.champ_name = champ_name
        self.app_ref = app_ref
        self.icon_img = image
        self.create_image(0, 0, image=self.icon_img, anchor="nw")

        badge = Config.scaled(16)
        self.badge_bg = self.create_oval(size - badge, size - badge, size - 1, size - 1,
                                         fill=Config.COLOR_CI_BADGE,
                                         outline=Config.COLOR_TEXT_OUTLINE,
                                         state="hidden")
        self.badge_txt = self.create_text(size - badge // 2, size - badge // 2,
                                          text="CI",
                                          fill=Config.COLOR_TEXT_ACTIVE,
                                          font=Config.font(-6),
                                          state="hidden")

        self.bind("<Control-Button-1>", self._toggle)
        self.bind("<Control-Button-3>", self._toggle)

    def _toggle(self, event):
        self.app_ref.set_cosmic_insight(self.champ_name)
        return "break"

    def set_flag(self, on: bool):
        state = "normal" if on else "hidden"
        self.itemconfig(self.badge_bg, state=state)
        self.itemconfig(self.badge_txt, state=state)


class SpellTimerWidget(tk.Canvas):
    def __init__(self, parent, champ_name: str, spell_name: str, app_ref):
        size = Config.icon_size()
        super().__init__(parent, width=size, height=size,
                         bg=Config.COLOR_BG, highlightthickness=0)
        self.champ_name = champ_name
        self.spell_name = spell_name
        self.app_ref = app_ref
        self.timer = CooldownTimer()
        self.timer_job = None

        self.icon_img = AssetManager.load_icon(
            "spells", base_spell_name(spell_name), (size, size))
        self.create_image(0, 0, image=self.icon_img, anchor="nw")

        self.dim_img = AssetManager.create_dim_layer((size, size))
        self.dim_id = self.create_image(0, 0, image=self.dim_img, anchor="nw",
                                        state="hidden")
        self.warn_ring = self.create_rectangle(
            1, 1, size - 1, size - 1, outline=Config.COLOR_TEXT_WARN,
            width=max(2, Config.scaled(2)), state="hidden")

        self.bind("<Button-1>", self._on_left_click)
        self.bind("<Button-3>", self._on_right_click)
        self.bind("<Button-2>", self._on_middle_click)
        self.bind("<MouseWheel>", self._on_scroll)

    # -- state -----------------------------------------------------------

    @property
    def is_active(self) -> bool:
        return self.timer.is_active

    @property
    def remaining(self) -> int:
        return self.timer.remaining

    def base_cooldown(self) -> int:
        key = base_spell_name(self.spell_name).lower()
        return Config.SPELL_TIMERS.get(key, DEFAULT_COOLDOWN)

    # -- events ----------------------------------------------------------

    def _on_left_click(self, event):
        self.arm()

    def _on_right_click(self, event):
        if self.is_active:
            self.reset()

    def _on_middle_click(self, event):
        self.app_ref.call_out(self.champ_name, self.spell_name,
                              self.remaining if self.is_active else 0)

    def _on_scroll(self, event):
        if not self.is_active:
            return "break"
        self.nudge(Config.NUDGE_STEP if event.delta > 0 else -Config.NUDGE_STEP)
        return "break"

    # -- public API ------------------------------------------------------

    def arm(self) -> None:
        """Start this spell's cooldown and tell the duo partner."""
        if self.is_active:
            return
        base_cd = self.base_cooldown()
        haste = self.app_ref.get_haste(self.champ_name)
        duration = self.timer.start(base_cd, haste)
        if haste:
            log.info("%s (%s): base %ds -> %d haste -> %ds",
                     self.champ_name, self.spell_name, base_cd, haste, duration)
        self._begin_drawing()
        self.app_ref.broadcast_start(self.champ_name, self.spell_name,
                                     duration, base_cd, haste)

    def adopt(self, remaining: int, base_cd: Optional[int] = None,
              haste: int = 0) -> bool:
        """Take a timer from a duo partner. Never broadcasts."""
        if not self.timer.adopt(remaining, base_cd, haste):
            return False
        self._begin_drawing()
        return True

    def reset(self, broadcast: bool = True) -> None:
        was_active = self.is_active
        self.timer.reset()
        self._stop_drawing()
        if broadcast and was_active:
            self.app_ref.broadcast_reset(self.champ_name, self.spell_name)

    def cancel(self) -> None:
        """Silently stop ticking. For teardown, where nobody wants to know."""
        self.timer.reset()
        self._stop_drawing()

    def _expired(self, broadcast: bool, cue: bool = False) -> None:
        """Finish up after the timer has already reset itself.

        reset() cannot do this: by the time it is called the timer is idle, so
        it would decide there was nothing to announce and a partner would be
        left holding a cooldown we have cleared.

        ``cue`` marks the spell genuinely coming back up, as opposed to being
        cleared by hand or corrected away by a haste change.
        """
        self._stop_drawing()
        if broadcast:
            self.app_ref.broadcast_reset(self.champ_name, self.spell_name)
        if cue:
            self.app_ref.spell_ready(self.champ_name, self.spell_name)

    def set_remaining(self, remaining: int, broadcast: bool = False) -> None:
        if not self.is_active:
            return
        if not self.timer.set_remaining(remaining):
            self._expired(broadcast)
            return
        self._restart_ticking()
        if broadcast:
            self.app_ref.broadcast_adjust(self.champ_name, self.spell_name,
                                          self.timer.remaining)

    def nudge(self, delta: int, broadcast: bool = True) -> None:
        if not self.is_active:
            return
        if not self.timer.nudge(delta):
            self._expired(broadcast)
            return
        self._restart_ticking()
        if broadcast:
            self.app_ref.broadcast_adjust(self.champ_name, self.spell_name,
                                          self.timer.remaining)

    def recalculate(self, new_haste: int) -> None:
        """Re-apply the cooldown at a new haste, keeping elapsed time."""
        if not self.is_active:
            return
        if not self.timer.recalculate(new_haste):
            # Haste corrections are local bookkeeping on a shared fact; the
            # partner is applying the same one to their own copy.
            self._expired(broadcast=False)
            return
        self._restart_ticking()

    # -- drawing ---------------------------------------------------------

    def _begin_drawing(self) -> None:
        self.itemconfig(self.dim_id, state="normal")
        self._restart_ticking()

    def _stop_drawing(self) -> None:
        self._cancel_job()
        self.delete("timer_text")
        self.itemconfig(self.dim_id, state="hidden")
        self.itemconfig(self.warn_ring, state="hidden")

    def _cancel_job(self) -> None:
        if self.timer_job:
            try:
                self.after_cancel(self.timer_job)
            except Exception:
                pass
            self.timer_job = None

    def _restart_ticking(self) -> None:
        self._cancel_job()
        self._tick()

    def _tick(self) -> None:
        self.timer_job = None
        remaining = self.timer.remaining
        if not self.timer.is_active or remaining <= 0:
            self.timer.reset()
            self._expired(broadcast=False, cue=True)
            return

        warn = remaining <= Config.WARN_THRESHOLD
        self.itemconfig(self.warn_ring, state="normal" if warn else "hidden")

        m, s = divmod(remaining, 60)
        text = f"{m}:{s:02}" if remaining >= 60 else str(remaining)
        self._draw_outlined_text(
            text, Config.COLOR_TEXT_WARN if warn else Config.COLOR_TEXT_ACTIVE)

        # Land the next tick just as the displayed second changes, rather
        # than a fixed second later, so the digits never skip.
        delay = (self.timer.remaining_exact - (remaining - 1)) * 1000
        self.timer_job = self.after(int(min(1000, max(20, delay))), self._tick)

    def _adaptive_font(self, text: str) -> Tuple[str, int, str]:
        length = len(text)
        if length <= 2:
            return Config.font(+2)
        if length == 3:
            return Config.font(+1)
        if length == 4:
            return Config.font(-1)
        return Config.font(-3)

    def _draw_outlined_text(self, text: str,
                            fill: str = Config.COLOR_TEXT_ACTIVE) -> None:
        self.delete("timer_text")
        size = Config.icon_size()
        cx, cy = size // 2, size // 2
        font_spec = self._adaptive_font(text)
        for ox, oy in _OUTLINE_OFFSETS:
            self.create_text(cx + ox, cy + oy, text=text, font=font_spec,
                             fill=Config.COLOR_TEXT_OUTLINE, tags="timer_text",
                             anchor="center")
        self.create_text(cx, cy, text=text, font=font_spec, fill=fill,
                         tags="timer_text", anchor="center")

    def destroy(self):
        # A pending `after` outliving its canvas fires into a deleted Tcl
        # command, which surfaces as a background error with no stack.
        self._cancel_job()
        super().destroy()
