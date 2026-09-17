"""Optional enemy rune detection via Riot's Spectator-v5 API.

The spectator endpoint does not know about a game the moment it starts, so a
single lookup at match start almost always comes back empty. That looked like
a broken API key. Instead the lookup retries on a schedule until the game
shows up, and only gives up once it has run out of attempts.
"""

from __future__ import annotations
import json
import logging
import os
import queue
import threading
from typing import Callable, Dict, List, Optional, Tuple

import requests

log = logging.getLogger("runes")

COSMIC_INSIGHT_ID = 8347
COSMIC_INSIGHT_HASTE = 18

REGION_TO_PLATFORM = {
    "BR": "br1", "EUNE": "eun1", "EUW": "euw1", "JP": "jp1", "KR": "kr",
    "LAN": "la1", "LAS": "la2", "NA": "na1", "OCE": "oc1", "PH": "ph2",
    "RU": "ru", "SG": "sg2", "TH": "th2", "TR": "tr1", "TW": "tw2",
    "VN": "vn2", "ME": "me1", "MENA": "me1", "PBE": "pbe1",
}

PLATFORM_TO_ROUTE = {
    "na1": "americas", "br1": "americas", "la1": "americas", "la2": "americas",
    "pbe1": "americas",
    "euw1": "europe", "eun1": "europe", "tr1": "europe", "ru": "europe",
    "me1": "europe",
    "kr": "asia", "jp1": "asia",
    "oc1": "asia", "ph2": "asia", "sg2": "asia", "th2": "asia",
    "tw2": "asia", "vn2": "asia",
}

LOCKFILE_PATHS = [
    r"C:\Riot Games\League of Legends\lockfile",
    os.path.expandvars(r"%LOCALAPPDATA%\Riot Games\League of Legends\lockfile"),
]

DDRAGON_VERSIONS = "https://ddragon.leagueoflegends.com/api/versions.json"
DDRAGON_CHAMPIONS = "https://ddragon.leagueoflegends.com/cdn/{}/data/en_US/champion.json"

OK = "ok"
NO_REGION = "no_region"
NOT_FOUND = "not_found"
FORBIDDEN = "forbidden"
ERROR = "error"

# Seconds to wait before each further attempt, so the first four minutes of a
# game are covered. A rejected key or an unknown region will not fix itself,
# so only these two are worth trying again.
RETRY_DELAYS = (45, 75, 120, 180)
RETRYABLE = frozenset({NOT_FOUND, ERROR})


def read_lcu_region() -> Optional[str]:
    for path in LOCKFILE_PATHS:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r") as f:
                parts = f.read().strip().split(":")
            if len(parts) < 5:
                continue
            port, password = parts[2], parts[3]
            resp = requests.get(
                f"https://127.0.0.1:{port}/riotclient/region-locale",
                auth=("riot", password), verify=False, timeout=2,
            )
            if resp.status_code == 200:
                region = (resp.json().get("region") or "").strip().upper()
                if region:
                    return region
        except Exception:
            continue
    return None


def resolve_platform(configured: str = "") -> Optional[str]:
    val = (configured or "").strip().lower()
    if val:
        if val in PLATFORM_TO_ROUTE:
            return val
        return REGION_TO_PLATFORM.get(val.upper())
    region = read_lcu_region()
    if region:
        return REGION_TO_PLATFORM.get(region)
    return None


class ChampionIndex:
    def __init__(self, cache_path: str):
        self.cache_path = cache_path
        self._by_id: Dict[int, str] = {}

    def _load_disk(self) -> bool:
        try:
            with open(self.cache_path, "r") as f:
                data = json.load(f)
            self._by_id = {int(k): v for k, v in data.items()}
            return bool(self._by_id)
        except Exception:
            return False

    def _save_disk(self):
        try:
            with open(self.cache_path, "w") as f:
                json.dump({str(k): v for k, v in self._by_id.items()}, f)
        except Exception:
            pass

    def get(self) -> Dict[int, str]:
        if self._by_id:
            return self._by_id
        if self._load_disk():
            return self._by_id
        try:
            ver = requests.get(DDRAGON_VERSIONS, timeout=5).json()[0]
            data = requests.get(DDRAGON_CHAMPIONS.format(ver), timeout=8).json()["data"]
            self._by_id = {int(v["key"]): k for k, v in data.items()}
            self._save_disk()
        except Exception as e:
            log.warning(f"Champion index unavailable: {e}")
        return self._by_id


