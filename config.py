"""Shared configuration and the PyInstaller-aware resource resolver."""

from __future__ import annotations
import os
import sys


def resource_path(relative_path: str) -> str:
    base_path = getattr(sys, "_MEIPASS", None) or os.path.abspath(".")
    return os.path.join(base_path, relative_path)


class Config:
    if getattr(sys, "frozen", False):
        APP_DIR = os.path.dirname(sys.executable)
    else:
        APP_DIR = os.path.dirname(os.path.abspath(__file__))
    CONFIG_FILE = os.path.join(APP_DIR, "config.json")
    LOG_FILE = os.path.join(APP_DIR, "spell_timer.log")

    ITEM_HASTE_MAP = {
        3158: 10,    # Ionian Boots of Lucidity (SR/ARAM) -> 10 Haste
        3171: 20,    # Crimson Lucidity (Ornn Upgrade) -> 20 Haste
        223158: 10,  # Ionian Boots (Arena) -> 10 Haste
    }

    SPELL_TIMERS = {
        "summonerflash":    300,
        "summonerteleport": 360,
        "summonerheal":     240,
        "summonerboost":    210,  # Cleanse
        "summonerexhaust":  210,
        "summonerhaste":    210,  # Ghost
        "summonerbarrier":  180,
        "summonerdot":      180,  # Ignite
        "summonersmite":    15,
        "summonersnowball": 80,   # ARAM Mark
        "summonerclarity":  240,
        "summonermana":     240
    }

    CURATED_SPELLS = frozenset(SPELL_TIMERS)

    GLOBAL_OPACITY = 0.85
    ICON_SIZE = 38
    ROW_PADDING_Y = 6
    SHOW_SEPARATOR = True

    COLOR_BG = "#091428"
    COLOR_BORDER = "#463714"
    COLOR_SEPARATOR = "#000000"
    COLOR_TEXT_ACTIVE = "#FFFFFF"
    COLOR_TEXT_OUTLINE = "#000000"

    COLOR_PINNED = "#8B0000"    # Dark Red
    COLOR_HANDLE = "#666666"    # Grey
    COLOR_CI_BADGE = "#7B4DBF"  # Purple -- Cosmic Insight marker
    COLOR_TEXT_WARN = "#FFC845"  # Gold -- spell is nearly back up
    COLOR_SYNCED = "#0AC8B9"    # Teal -- duo sync live

    FONT_FAMILY = "Arial"
    BASE_FONT_SIZE = 12

    LCL_URL = "https://127.0.0.1:2999/liveclientdata/allgamedata"
    DDRAGON_VER_URL = "https://ddragon.leagueoflegends.com/api/versions.json"
    DDRAGON_DATA_URL = "https://ddragon.leagueoflegends.com/cdn/{}/data/en_US/summoner.json"

    GAME_POLL_INTERVAL = 2.0
    GAME_POLL_TIMEOUT = 1.5
    GAME_LOST_GRACE = 5

    UI_POLL_INTERVAL = 150

    DDRAGON_CACHE_FILE = os.path.join(APP_DIR, "spell_cooldowns.json")
    DDRAGON_CACHE_TTL = 24 * 3600

    WARN_THRESHOLD = 15
    NUDGE_STEP = 5

    DEFAULT_HOTKEY_MOD = "alt"

    SYNC_BROKER = "broker.hivemq.com"
    SYNC_PORT = 1883
