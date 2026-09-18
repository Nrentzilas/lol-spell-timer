"""Settings persistence: defaults, validation, migration, and safe writes."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import settings as settings_module
from config import Config
from settings import Settings


@pytest.fixture(autouse=True)
def fixed_screen(monkeypatch):
    """A predictable desktop, so clamping does not depend on the test machine."""
    monkeypatch.setattr(settings_module.Win32Utils, "virtual_screen",
                        staticmethod(lambda: (0, 0, 1920, 1080)))


@pytest.fixture
def path(tmp_path):
    return str(tmp_path / "config.json")


def write(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def test_missing_file_gives_defaults_on_screen(path):
    s = Settings.load(path)
    assert 0 <= s.x <= 1920 and 0 <= s.y <= 1080
    assert s.room == ""
    assert s.hotkey_mod == Config.DEFAULT_HOTKEY_MOD


def test_roundtrip(path):
    s = Settings.load(path)
    s.x, s.y = 400, 300
    s.room = "duo"
    s.riot_api_key = "RGAPI-test"
    s.ui_scale = 1.5
    s.send_callouts = True
    s.sound_cue = True
    assert s.save() is True

    again = Settings.load(path)
    assert (again.x, again.y) == (400, 300)
    assert again.room == "duo"
    assert again.riot_api_key == "RGAPI-test"
    assert again.ui_scale == 1.5
    assert again.send_callouts is True
    assert again.sound_cue is True


def test_a_save_leaves_no_temporary_file_behind(path):
    s = Settings.load(path)
    s.save()
    assert os.path.exists(path)
    assert not os.path.exists(path + ".tmp")


def test_the_file_is_replaced_whole_not_truncated_in_place(path, monkeypatch):
    """A failed write must leave the previous settings readable."""
    s = Settings.load(path)
    s.room = "good"
    s.save()

    broken = Settings.load(path)
    broken.room = "bad"
    monkeypatch.setattr(settings_module.json, "dump",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    assert broken.save() is False

    assert Settings.load(path).room == "good"
    assert not os.path.exists(path + ".tmp")


def test_corrupt_file_falls_back_to_defaults(path):
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not json at all")
    s = Settings.load(path)
    assert s.room == ""
    assert s.hotkey_mod == Config.DEFAULT_HOTKEY_MOD


def test_off_screen_position_is_pulled_back(path):
    write(path, {"x": 99999, "y": -400})
    s = Settings.load(path)
    assert 0 <= s.x <= 1920
    assert 0 <= s.y <= 1080


def test_room_is_sanitized_on_the_way_in(path):
    write(path, {"room": "my room/#+"})
    assert Settings.load(path).room == "myroom"


@pytest.mark.parametrize("raw,expected", [
    (1.0, 1.0), (1.5, 1.5), (99, Config.UI_SCALE_MAX),
    (0.1, Config.UI_SCALE_MIN), ("junk", 1.0), (None, 1.0),
])
def test_ui_scale_is_clamped(path, raw, expected):
    write(path, {"ui_scale": raw})
    assert Settings.load(path).ui_scale == expected


# -- the broker ----------------------------------------------------------

def test_the_default_broker_is_used_in_the_clear(path):
    """The default is used in the clear unless the port asks for TLS."""
    s = Settings.load(path)
    assert s.broker == Config.SYNC_BROKER
    assert s.broker_port == Config.SYNC_PLAIN_PORT
    assert s.sync_tls is False


def test_the_retired_default_broker_moves_to_the_new_one(path):
    """Every old config.json saved broker.hivemq.com; it now drops connections."""
    write(path, {"broker": "broker.hivemq.com", "broker_port": 1883, "room": "duo"})
    s = Settings.load(path)
    assert s.broker == Config.SYNC_BROKER
    assert s.broker_port == 1883
    assert s.room == "duo"


def test_a_broker_the_user_chose_is_kept(path):
    write(path, {"broker": "mqtt.example.org"})
    assert Settings.load(path).broker == "mqtt.example.org"


def test_pointing_at_8883_turns_on_tls(path):
    write(path, {"broker": "mqtt.example.org", "broker_port": 8883})
    s = Settings.load(path)
    assert s.broker_port == 8883
    assert s.sync_tls is True


def test_any_other_port_is_taken_as_is(path):
    write(path, {"broker": "mqtt.example.org", "broker_port": 1884})
    s = Settings.load(path)
    assert s.broker_port == 1884
    assert s.sync_tls is False


@pytest.mark.parametrize("bad", ["eight-eight-eight-three", None, 0, -1, 99999])
def test_a_nonsense_port_falls_back_to_the_default(path, bad):
    write(path, {"broker_port": bad})
    assert Settings.load(path).broker_port == Config.SYNC_PORT


def test_describe_mentions_the_things_people_report(path):
    s = Settings.load(path)
    s.room = "duo"
    text = s.describe()
    assert "duo" in text and "Scale" in text and "Hotkeys" in text
    assert s.riot_api_key not in text or "none" in text
