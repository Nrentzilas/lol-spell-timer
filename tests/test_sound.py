"""The audio cue. winsound is faked; nothing here makes a noise."""

import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sound
from sound import CUE_FREQ, CUE_MS, Chime


class FakeWinsound:
    """Records beeps, and can hold one open to simulate a tone playing."""

    def __init__(self, hold: threading.Event = None):
        self.calls = []
        self.rang = threading.Event()
        self.started = threading.Event()
        self.hold = hold
        self.raises = False

    def Beep(self, freq, ms):
        self.calls.append((freq, ms))
        self.started.set()
        if self.raises:
            raise RuntimeError("no audio device")
        if self.hold:
            self.hold.wait(2.0)
        self.rang.set()


@pytest.fixture
def fake(monkeypatch):
    def install(hold=None):
        fw = FakeWinsound(hold)
        monkeypatch.setattr(sound, "winsound", fw)
        monkeypatch.setattr(sound, "HAS_WINSOUND", True)
        return fw
    return install


def test_an_enabled_chime_beeps_once(fake):
    fw = fake()
    chime = Chime(enabled=True)
    assert chime.play() is True
    assert fw.rang.wait(2.0), "the beep never happened"
    assert fw.calls == [(CUE_FREQ, CUE_MS)]


def test_a_disabled_chime_stays_quiet(fake):
    fw = fake()
    assert Chime(enabled=False).play() is False
    assert fw.calls == []


def test_toggling_it_on_takes_effect_without_a_restart(fake):
    fw = fake()
    chime = Chime(enabled=False)
    chime.play()
    chime.enabled = True
    assert chime.play() is True
    assert fw.rang.wait(2.0)


def test_five_cooldowns_ending_together_make_one_beep(fake):
    """One beep says the same thing as five overlapping ones."""
    hold = threading.Event()
    fw = fake(hold)
    chime = Chime(enabled=True)
    assert chime.play() is True
    assert fw.started.wait(2.0)
    assert [chime.play() for _ in range(4)] == [False, False, False, False]
    hold.set()
    assert fw.rang.wait(2.0)
    assert fw.calls == [(CUE_FREQ, CUE_MS)]


def test_it_can_beep_again_once_the_first_one_finishes(fake):
    fw = fake()
    chime = Chime(enabled=True)
    chime.play()
    assert fw.rang.wait(2.0)
    for _ in range(50):          # the flag clears on the worker thread
        if chime.play():
            break
        threading.Event().wait(0.02)
    assert len(fw.calls) == 2


def test_a_machine_with_no_audio_does_not_take_the_app_down(fake):
    fw = fake()
    fw.raises = True
    chime = Chime(enabled=True)
    assert chime.play() is True
    assert fw.started.wait(2.0)
    for _ in range(50):          # the flag must still be cleared
        if not chime._playing.is_set():
            break
        threading.Event().wait(0.02)
    assert chime._playing.is_set() is False


def test_without_winsound_it_is_simply_unavailable(monkeypatch):
    monkeypatch.setattr(sound, "HAS_WINSOUND", False)
    chime = Chime(enabled=True)
    assert chime.available is False
    assert chime.play() is False
