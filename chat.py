"""Timer call-out text, and copying it to the clipboard."""

from __future__ import annotations
import logging
from typing import Optional

from gamedata import format_clock

log = logging.getLogger("chat")

SPELL_LABELS = {
    "SummonerFlash": "Flash",
    "SummonerTeleport": "TP",
    "SummonerDot": "Ignite",
    "SummonerHeal": "Heal",
    "SummonerBarrier": "Barrier",
    "SummonerExhaust": "Exhaust",
    "SummonerBoost": "Cleanse",
    "SummonerHaste": "Ghost",
    "SummonerSmite": "Smite",
    "SummonerSnowball": "Snowball",
    "SummonerMana": "Clarity",
    "SummonerClarity": "Clarity",
}


def spell_label(spell: str) -> str:
    if not spell:
        return "Spell"
    name = spell
    if name.endswith("2") and name[:-1] in SPELL_LABELS:
        name = name[:-1]
    if name in SPELL_LABELS:
        return SPELL_LABELS[name]
    if name.startswith("Summoner") and len(name) > 8:
        return name[8:].rstrip("2") or "Spell"
    return name


def format_message(champ: str, spell: str, remaining: int,
                   game_time: Optional[float] = None) -> str:
    label = spell_label(spell)
    champ = champ or "Enemy"
    if not remaining or remaining <= 0:
        return f"{champ} {label} is up"
    if game_time is not None:
        return f"{champ} {label} up at {format_clock(game_time + remaining)}"
    return f"{champ} {label} back in {format_clock(remaining)}"


def copy(root, text: str) -> bool:
    try:
        root.clipboard_clear()
        root.clipboard_append(text)
        root.update_idletasks()
        return True
    except Exception as e:
        log.warning("Clipboard copy failed: %s", e)
        return False
