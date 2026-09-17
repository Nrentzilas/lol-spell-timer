import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config, DUPLICATE_SUFFIX, base_spell_name
from gamedata import GameDataManager, GamePoller, MATCH_ENDED


def player(name, team, champ, s1, s2, items=(), tag=None):
    return {
        "summonerName": name,
        "riotIdGameName": name,
        "riotIdTagLine": tag or "EUW",
        "team": team,
        "rawChampionName": "game_character_displayname_" + champ,
        "summonerSpells": {
            "summonerSpellOne": {"rawDisplayName": s1},
            "summonerSpellTwo": {"rawDisplayName": s2},
        },
        "items": [{"itemID": i} for i in items],
    }


FLASH = "GeneratedTip_SummonerSpell_SummonerFlash_DisplayName"
IGNITE = "GeneratedTip_SummonerSpell_SummonerDot_DisplayName"
TP = "GeneratedTip_SummonerSpell_SummonerTeleport_DisplayName"


def test_returns_only_the_other_team():
    data = {
        "activePlayer": {"summonerName": "Me"},
        "allPlayers": [
            player("Me", "CHAOS", "Ahri", FLASH, IGNITE),
            player("Ally", "CHAOS", "Lulu", FLASH, IGNITE),
            player("Foe", "ORDER", "Darius", FLASH, TP),
        ],
    }
    enemies = GameDataManager.parse_enemies(data)
    assert [e["champ"] for e in enemies] == ["Darius"]


def test_active_player_team_field_is_not_trusted():
    data = {
        "activePlayer": {"summonerName": "Me", "team": "ORDER"},
        "allPlayers": [
            player("Me", "CHAOS", "Ahri", FLASH, IGNITE),
            player("Foe", "ORDER", "Darius", FLASH, TP),
        ],
    }
    assert [e["champ"] for e in GameDataManager.parse_enemies(data)] == ["Darius"]


def test_falls_back_to_order_when_player_is_not_in_the_list():
    data = {
        "activePlayer": {"summonerName": "Spectator"},
        "allPlayers": [player("Foe", "CHAOS", "Darius", FLASH, TP)],
    }
    assert [e["champ"] for e in GameDataManager.parse_enemies(data)] == ["Darius"]


def test_empty_and_missing_payloads():
    assert GameDataManager.parse_enemies(None) == []
    assert GameDataManager.parse_enemies({}) == []


@pytest.mark.parametrize("items,expected", [
    ((), 0),
    ((3158,), 10),          # Ionian Boots of Lucidity
    ((3171,), 20),
    ((223158,), 10),
    ((1001, 3340), 0),
    ((3158, 3171), 30),
])


def test_item_haste(items, expected):
    data = {
        "activePlayer": {"summonerName": "Me"},
        "allPlayers": [
            player("Me", "CHAOS", "Ahri", FLASH, IGNITE),
            player("Foe", "ORDER", "Darius", FLASH, TP, items=items),
        ],
    }
    assert GameDataManager.parse_enemies(data)[0]["haste"] == expected


@pytest.mark.parametrize("raw,expected", [
    (FLASH, "SummonerFlash"),
    (IGNITE, "SummonerDot"),
    (TP, "SummonerTeleport"),
    ("GeneratedTip_SummonerSpell_SummonerSmite_DisplayName", "SummonerSmite"),
    ("GeneratedTip_SummonerSpell_SummonerBoost_DisplayName", "SummonerBoost"),
    ("Cleanse", "SummonerBoost"),
    ("Ghost", "SummonerHaste"),
    ("SummonerSnowball", "SummonerSnowball"),
    (None, "Unknown"),
    ("", "Unknown"),
    ("total gibberish", "Unknown"),
])


def test_clean_spell_name(raw, expected):
    assert GameDataManager._clean_spell_name(raw) == expected


def test_every_known_spell_has_a_base_cooldown():
    for raw, _ in [(FLASH, 0), (IGNITE, 0), (TP, 0)]:
        name = GameDataManager._clean_spell_name(raw)
        assert name.lower() in Config.SPELL_TIMERS


