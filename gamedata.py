"""League client and DDragon access, and the polling worker."""

from __future__ import annotations
import json
import logging
import os
import queue
import threading
import time
from typing import Any, Dict, List, Optional

import requests

from config import Config

log = logging.getLogger("game")

MATCH_ENDED = object()


def _make_session() -> requests.Session:
    session = requests.Session()
    session.verify = False
    session.trust_env = False
    return session


class SpellCooldowns:
    def __init__(self, cache_file: str = None, ttl: int = None):
        self.cache_file = cache_file or Config.DDRAGON_CACHE_FILE
        self.ttl = Config.DDRAGON_CACHE_TTL if ttl is None else ttl

    def load_cached(self) -> Dict[str, int]:
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            timers = data.get("timers") or {}
            return {str(k): int(v) for k, v in timers.items()}
        except Exception:
            return {}

    def is_stale(self) -> bool:
        try:
            return (time.time() - os.path.getmtime(self.cache_file)) > self.ttl
        except OSError:
            return True

    def _save(self, timers: Dict[str, int]) -> None:
        try:
            tmp = self.cache_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"fetched": time.time(), "timers": timers}, f)
            os.replace(tmp, self.cache_file)
        except Exception as e:
            log.warning("Could not write cooldown cache: %s", e)

    def fetch(self) -> Dict[str, int]:
        try:
            with _make_session() as session:
                session.verify = True
                v_resp = session.get(Config.DDRAGON_VER_URL, timeout=5)
                if v_resp.status_code != 200:
                    return {}
                version = v_resp.json()[0]

                d_resp = session.get(
                    Config.DDRAGON_DATA_URL.format(version), timeout=8)
                if d_resp.status_code != 200:
                    return {}

            timers = {}
            for spell_id, info in (d_resp.json().get("data") or {}).items():
                cooldowns = info.get("cooldown") or [300]
                timers[spell_id.lower()] = int(cooldowns[0])
            return timers
        except Exception as e:
            log.warning("DDragon update failed: %s", e)
            return {}

    @staticmethod
    def _merge(timers: Dict[str, int]) -> int:
        added = {k: v for k, v in timers.items()
                 if k not in Config.CURATED_SPELLS and v > 0}
        Config.SPELL_TIMERS.update(added)
        return len(added)

    def apply_cached(self) -> int:
        return self._merge(self.load_cached())

    def refresh_async(self) -> None:
        if not self.is_stale():
            log.info("Cooldown cache is current.")
            return

        def worker():
            timers = self.fetch()
            if not timers:
                return
            added = self._merge(timers)
            self._save(timers)
            log.info("Cooldowns refreshed from DDragon: %d spells, %d new "
                     "(curated values kept).", len(timers), added)

        threading.Thread(target=worker, daemon=True, name="ddragon").start()


class GameDataManager:
    @staticmethod
    def parse_enemies(data: Optional[Dict]) -> List[Dict]:
        if not data:
            return []

        all_players = data.get("allPlayers") or []
        active_data = data.get("activePlayer") or {}
        my_name = active_data.get("summonerName")

        my_team = None
        if my_name:
            for p in all_players:
                if p.get("summonerName") == my_name:
                    my_team = p.get("team")
                    break

        if not my_team:
            my_team = active_data.get("team", "ORDER")

        enemies = []

        for p in all_players:
            if p.get("team") == my_team:
                continue

            raw_name = p.get("rawChampionName", "") or p.get("championName", "")
            champ_name = raw_name.split("_")[-1] if "_" in raw_name else raw_name
            spells = p.get("summonerSpells") or {}

            current_haste = 0
            for item in (p.get("items") or []):
                current_haste += Config.ITEM_HASTE_MAP.get(item.get("itemID", 0), 0)

            spell1 = GameDataManager._clean_spell_name(
                (spells.get("summonerSpellOne") or {}).get("rawDisplayName"))
            spell2 = GameDataManager._clean_spell_name(
                (spells.get("summonerSpellTwo") or {}).get("rawDisplayName"))
            if spell2 == spell1:
                spell2 = spell2 + "2"

            enemies.append({
                "champ": champ_name,
                "spell1": spell1,
                "spell2": spell2,
                "haste": current_haste,
            })
        return enemies

    @staticmethod
    def player_ids(data: Optional[Dict]) -> List[Dict[str, str]]:
        out = []
        for p in ((data or {}).get("allPlayers") or []):
            name, tag = p.get("riotIdGameName"), p.get("riotIdTagLine")
            if name and tag:
                out.append({"name": name, "tag": tag})
        return out

    @staticmethod
    def _clean_spell_name(raw: Optional[str]) -> str:
        if not raw:
            return "Unknown"
        raw_lower = raw.lower()
        if "teleport" in raw_lower: return "SummonerTeleport"
        if "smite" in raw_lower: return "SummonerSmite"
        if "flash" in raw_lower: return "SummonerFlash"
        if "ignite" in raw_lower or "dot" in raw_lower: return "SummonerDot"
        if "barrier" in raw_lower: return "SummonerBarrier"
        if "heal" in raw_lower: return "SummonerHeal"
        if "exhaust" in raw_lower: return "SummonerExhaust"
        if "cleanse" in raw_lower or "boost" in raw_lower: return "SummonerBoost"
        if "ghost" in raw_lower or "haste" in raw_lower: return "SummonerHaste"
        parts = raw.split("_")
        for p in reversed(parts):
            if p.startswith("Summoner") and p != "SummonerSpell":
                return p
        return "Unknown"

    @staticmethod
    def game_time(data: Optional[Dict]) -> Optional[float]:
        try:
            raw = ((data or {}).get("gameData") or {}).get("gameTime")
            return float(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None


def format_clock(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 60}:{total % 60:02d}"


class GamePoller:
    def __init__(self, interval: float = None, timeout: float = None,
                 grace: int = None):
        self.interval = Config.GAME_POLL_INTERVAL if interval is None else interval
        self.timeout = Config.GAME_POLL_TIMEOUT if timeout is None else timeout
        self.grace = Config.GAME_LOST_GRACE if grace is None else grace
        self.inbox: "queue.Queue[Any]" = queue.Queue()
        self.misses = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="gamepoll")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None

    def poll(self) -> Any:
        latest = None
        while True:
            try:
                latest = self.inbox.get_nowait()
            except queue.Empty:
                return latest


    def _run(self) -> None:
        session = _make_session()
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                data = self._fetch(session)

                if data is not None:
                    if self.misses >= self.grace:
                        log.info("Live client back.")
                    self.misses = 0
                    self.inbox.put(data)
                else:
                    self.misses += 1
                    if self.misses == self.grace:
                        self.inbox.put(MATCH_ENDED)
                    elif self.misses < self.grace:
                        log.debug("Live client miss %d/%d.", self.misses, self.grace)

                self._stop.wait(max(0.1, self.interval - (time.monotonic() - started)))
        finally:
            session.close()

    def _fetch(self, session: requests.Session) -> Optional[Dict]:
        try:
            resp = session.get(Config.LCL_URL, timeout=self.timeout)
        except Exception:
            return None
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            return None
