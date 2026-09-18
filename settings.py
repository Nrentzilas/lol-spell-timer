"""Persisted settings: load, validate, and save without risking the file.

Saving happens on every pin toggle and every match end, so a half-written
config.json would cost the user their position, room code and API key. The
write goes to a temporary file and is renamed over the original, the same way
the cooldown cache is written.
"""

from __future__ import annotations
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Tuple

import hotkeys
import sync
from config import Config
from win32util import Win32Utils

log = logging.getLogger("settings")

# How much of the overlay has to stay on a monitor for it to be reachable.
MARGIN_X = 120
MARGIN_Y = 40


def default_position() -> Tuple[int, int]:
    vx, vy, vw, _ = Win32Utils.virtual_screen()
    return vx + vw - 250, vy + 100


def clamp_to_screen(x: int, y: int) -> Tuple[int, int]:
    vx, vy, vw, vh = Win32Utils.virtual_screen()
    try:
        x, y = int(x), int(y)
    except (TypeError, ValueError):
        return default_position()
    cx = max(vx, min(x, vx + vw - MARGIN_X))
    cy = max(vy, min(y, vy + vh - MARGIN_Y))
    if (cx, cy) != (x, y):
        log.warning("Saved position (%s,%s) is off-screen; moved to (%s,%s).",
                    x, y, cx, cy)
    return cx, cy


@dataclass
class Settings:
    x: int = 0
    y: int = 0
    pinned: bool = False
    room: str = ""
    broker: str = Config.SYNC_BROKER
    broker_port: int = Config.SYNC_PORT
    riot_api_key: str = ""
    region: str = ""
    hotkey_mod: str = Config.DEFAULT_HOTKEY_MOD
    type_on_paste: bool = False
    send_callouts: bool = False
    sound_cue: bool = False
    ui_scale: float = 1.0
    path: str = field(default=Config.CONFIG_FILE, repr=False)

    @property
    def sync_tls(self) -> bool:
        """Port 8883 is the convention for MQTT over TLS; honour it."""
        return int(self.broker_port) == Config.SYNC_TLS_PORT

    @classmethod
    def load(cls, path: str = None) -> "Settings":
        path = path or Config.CONFIG_FILE
        s = cls(path=path)
        data: Dict[str, Any] = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception as e:
                log.warning("Could not read settings (%s); using defaults.", e)
        else:
            s.x, s.y = default_position()

        if data:
            s.x = data.get("x", s.x)
            s.y = data.get("y", s.y)
            s.pinned = bool(data.get("pinned", False))
            s.room = sync.sanitize_room(data.get("room", ""))
            s.broker = cls._broker(data.get("broker"))
            s.broker_port = cls._port(data.get("broker_port"), s.broker)
            s.riot_api_key = (data.get("riot_api_key") or "").strip()
            s.region = (data.get("region") or "").strip()
            s.hotkey_mod = (data.get("hotkey_mod", Config.DEFAULT_HOTKEY_MOD) or "").strip()
            s.type_on_paste = bool(data.get("type_on_paste", False))
            s.send_callouts = bool(data.get("send_callouts", False))
            s.sound_cue = bool(data.get("sound_cue", False))
            s.ui_scale = Config.clamp_scale(data.get("ui_scale", 1.0))

        s.x, s.y = clamp_to_screen(s.x, s.y)
        return s

    @staticmethod
    def _broker(raw: Any) -> str:
        broker = (raw or "").strip() or Config.SYNC_BROKER
        if broker.lower() in Config.RETIRED_BROKERS:
            log.info("Broker %s is retired; using %s.", broker, Config.SYNC_BROKER)
            return Config.SYNC_BROKER
        return broker

    @staticmethod
    def _port(raw: Any, broker: str) -> int:
        try:
            port = int(raw)
        except (TypeError, ValueError):
            return Config.SYNC_PORT
        if not 1 <= port <= 65535:
            log.warning("Ignoring broker port %s; using %d.", port, Config.SYNC_PORT)
            return Config.SYNC_PORT
        return port

    def as_dict(self) -> Dict[str, Any]:
        return {
            "x": int(self.x),
            "y": int(self.y),
            "pinned": bool(self.pinned),
            "room": self.room,
            "broker": self.broker,
            "broker_port": int(self.broker_port),
            "riot_api_key": self.riot_api_key,
            "region": self.region,
            "hotkey_mod": self.hotkey_mod,
            "type_on_paste": bool(self.type_on_paste),
            "send_callouts": bool(self.send_callouts),
            "sound_cue": bool(self.sound_cue),
            "ui_scale": float(self.ui_scale),
        }

    def save(self) -> bool:
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.as_dict(), f, indent=1)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            log.debug("Settings saved.")
            return True
        except Exception as e:
            log.warning("Could not save settings: %s", e)
            try:
                os.remove(tmp)
            except OSError:
                pass
            return False

    def describe(self) -> str:
        return (f"Pos({self.x},{self.y}), Pinned({self.pinned}), "
                f"Room({self.room or 'none'}), "
                f"RuneKey({'set' if self.riot_api_key else 'none'}), "
                f"Hotkeys({hotkeys.describe(self.hotkey_mod)}), "
                f"Scale({self.ui_scale:g}x)")