def test_duplicate_spell_names_are_disambiguated():
    data = {
        "activePlayer": {"summonerName": "Me"},
        "allPlayers": [
            player("Me", "CHAOS", "Ahri", FLASH, IGNITE),
            player("Foe", "ORDER", "Darius", "nonsense", "more nonsense"),
        ],
    }
    enemy = GameDataManager.parse_enemies(data)[0]
    assert enemy["spell1"] == "Unknown"
    assert enemy["spell2"] != enemy["spell1"]


def test_player_ids_skips_incomplete_entries():
    data = {"allPlayers": [
        {"riotIdGameName": "A", "riotIdTagLine": "EUW"},
        {"riotIdGameName": "B"},
        {"riotIdTagLine": "NA1"},
    ]}
    assert GameDataManager.player_ids(data) == [{"name": "A", "tag": "EUW"}]


def test_player_ids_tolerates_missing_payload():
    assert GameDataManager.player_ids(None) == []
    assert GameDataManager.player_ids({}) == []


class FakePoller(GamePoller):
    def __init__(self, responses, **kw):
        super().__init__(**kw)
        self.responses = list(responses)

    def _fetch(self, session):
        if not self.responses:
            self._stop.set()
            return None
        resp = self.responses.pop(0)
        if not self.responses:
            self._stop.set()
        return resp


def drain(poller):
    out = []
    while not poller.inbox.empty():
        out.append(poller.inbox.get_nowait())
    return out


def run_scripted(responses, grace=3):
    p = FakePoller(responses, interval=0.001, grace=grace)
    p._run()
    return drain(p)


def test_single_miss_does_not_end_the_match():
    events = run_scripted([{"a": 1}, None, {"a": 2}], grace=3)
    assert MATCH_ENDED not in events
    assert events == [{"a": 1}, {"a": 2}]


def test_match_ends_after_the_grace_period():
    events = run_scripted([{"a": 1}, None, None, None], grace=3)
    assert events[-1] is MATCH_ENDED


def test_match_ended_is_emitted_only_once():
    events = run_scripted([{"a": 1}] + [None] * 8, grace=3)
    assert events.count(MATCH_ENDED) == 1


def test_recovery_after_a_near_miss_resets_the_counter():
    events = run_scripted([{"a": 1}, None, None, {"a": 2}, None, None], grace=3)
    assert MATCH_ENDED not in events
    assert events == [{"a": 1}, {"a": 2}]


def test_poll_returns_the_newest_item_and_none_when_idle():
    p = GamePoller()
    assert p.poll() is None
    p.inbox.put({"n": 1})
    p.inbox.put({"n": 2})
    assert p.poll() == {"n": 2}
    assert p.poll() is None


def test_poll_does_not_lose_match_ended_behind_a_backlog():
    p = GamePoller()
    p.inbox.put({"n": 1})
    p.inbox.put(MATCH_ENDED)
    assert p.poll() is MATCH_ENDED


# -- resolving a disambiguated duplicate --------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("SummonerSmite2", "SummonerSmite"),
    ("SummonerFlash2", "SummonerFlash"),
    ("SummonerFlash", "SummonerFlash"),
    ("Unknown2", "Unknown2"),        # not a spell we know; leave it be
    ("Unknown", "Unknown"),
    ("", ""),
    (None, ""),
])
def test_base_spell_name(raw, expected):
    assert base_spell_name(raw) == expected


def test_the_suffix_a_duplicate_gets_is_the_one_that_is_stripped():
    """gamedata appends it and config strips it; they must agree."""
    data = {
        "activePlayer": {"summonerName": "Me", "team": "ORDER"},
        "allPlayers": [
            {"summonerName": "Me", "team": "ORDER"},
            {"summonerName": "Them", "team": "CHAOS",
             "rawChampionName": "game_character_displayname_LeeSin",
             "summonerSpells": {
                 "summonerSpellOne": {"rawDisplayName": "GeneratedTip_Smite"},
                 "summonerSpellTwo": {"rawDisplayName": "GeneratedTip_Smite"}}},
        ],
    }
    enemy = GameDataManager.parse_enemies(data)[0]
    assert enemy["spell1"] == "SummonerSmite"
    assert enemy["spell2"] == "SummonerSmite" + DUPLICATE_SUFFIX
    # The key differs, but both resolve to the same real spell.
    assert base_spell_name(enemy["spell2"]) == enemy["spell1"]
    assert Config.SPELL_TIMERS[base_spell_name(enemy["spell2"]).lower()] == 15
