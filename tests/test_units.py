import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import hotkeys
import runes
import sync
import chat
import gamedata
import gamewindow
import clipboardtyper
from config import Config
from gamedata import SpellCooldowns, GameDataManager


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


@pytest.mark.parametrize("seconds,expected", [
    (0, "0:00"),
    (5, "0:05"),
    (65, "1:05"),
    (872, "14:32"),
    (3600, "60:00"),
    (-10, "0:00"),
    (14.9, "0:14"),
])


def test_format_clock(seconds, expected):
    assert gamedata.format_clock(seconds) == expected


@pytest.mark.parametrize("spell,expected", [
    ("SummonerFlash", "Flash"),
    ("SummonerFlash2", "Flash"),
    ("SummonerDot", "Ignite"),
    ("SummonerBoost", "Cleanse"),
    ("SummonerHaste", "Ghost"),
    ("SummonerTeleport", "TP"),
    ("SummonerUnknownThing", "UnknownThing"),
    ("", "Spell"),
])


def test_spell_label(spell, expected):
    assert chat.spell_label(spell) == expected


def test_message_uses_game_clock():
    assert chat.format_message("MissFortune", "SummonerFlash", 165, 720) == \
        "MissFortune Flash up at 14:45"


def test_message_without_game_clock_falls_back_to_relative():
    assert chat.format_message("Ahri", "SummonerFlash", 165, None) == \
        "Ahri Flash back in 2:45"


def test_sub_minute_calls_are_a_countdown_not_a_clock_time():
    assert chat.format_message("MissFortune", "SummonerFlash", 47, 720) ==         "MissFortune Flash up in 47s"


@pytest.mark.parametrize("remaining,expected", [
    (1, "Ahri Flash up in 1s"),
    (59, "Ahri Flash up in 59s"),
    (60, "Ahri Flash up at 13:00"),
])
def test_the_switch_happens_at_a_minute(remaining, expected):
    assert chat.format_message("Ahri", "SummonerFlash", remaining, 720) == expected


def test_a_countdown_does_not_need_the_game_clock():
    assert chat.format_message("Ahri", "SummonerFlash", 47, None) ==         "Ahri Flash up in 47s"


def test_message_when_spell_is_up():
    assert chat.format_message("Ahri", "SummonerFlash", 0, 720) == "Ahri Flash is up"


def test_game_time_parsing():
    assert GameDataManager.game_time({"gameData": {"gameTime": 872.4}}) == 872.4
    assert GameDataManager.game_time({"gameData": {}}) is None
    assert GameDataManager.game_time({}) is None
    assert GameDataManager.game_time(None) is None
    assert GameDataManager.game_time({"gameData": {"gameTime": "x"}}) is None


def test_gamewindow_mode_is_a_known_value():
    assert gamewindow.mode() in (None, gamewindow.FULLSCREEN,
                                 gamewindow.BORDERLESS, gamewindow.WINDOWED)


def test_gamewindow_handles_league_being_absent():
    assert isinstance(gamewindow.find(), int)
    assert isinstance(gamewindow.is_foreground(0), bool)
    assert gamewindow.is_foreground(0) is False
    assert gamewindow.rect(0) is None


def test_typer_starts_disabled_and_types_nothing(monkeypatch):
    t = clipboardtyper.ClipboardTyper()
    assert t.enabled is False
    sent = []
    monkeypatch.setattr(clipboardtyper, "_SendInput", lambda *a: sent.append(a) or 2)
    assert clipboardtyper.type_text("") == 0
    assert sent == []


def test_type_text_counts_characters(monkeypatch):
    calls = []
    monkeypatch.setattr(clipboardtyper, "_SendInput",
                        lambda n, arr, size: calls.append(n) or 2)
    assert clipboardtyper.type_text("abc") == 3
    assert calls == [2, 2, 2]          # one SendInput of 2 events per character


@pytest.mark.parametrize("raw,expected", [
    ("MissFortune Flash up at 14:32", "MissFortune Flash up at 14:32"),
    ("  padded  ", "padded"),
    ("two\nlines", "two lines"),
])
def test_sanitize_flattens_to_one_line(raw, expected):
    assert clipboardtyper.sanitize(raw)[0] == expected


@pytest.mark.parametrize("raw", ["", "   ", "\n", None])
def test_sanitize_rejects_nothing_to_send(raw):
    text, err = clipboardtyper.sanitize(raw)
    assert text == ""
    assert err


def test_sanitize_refuses_a_leading_slash_so_it_is_not_a_command():
    text, err = clipboardtyper.sanitize("/remake")
    assert text == ""
    assert "command" in err


def test_sanitize_caps_the_length():
    text, err = clipboardtyper.sanitize("x" * 500)
    assert err is None
    assert len(text) == clipboardtyper.MAX_CHAT_LEN


