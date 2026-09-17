"""Tray menu ticks, and the ordering that keeps them honest.

pystray reads every item's checked state the instant a click handler returns
and bakes it into the native menu. Anything a tick reflects therefore has to
be true by then, which is what these tests pin down.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tray as tray_module
from config import Config
from main import apply_tray_toggle
from settings import Settings


class FakeApp:
    """The part of OverlayApp the menu reads, without Tk or a tray thread."""

    def __init__(self, path):
        self.settings = Settings.load(path)
        self.demo_mode = False
        self.game_active = False
        self.queued = []

    # the properties the menu's predicates go through
    @property
    def send_callouts(self):
        return self.settings.send_callouts

    @property
    def type_on_paste(self):
        return self.settings.type_on_paste

    @property
    def sound_cue(self):
        return self.settings.sound_cue

    @property
    def ui_scale(self):
        return self.settings.ui_scale

    def request(self, name, value=None):
        """What OverlayApp.request does: flip now, queue the rest."""
        apply_tray_toggle(self, name, value)
        self.queued.append((name, value))


@pytest.fixture
def app(tmp_path):
    return FakeApp(str(tmp_path / "config.json"))


TOGGLES = [
    (tray_module.TOGGLE_SEND, "send_callouts"),
    (tray_module.TOGGLE_PASTE, "type_on_paste"),
    (tray_module.TOGGLE_SOUND, "sound_cue"),
]


# -- the flip happens immediately ---------------------------------------

@pytest.mark.parametrize("action,attr", TOGGLES)
def test_a_toggle_is_true_before_the_click_handler_returns(app, action, attr):
    """The regression: this used to wait on the Tk thread, so the menu
    rebuilt with the old value and the tick ran a click behind."""
    assert getattr(app.settings, attr) is False
    apply_tray_toggle(app, action)
    assert getattr(app.settings, attr) is True


@pytest.mark.parametrize("action,attr", TOGGLES)
def test_toggling_twice_comes_back_to_where_it_started(app, action, attr):
    apply_tray_toggle(app, action)
    apply_tray_toggle(app, action)
    assert getattr(app.settings, attr) is False


@pytest.mark.parametrize("action,attr", TOGGLES)
def test_the_work_is_still_queued_for_the_tk_thread(app, action, attr):
    app.request(action)
    assert app.queued == [(action, None)]


def test_an_unknown_action_changes_nothing(app):
    before = app.settings.as_dict()
    assert apply_tray_toggle(app, "not_a_real_action") is False
    assert app.settings.as_dict() == before


# -- demo rows ----------------------------------------------------------

def test_demo_rows_flip_when_no_match_is_running(app):
    assert apply_tray_toggle(app, tray_module.TOGGLE_DEMO) is True
    assert app.demo_mode is True
    apply_tray_toggle(app, tray_module.TOGGLE_DEMO)
    assert app.demo_mode is False


def test_demo_rows_refused_during_a_match_do_not_show_a_tick(app):
    """A tick claiming demo rows are on, during a match that ignores them,
    is worse than no tick at all."""
    app.game_active = True
    apply_tray_toggle(app, tray_module.TOGGLE_DEMO)
    assert app.demo_mode is False


# -- the size radio -----------------------------------------------------

def test_picking_a_size_registers_immediately(app):
    apply_tray_toggle(app, tray_module.SCALE, 1.5)
    assert app.settings.ui_scale == 1.5


def test_a_nonsense_size_is_clamped_not_stored_raw(app):
    apply_tray_toggle(app, tray_module.SCALE, 99)
    assert app.settings.ui_scale == Config.UI_SCALE_MAX


# -- through the real menu ----------------------------------------------

def find_item(menu, text_fragment):
    """The descriptor pystray would read, found by its label."""
    for item in menu:
        if item.submenu is not None:
            found = find_item(item.submenu, text_fragment)
            if found is not None:
                return found
        if text_fragment.lower() in str(item.text).lower():
            return item
    return None


@pytest.fixture(scope="module")
def _real_tray(tmp_path_factory):
    """One Tray for the module: pystray registers a window class per Icon and
    a second one in the same process fails with "Class already exists"."""
    pytest.importorskip("pystray")
    app = FakeApp(str(tmp_path_factory.mktemp("tray") / "config.json"))
    try:
        return tray_module.Tray(app.request, app), app
    except OSError as e:                 # no window station, or already taken
        pytest.skip(f"pystray Icon unavailable: {e}")


@pytest.fixture
def tray(_real_tray):
    """The shared Tray, with its state wound back to the defaults."""
    tray, app = _real_tray
    app.settings.send_callouts = False
    app.settings.type_on_paste = False
    app.settings.sound_cue = False
    app.settings.ui_scale = 1.0
    app.demo_mode = False
    app.game_active = False
    return tray


@pytest.mark.parametrize("label", [
    "Send call-outs", "Type clipboard", "Beep when a spell", "demo rows",
])
def test_clicking_a_menu_item_ticks_it_on_the_same_click(tray, label):
    """Drives the real descriptors the way pystray's own wrapper does:
    invoke the callback, then read `checked` -- which is exactly when
    pystray rebuilds the native menu."""
    item = find_item(tray.icon.menu, label)
    assert item is not None, f"no menu item matching {label!r}"
    assert item.checked is False

    item(tray.icon)                      # the click
    assert item.checked is True, "the tick is a click behind"

    item(tray.icon)
    assert item.checked is False


def test_the_size_radio_follows_the_click(tray):
    one_x = find_item(tray.icon.menu, "1x")
    one_and_a_half = find_item(tray.icon.menu, "1.5x")
    assert one_x.checked is True and one_and_a_half.checked is False

    one_and_a_half(tray.icon)
    assert one_and_a_half.checked is True
    assert one_x.checked is False


@pytest.mark.parametrize("label", [
    "Send call-outs", "Type clipboard", "Beep when a spell",
])
def test_every_toggle_starts_unticked(tray, label):
    assert find_item(tray.icon.menu, label).checked is False