class RuneLookup:
    def __init__(self, api_key: str = "", region: str = "", cache_dir: str = ".",
                 retry_delays: Tuple[int, ...] = RETRY_DELAYS):
        self.api_key = (api_key or "").strip()
        self.region = (region or "").strip()
        self.retry_delays = tuple(retry_delays)
        self.results: "queue.Queue[Tuple[str, Dict[str, bool], bool]]" = queue.Queue()
        self.champions = ChampionIndex(os.path.join(cache_dir, "champion_ids.json"))
        self._busy = threading.Lock()
        self._stop = threading.Event()
        self._puuid: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def start(self, players: List[Dict[str, str]]):
        if not self.enabled:
            return
        if not self._busy.acquire(blocking=False):
            return
        self._stop.clear()
        threading.Thread(
            target=self._run, args=(players,), daemon=True, name="runes"
        ).start()

    def stop(self):
        """Abandon a retry schedule -- the match ended, or the key changed."""
        self._stop.set()

    def poll(self) -> List[Tuple[str, Dict[str, bool], bool]]:
        out = []
        while True:
            try:
                out.append(self.results.get_nowait())
            except queue.Empty:
                break
        return out


    def _run(self, players: List[Dict[str, str]]):
        try:
            for attempt, delay in enumerate((0,) + self.retry_delays):
                if delay and self._stop.wait(delay):
                    log.debug("Rune lookup abandoned.")
                    return
                if self._stop.is_set():
                    return

                try:
                    status, found = self._lookup(players)
                except Exception as e:
                    log.warning(f"Lookup failed: {e}")
                    status, found = ERROR, {}

                more = (status in RETRYABLE
                        and attempt < len(self.retry_delays))
                self.results.put((status, found, not more))
                if not more:
                    return
                log.info("Runes not visible yet (%s); retrying in %ds.",
                         status, self.retry_delays[attempt])
        finally:
            self._busy.release()

    def _headers(self) -> Dict[str, str]:
        return {"X-Riot-Token": self.api_key}

    def _lookup(self, players: List[Dict[str, str]]) -> Tuple[str, Dict[str, bool]]:
        platform = resolve_platform(self.region)
        if not platform:
            log.warning("Could not determine region -- set 'region' in config.json.")
            return NO_REGION, {}
        route = PLATFORM_TO_ROUTE.get(platform, "europe")

        puuid = self._puuid
        for p in ([] if puuid else players):
            name, tag = p.get("name"), p.get("tag")
            if not name or not tag:
                continue
            url = f"https://{route}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{name}/{tag}"
            try:
                r = requests.get(url, headers=self._headers(), timeout=5)
            except Exception:
                continue
            if r.status_code in (401, 403):
                log.warning("API key rejected (expired?).")
                return FORBIDDEN, {}
            if r.status_code == 200:
                puuid = r.json().get("puuid")
                if puuid:
                    # Good across retries; only the spectator call is repeated.
                    self._puuid = puuid
                    break
        if not puuid:
            return NOT_FOUND, {}

        url = f"https://{platform}.api.riotgames.com/lol/spectator/v5/active-games/by-summoner/{puuid}"
        try:
            r = requests.get(url, headers=self._headers(), timeout=6)
        except Exception as e:
            log.warning(f"Spectator request failed: {e}")
            return ERROR, {}

        if r.status_code in (401, 403):
            log.warning("API key rejected (expired?).")
            return FORBIDDEN, {}
        if r.status_code == 404:
            return NOT_FOUND, {}
        if r.status_code != 200:
            log.info(f"Spectator returned {r.status_code}.")
            return ERROR, {}

        by_id = self.champions.get()
        if not by_id:
            return ERROR, {}

        found: Dict[str, bool] = {}
        for part in r.json().get("participants", []):
            champ = by_id.get(part.get("championId"))
            if not champ:
                continue
            perks = (part.get("perks") or {}).get("perkIds") or []
            found[champ] = COSMIC_INSIGHT_ID in perks
        return OK, found