def test_send_to_chat_wraps_the_text_in_two_enters(monkeypatch):
    events = []
    monkeypatch.setattr(clipboardtyper, "tap", lambda vk: events.append(vk))
    monkeypatch.setattr(clipboardtyper, "type_text",
                        lambda t: events.append(t) or len(t))
    monkeypatch.setattr(clipboardtyper.time, "sleep", lambda s: None)
    assert clipboardtyper.send_to_chat("hi") == 2
    assert events == [clipboardtyper.VK_RETURN, "hi", clipboardtyper.VK_RETURN]


def test_send_is_ignored_while_the_feature_is_off():
    t = clipboardtyper.ClipboardTyper()
    assert t.send_enabled is False
    assert t.send("MissFortune Flash is up") is False
    assert t._pending.qsize() == 0


def test_send_queues_sanitized_text():
    t = clipboardtyper.ClipboardTyper(send_enabled=True)
    assert t.send("  MissFortune  Flash is up  ") is True
    assert t._pending.get_nowait() == ("send", "MissFortune Flash is up")


def test_send_refuses_a_chat_command():
    t = clipboardtyper.ClipboardTyper(send_enabled=True)
    assert t.send("/all hello") is False
    assert t._pending.qsize() == 0


def test_nothing_is_typed_when_league_is_not_the_focused_window(monkeypatch):
    t = clipboardtyper.ClipboardTyper(send_enabled=True)
    monkeypatch.setattr(clipboardtyper.gamewindow, "find", lambda: 0)
    monkeypatch.setattr(clipboardtyper.gamewindow, "is_foreground",
                        lambda hwnd=None: False)
    typed = []
    monkeypatch.setattr(clipboardtyper, "send_to_chat", typed.append)
    t._send("MissFortune Flash is up")
    assert typed == []


def test_a_focused_league_gets_the_call_out(monkeypatch):
    t = clipboardtyper.ClipboardTyper(send_enabled=True)
    monkeypatch.setattr(clipboardtyper.gamewindow, "find", lambda: 1234)
    monkeypatch.setattr(clipboardtyper.gamewindow, "is_foreground",
                        lambda hwnd=None: True)
    typed = []
    monkeypatch.setattr(clipboardtyper, "send_to_chat",
                        lambda text: typed.append(text) or len(text))
    t._send("MissFortune Flash is up")
    assert typed == ["MissFortune Flash is up"]
    assert t._typing is False


# -- room codes never become the topic -----------------------------------

def test_a_room_code_does_not_appear_in_its_topic():
    topic = sync.room_topic("duo")
    assert "duo" not in topic
    assert topic.startswith("loltimer/v2/")


def test_the_same_code_always_gives_the_same_topic():
    assert sync.room_topic("duo") == sync.room_topic("  duo  ")


def test_different_codes_give_different_topics():
    assert sync.room_topic("duo") != sync.room_topic("duo2")


def test_no_room_means_no_topic():
    assert sync.room_topic("") == ""
    assert sync.room_topic(None) == ""


def test_a_hostile_code_cannot_reach_another_topic():
    for hostile in ("../other", "a/#", "+", "a b", "#"):
        topic = sync.room_topic(hostile)
        if not topic:
            continue
        tail = topic[len("loltimer/v2/"):]
        assert tail.isalnum()


def test_the_client_derives_its_topic_from_the_room():
    client = sync.SyncClient("duo", "broker.example", 8883)
    assert client.topic == sync.room_topic("duo")
    assert "duo" not in client.topic


def test_coming_online_asks_the_app_to_resend_its_timers():
    """Anything started while the link was down was dropped; resend on connect."""
    client = sync.SyncClient("duo", "broker.example")

    class FakeMqtt:
        def subscribe(self, *a, **k):
            pass

    client._on_connect(FakeMqtt(), None, {}, 0)
    assert client.connected
    assert [m["type"] for m in client.poll()] == [sync.MSG_CONNECTED]


# -- overlay scaling -----------------------------------------------------

def test_scaling_is_the_identity_at_one_x():
    Config.UI_SCALE = 1.0
    try:
        assert Config.icon_size() == Config.ICON_SIZE
        assert Config.font()[1] == Config.BASE_FONT_SIZE
    finally:
        Config.UI_SCALE = 1.0


def test_scaling_grows_every_dimension_together():
    Config.UI_SCALE = 2.0
    try:
        assert Config.icon_size() == Config.ICON_SIZE * 2
        assert Config.font()[1] == Config.BASE_FONT_SIZE * 2
        assert Config.scaled(3) == 6
    finally:
        Config.UI_SCALE = 1.0


def test_a_scaled_size_is_never_zero():
    Config.UI_SCALE = Config.UI_SCALE_MIN
    try:
        assert Config.scaled(1) >= 1
        assert Config.font(-6)[1] >= 6
    finally:
        Config.UI_SCALE = 1.0


@pytest.mark.parametrize("raw,expected", [
    (2.0, 2.0), (99, Config.UI_SCALE_MAX), (0, Config.UI_SCALE_MIN),
    ("x", 1.0), (None, 1.0),
])
def test_clamp_scale(raw, expected):
    assert Config.clamp_scale(raw) == expected
