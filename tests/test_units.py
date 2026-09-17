import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hotkeys
import runes
import sync
from gamedata import SpellCooldowns


@pytest.mark.parametrize("raw,expected", [
    ("duo", "duo"),
    ("  duo  ", "duo"),
    ("my-room_1", "my-room_1"),
    ("a/b/c", "abc"),
    ("room#+", "room"),
    ("", ""),
    (None, ""),
    ("x" * 80, "x" * 32),
])


def test_sanitize_room(raw, expected):
    assert sync.sanitize_room(raw) == expected


def test_room_code_never_escapes_its_topic_segment():
    for hostile in ("../other", "a/#", "+", "a b"):
        assert "/" not in sync.sanitize_room(hostile)
        assert "#" not in sync.sanitize_room(hostile)
        assert "+" not in sync.sanitize_room(hostile)


def test_parse_modifier_accepts_known_combos():
    assert hotkeys.parse_modifier("alt") == (hotkeys.MOD_ALT, None)
    assert hotkeys.parse_modifier("CTRL") == (hotkeys.MOD_CONTROL, None)
    mods, err = hotkeys.parse_modifier("ctrl+alt")
    assert err is None
    assert mods == hotkeys.MOD_CONTROL | hotkeys.MOD_ALT


def test_shift_is_rejected_because_it_selects_the_second_spell():
    mods, err = hotkeys.parse_modifier("shift")
    assert mods == 0 and "reserved" in err
    mods, err = hotkeys.parse_modifier("ctrl+shift")
    assert mods == 0 and err


@pytest.mark.parametrize("spec", ["", "   ", "banana", "alt+banana"])


def test_parse_modifier_rejects_junk(spec):
    mods, err = hotkeys.parse_modifier(spec)
    assert mods == 0 and err


def test_describe():
    assert hotkeys.describe("alt") == "Alt+1-5"
    assert hotkeys.describe("ctrl+alt") == "Ctrl+Alt+1-5"
    assert hotkeys.describe("shift") == "off"
    assert hotkeys.describe("") == "off"


def test_configured_region_wins_without_touching_the_client():
    assert runes.resolve_platform("euw") == "euw1"
    assert runes.resolve_platform("EUW") == "euw1"
    assert runes.resolve_platform("euw1") == "euw1"
    assert runes.resolve_platform("na") == "na1"


def test_unknown_region_resolves_to_nothing():
    assert runes.resolve_platform("atlantis") is None


def test_every_platform_has_a_routing_host():
    for region, platform in runes.REGION_TO_PLATFORM.items():
        assert platform in runes.PLATFORM_TO_ROUTE, region


def test_cache_roundtrip(tmp_path):
    path = str(tmp_path / "cd.json")
    c = SpellCooldowns(cache_file=path, ttl=3600)
    assert c.load_cached() == {}
    assert c.is_stale()

    c._save({"summonerflash": 300, "summonerdot": 180})
    assert c.load_cached() == {"summonerflash": 300, "summonerdot": 180}
    assert not c.is_stale()


def test_corrupt_cache_is_ignored_rather_than_fatal(tmp_path):
    path = str(tmp_path / "cd.json")
    open(path, "w").write("{ not json")
    assert SpellCooldowns(cache_file=path).load_cached() == {}


def test_stale_cache_is_detected(tmp_path):
    path = str(tmp_path / "cd.json")
    c = SpellCooldowns(cache_file=path, ttl=0)
    c._save({"summonerflash": 300})
    time.sleep(0.01)
    assert c.is_stale()


def test_ddragon_cannot_overwrite_a_curated_cooldown(tmp_path):
    from config import Config
    path = str(tmp_path / "cd.json")
    c = SpellCooldowns(cache_file=path)
    before = dict(Config.SPELL_TIMERS)
    try:
        c._save({"summonerteleport": 300, "summonerflash": 999})
        c.apply_cached()
        assert Config.SPELL_TIMERS["summonerteleport"] == before["summonerteleport"]
        assert Config.SPELL_TIMERS["summonerflash"] == before["summonerflash"]
    finally:
        Config.SPELL_TIMERS.clear()
        Config.SPELL_TIMERS.update(before)


def test_ddragon_still_supplies_spells_we_do_not_know_about(tmp_path):
    from config import Config
    path = str(tmp_path / "cd.json")
    c = SpellCooldowns(cache_file=path)
    before = dict(Config.SPELL_TIMERS)
    try:
        c._save({"summonerbrandnewmode": 45})
        assert c.apply_cached() == 1
        assert Config.SPELL_TIMERS["summonerbrandnewmode"] == 45
    finally:
        Config.SPELL_TIMERS.clear()
        Config.SPELL_TIMERS.update(before)


def test_zero_cooldown_placeholders_are_ignored(tmp_path):
    from config import Config
    path = str(tmp_path / "cd.json")
    c = SpellCooldowns(cache_file=path)
    before = dict(Config.SPELL_TIMERS)
    try:
        c._save({"summoner_ultbooksmiteplaceholder": 0})
        assert c.apply_cached() == 0
        assert "summoner_ultbooksmiteplaceholder" not in Config.SPELL_TIMERS
    finally:
        Config.SPELL_TIMERS.clear()
        Config.SPELL_TIMERS.update(before)
