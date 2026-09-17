"""The spell widget: what it draws is Tk's problem, what it announces is ours.

These run against a real (hidden) Tk root but never build the full app, so they
stay fast and do not need a tray, a keyboard hook or the network.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

tk = pytest.importorskip("tkinter")

from config import Config
from widgets import SpellTimerWidget


class FakeApp:
    """Stands in for OverlayApp, recording everything it is told."""

    def __init__(self, haste=0):
        self.haste = haste
        self.sent = []
        self.called_out = []

    def get_haste(self, champ):
        return self.haste

    def broadcast_start(self, champ, spell, remaining, base_cd, haste):
        self.sent.append(("start", champ, spell, remaining, base_cd, haste))

    def broadcast_reset(self, champ, spell):
        self.sent.append(("reset", champ, spell))

    def broadcast_adjust(self, champ, spell, remaining):
        self.sent.append(("adjust", champ, spell, remaining))

    def call_out(self, champ, spell, remaining):
        self.called_out.append((champ, spell, remaining))

    @property
    def kinds(self):
        return [s[0] for s in self.sent]


@pytest.fixture(scope="module")
def root():
    try:
        r = tk.Tk()
    except tk.TclError as e:            # no display
        pytest.skip(f"Tk unavailable: {e}")
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture
def app():
    return FakeApp()


@pytest.fixture
def widget(root, app):
    w = SpellTimerWidget(root, "Ahri", "SummonerFlash", app)
    yield w
    w.destroy()


# -- arming --------------------------------------------------------------

def test_arming_starts_the_cooldown_and_tells_the_partner(widget, app):
    widget.arm()
    assert widget.is_active
    assert widget.remaining == 300
    assert app.sent == [("start", "Ahri", "SummonerFlash", 300, 300, 0)]


def test_the_broadcast_carries_the_base_cooldown_and_haste(root, app):
    app.haste = 18
    w = SpellTimerWidget(root, "Ahri", "SummonerFlash", app)
    try:
        w.arm()
        assert w.remaining == 254
        # The base cooldown has to travel, or the partner cannot recalculate.
        assert app.sent == [("start", "Ahri", "SummonerFlash", 254, 300, 18)]
    finally:
        w.destroy()


def test_arming_twice_does_nothing(widget, app):
    widget.arm()
    widget.arm()
    assert len(app.sent) == 1


def test_an_unknown_spell_falls_back_rather_than_failing(root, app):
    w = SpellTimerWidget(root, "Ahri", "SummonerMystery", app)
    try:
        assert w.base_cooldown() == 300
        w.arm()
        assert w.is_active
    finally:
        w.destroy()


# -- resetting -----------------------------------------------------------

def test_reset_announces_itself(widget, app):
    widget.arm()
    widget.reset()
    assert not widget.is_active
    assert app.kinds == ["start", "reset"]


def test_resetting_an_idle_timer_announces_nothing(widget, app):
    widget.reset()
    assert app.sent == []


def test_cancel_is_silent(widget, app):
    widget.arm()
    widget.cancel()
    assert not widget.is_active
    assert app.kinds == ["start"]


# -- a partner's timer ---------------------------------------------------

def test_adopting_is_silent(widget, app):
    assert widget.adopt(200, 300, 0) is True
    assert widget.remaining == 200
    assert app.sent == []


def test_an_adopted_timer_still_answers_to_cosmic_insight(widget):
    """The regression: duo timers used to arrive without a base cooldown."""
    widget.adopt(300, 300, 0)
    widget.recalculate(18)
    assert widget.is_active
    assert widget.remaining == 254


def test_a_remote_adjustment_is_not_echoed_back(widget, app):
    widget.arm()
    app.sent.clear()
    widget.set_remaining(120, broadcast=False)
    assert widget.remaining == 120
    assert app.sent == []


# -- nudging -------------------------------------------------------------

def test_nudging_announces_the_new_value(widget, app):
    widget.arm()
    widget.set_remaining(100, broadcast=False)
    app.sent.clear()
    widget.nudge(5)
    assert widget.remaining == 105
    assert app.sent == [("adjust", "Ahri", "SummonerFlash", 105)]


def test_nudging_a_timer_to_zero_tells_the_partner_it_is_up(widget, app):
    """Otherwise a partner keeps counting down a spell we have cleared."""
    widget.arm()
    widget.set_remaining(3, broadcast=False)
    app.sent.clear()
    widget.nudge(-5)
    assert not widget.is_active
    assert app.sent == [("reset", "Ahri", "SummonerFlash")]


def test_nudging_cannot_exceed_the_full_cooldown(widget):
    widget.arm()
    widget.nudge(60)
    assert widget.remaining == 300


# -- haste corrections ---------------------------------------------------

def test_a_haste_correction_is_not_broadcast(widget, app):
    """Both sides apply the same Cosmic Insight flag to their own copy."""
    widget.arm()
    app.sent.clear()
    widget.recalculate(18)
    assert app.sent == []


def test_a_correction_that_expires_a_spell_is_also_silent(widget, app):
    widget.arm()
    widget.set_remaining(250, broadcast=False)   # 50 elapsed of 300
    app.sent.clear()
    widget.recalculate(500)                      # far shorter cooldown
    assert not widget.is_active
    assert app.sent == []


# -- the Tk lifecycle ----------------------------------------------------

def test_a_running_timer_schedules_a_tick(widget):
    widget.arm()
    assert widget.timer_job is not None


def test_destroying_a_running_widget_cancels_its_tick(root, app):
    """A pending `after` outliving its canvas is a background error."""
    w = SpellTimerWidget(root, "Ahri", "SummonerFlash", app)
    w.arm()
    assert w.timer_job is not None
    w.destroy()
    assert w.timer_job is None


@pytest.mark.parametrize("sequence", [
    "<Button-1>", "<Button-2>", "<Button-3>", "<MouseWheel>",
])
def test_every_mouse_action_is_bound(widget, sequence):
    assert widget.bind(sequence), f"{sequence} is not wired up"


def test_middle_click_asks_for_a_call_out_with_the_time_left(widget, app):
    # Called directly: an unmapped widget does not receive generated events.
    widget.arm()
    widget.set_remaining(47, broadcast=False)
    widget._on_middle_click(None)
    assert app.called_out == [("Ahri", "SummonerFlash", 47)]


def test_a_call_out_for_a_spell_that_is_up_reports_zero(widget, app):
    widget._on_middle_click(None)
    assert app.called_out == [("Ahri", "SummonerFlash", 0)]


# -- scaling -------------------------------------------------------------

def test_the_widget_is_built_at_the_current_scale(root, app):
    Config.UI_SCALE = 2.0
    try:
        w = SpellTimerWidget(root, "Ahri", "SummonerFlash", app)
        try:
            assert w.winfo_reqwidth() == Config.ICON_SIZE * 2
        finally:
            w.destroy()
    finally:
        Config.UI_SCALE = 1.0
