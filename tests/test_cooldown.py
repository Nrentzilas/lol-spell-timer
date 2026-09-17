"""The cooldown state machine, driven by a clock the test controls."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cooldown import CooldownTimer, apply_haste


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def timer(clock):
    return CooldownTimer(clock=clock)


# -- haste arithmetic ----------------------------------------------------

@pytest.mark.parametrize("base,haste,expected", [
    (300, 0, 300),
    (300, 10, 272),      # Lucidity boots
    (300, 18, 254),      # Cosmic Insight
    (300, 28, 234),      # both
    (180, 0, 180),
    (15, 20, 12),
])
def test_apply_haste(base, haste, expected):
    assert apply_haste(base, haste) == expected


def test_negative_haste_is_ignored():
    assert apply_haste(300, -50) == 300


# -- the basics ----------------------------------------------------------

def test_a_fresh_timer_is_idle(timer):
    assert not timer.is_active
    assert timer.remaining == 0
    assert timer.duration() is None


def test_start_returns_the_hasted_duration(timer):
    assert timer.start(300, 18) == 254
    assert timer.is_active
    assert timer.remaining == 254


def test_remaining_counts_down(timer, clock):
    timer.start(300)
    clock.advance(45)
    assert timer.remaining == 255


def test_remaining_rounds_up_so_the_display_never_shows_a_second_early(timer, clock):
    timer.start(300)
    clock.advance(0.5)
    assert timer.remaining == 300


def test_remaining_never_goes_negative(timer, clock):
    timer.start(300)
    clock.advance(500)
    assert timer.remaining == 0


def test_reset_clears_everything(timer):
    timer.start(300, 10)
    timer.reset()
    assert not timer.is_active
    assert timer.remaining == 0
    assert timer.base_cd is None
    assert timer.haste == 0


# -- recalculate ---------------------------------------------------------

def test_flagging_haste_mid_timer_keeps_the_elapsed_time(timer, clock):
    timer.start(300, 0)
    clock.advance(100)
    assert timer.recalculate(18) is True
    # 254 total at 18 haste, 100 of it already gone.
    assert timer.remaining == 154


def test_removing_haste_mid_timer_extends_it(timer, clock):
    timer.start(300, 18)     # 254
    clock.advance(54)
    assert timer.recalculate(0) is True
    assert timer.remaining == 246


def test_recalculate_expires_a_spell_that_is_already_back(timer, clock):
    timer.start(300, 0)
    clock.advance(280)
    assert timer.recalculate(18) is False    # 254 < 280 elapsed
    assert not timer.is_active


def test_recalculate_to_the_same_haste_changes_nothing(timer, clock):
    timer.start(300, 10)
    clock.advance(30)
    before = timer.remaining
    assert timer.recalculate(10) is True
    assert timer.remaining == before


def test_recalculate_is_a_no_op_on_an_idle_timer(timer):
    assert timer.recalculate(18) is False
    assert not timer.is_active


def test_a_partner_with_more_time_left_cannot_extend_past_a_full_cooldown(timer):
    # Adopted at more than our own hasted duration: elapsed would be negative
    # and would otherwise hand back more than the spell's full cooldown.
    timer.adopt(290, base_cd=300, haste=18)
    assert timer.recalculate(0) is True
    assert timer.remaining <= 300


# -- adopting a partner's timer -----------------------------------------

def test_an_adopted_timer_can_still_be_haste_corrected(timer, clock):
    """The regression test: a duo timer used to lose its base cooldown."""
    timer.adopt(200, base_cd=300, haste=0)
    assert timer.base_cd == 300
    clock.advance(10)
    assert timer.recalculate(18) is True
    # 100 elapsed of the original 300, so 254 - 110 left at the new haste.
    assert timer.remaining == 144


def test_an_adopted_timer_without_a_base_cooldown_still_runs(timer):
    assert timer.adopt(120) is True
    assert timer.is_active
    assert timer.remaining == 120
    assert timer.duration() is None
    # Nothing to recalculate from, but it must not die or throw.
    assert timer.recalculate(18) is True
    assert timer.remaining == 120


@pytest.mark.parametrize("bad", [0, -5, None, "", "abc"])
def test_adopting_nothing_useful_is_refused(timer, bad):
    assert timer.adopt(bad) is False
    assert not timer.is_active


# -- nudging -------------------------------------------------------------

def test_nudge_adds_seconds(timer, clock):
    timer.start(300)
    clock.advance(100)
    assert timer.nudge(5) is True
    assert timer.remaining == 205


def test_nudge_never_exceeds_the_full_cooldown(timer):
    timer.start(300)
    timer.nudge(60)
    assert timer.remaining == 300


def test_nudge_respects_haste_when_clamping(timer):
    timer.start(300, 18)
    timer.nudge(60)
    assert timer.remaining == 254


def test_nudging_below_zero_resets(timer, clock):
    timer.start(300)
    clock.advance(297)
    assert timer.nudge(-5) is False
    assert not timer.is_active


def test_nudge_does_nothing_when_idle(timer):
    assert timer.nudge(5) is False


# -- explicit adjustments ------------------------------------------------

def test_set_remaining_moves_the_deadline(timer):
    timer.start(300)
    assert timer.set_remaining(42) is True
    assert timer.remaining == 42


def test_set_remaining_is_unclamped_by_default(timer):
    """A partner's adjustment is taken at face value."""
    timer.start(300, 18)
    timer.set_remaining(290)
    assert timer.remaining == 290


def test_set_remaining_to_zero_resets(timer):
    timer.start(300)
    assert timer.set_remaining(0) is False
    assert not timer.is_active


def test_set_remaining_ignores_junk(timer):
    timer.start(300)
    assert timer.set_remaining("later") is True
    assert timer.remaining == 300
