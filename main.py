"""Spell Timer: a borderless overlay tracking enemy summoner cooldowns.

This module is the wiring. The pieces it wires together:

    gamedata     polls League's live client endpoint
    widgets      draws a row per enemy, on top of cooldown.CooldownTimer
    sync         shares timers with a duo partner over MQTT
    runes        optional Cosmic Insight lookup via the Riot API
    hotkeys      system-wide keys for arming a timer
    clipboardtyper  types call-outs into League's chat
    tray/dialogs/settings  the bits the user configures
"""

from __future__ import annotations
import logging
import os
import queue
import signal
import sys
import tkinter as tk
from typing import Any, Dict, List, Optional, Tuple

import urllib3

import applog
import chat
import clipboardtyper
import dialogs
import gamewindow
import hotkeys
import runes
import sync
import tray as tray_module
from config import Config
from assets import AssetManager
from gamedata import MATCH_ENDED, GameDataManager, GamePoller, SpellCooldowns
from settings import Settings, default_position
from version import APP_NAME, __version__
from widgets import ChampionIcon, SpellTimerWidget
from win32util import SingleInstanceChecker, TopmostKeeper, Win32Utils

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = logging.getLogger("app")

# Stand-in rows so the overlay can be positioned without being in a game,
# which is otherwise the first thing a new user cannot do.
DEMO_ENEMIES = [
    {"champ": "Ahri", "spell1": "SummonerFlash", "spell2": "SummonerDot", "haste": 0},
    {"champ": "LeeSin", "spell1": "SummonerSmite", "spell2": "SummonerFlash", "haste": 0},
    {"champ": "Darius", "spell1": "SummonerTeleport", "spell2": "SummonerFlash", "haste": 0},
    {"champ": "Ezreal", "spell1": "SummonerFlash", "spell2": "SummonerHeal", "haste": 0},
    {"champ": "Leona", "spell1": "SummonerFlash", "spell2": "SummonerExhaust", "haste": 0},
]


class OverlayApp:
    def __init__(self, settings: Settings):
        self.settings = settings
        Config.UI_SCALE = settings.ui_scale

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP_NAME)
        self.root.configure(bg=Config.COLOR_BG)
        self.root.overrideredirect(True)
        self.root.wm_attributes("-topmost", True)
        self.root.wm_attributes("-alpha", Config.GLOBAL_OPACITY)
        # Everything below runs inside a Tk callback. Without this, a failure
        # in one goes to a stderr that the windowed build does not have, and
        # the user is told to check a log that never mentions it.
        self.root.report_callback_exception = self._on_ui_error

        self.hwnd = Win32Utils.hwnd_of(self.root)
        Win32Utils.set_no_focus(self.hwnd)
        self.topmost = TopmostKeeper(self.hwnd)
        self._warned_fullscreen = False

        self.game_active = False
        self.demo_mode = False
        self.enemy_data_cache: Dict[str, Dict] = {}
        self._img_refs: List[Any] = []
        self.widgets: Dict[Tuple[str, str], SpellTimerWidget] = {}
        self._by_champ: Dict[str, List[SpellTimerWidget]] = {}
        self.champ_icons: Dict[str, ChampionIcon] = {}
        self.cosmic_insight: Dict[str, bool] = {}
        self.enemy_order: List[str] = []
        self.enemy_spells: Dict[str, List[str]] = {}
        self._current_enemies: List[Dict] = []

        self._actions: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
        self._last_snapshot: Optional[Dict] = None
        self._last_handle_state = None
        self._shutting_down = False
        self._drag_data = {"x": 0, "y": 0}

        self.rune_lookup = runes.RuneLookup(settings.riot_api_key,
                                            settings.region, Config.APP_DIR)
        self.rune_status = None

        self.hotkeys = hotkeys.HotkeyManager(settings.hotkey_mod)
        self.hotkeys.start()

        self.typer = clipboardtyper.ClipboardTyper(
            enabled=settings.type_on_paste, send_enabled=settings.send_callouts)
        self.typer.start()

        self.sync = self._new_sync_client()
        self.sync.start()

        self.game_poller = GamePoller()
        self.game_poller.start()

        self._build_chrome()
        self.dialogs = dialogs.DialogHost(self.root)
        self.tray = tray_module.Tray(self.request, self)
        self.tray.run_detached()

        signal.signal(signal.SIGINT, self._on_signal)

        self._monitor_game_loop()
        self._poll_loop()

    # -- state the tray reads --------------------------------------------

    @property
    def ui_scale(self) -> float:
        return self.settings.ui_scale

    @property
    def send_callouts(self) -> bool:
        return self.settings.send_callouts

    @property
    def type_on_paste(self) -> bool:
        return self.settings.type_on_paste

    # -- construction ----------------------------------------------------

    def _build_chrome(self) -> None:
        self.container = tk.Frame(self.root, bg=Config.COLOR_BORDER, padx=1, pady=1)
        self.container.pack()
        self.inner = tk.Frame(self.container, bg=Config.COLOR_BG, padx=4, pady=4)
        self.inner.pack()

        self.handle = tk.Label(self.inner, text="::::", bg=Config.COLOR_BG,
                               fg=Config.COLOR_HANDLE,
                               font=(Config.FONT_FAMILY, 7, "bold"), cursor="fleur")
        self.handle.pack(fill="x", pady=(0, 2))
        self.handle.bind("<ButtonPress-1>", self._start_drag)
        self.handle.bind("<B1-Motion>", self._do_drag)
        self.handle.bind("<Button-3>", self._toggle_pin)

        self.enemies_frame = tk.Frame(self.inner, bg=Config.COLOR_BG)
        self.enemies_frame.pack()

        self._update_handle_visual()
        self.root.withdraw()

    def _new_sync_client(self) -> sync.SyncClient:
        return sync.SyncClient(self.settings.room, self.settings.broker,
                               self.settings.broker_port, self.settings.sync_tls)

    def _on_ui_error(self, exc, value, tb) -> None:
        log.error("Unhandled UI error", exc_info=(exc, value, tb))

    # -- haste and runes -------------------------------------------------

    def get_haste(self, champ_name: str) -> int:
        haste = (self.enemy_data_cache.get(champ_name) or {}).get("haste", 0)
        if self.cosmic_insight.get(champ_name):
            haste += runes.COSMIC_INSIGHT_HASTE
        return haste

    def set_cosmic_insight(self, champ: str, value: Optional[bool] = None,
                           broadcast: bool = True) -> None:
        current = self.cosmic_insight.get(champ, False)
        new = (not current) if value is None else bool(value)
        if new == current and value is not None:
            return

        self.cosmic_insight[champ] = new
        icon = self.champ_icons.get(champ)
        if icon:
            icon.set_flag(new)

        haste = self.get_haste(champ)
        for w in self._by_champ.get(champ, ()):
            w.recalculate(haste)

        if broadcast:
            self.publish(sync.MSG_CI, {"champ": champ, "on": new})
        log.info("%s: Cosmic Insight %s -> %d haste", champ,
                 "ON" if new else "OFF", haste)

    def _apply_rune_results(self, status: str, found: Dict[str, bool]) -> None:
        self.rune_status = status
        if status != runes.OK:
            reason = {
                runes.NOT_FOUND: "custom/practice game, or not visible to spectator yet",
                runes.FORBIDDEN: "API key rejected -- dev keys expire every 24h",
                runes.NO_REGION: "region unknown -- set 'region' in config.json",
                runes.ERROR: "lookup failed",
            }.get(status, status)
            log.info("No auto-detect (%s). Ctrl+click a portrait to set it manually.",
                     reason)
            return

        for champ in self.champ_icons:
            self.set_cosmic_insight(champ, found.get(champ, False), broadcast=True)
        hits = [c for c in self.champ_icons if found.get(c)]
        log.info("Cosmic Insight: %s", ", ".join(hits) if hits else "nobody")

    # -- duo sync --------------------------------------------------------

    def publish(self, msg_type: str, body: Dict[str, Any]) -> None:
        # Demo rows are furniture; they must never reach a partner.
        if self.demo_mode:
            return
        self.sync.publish(msg_type, body)

    def broadcast_start(self, champ: str, spell: str, remaining: int,
                        base_cd: int, haste: int) -> None:
        # The base cooldown travels with the timer so the receiver can still
        # correct it when a Cosmic Insight flag turns up later.
        self.publish(sync.MSG_START, {"champ": champ, "spell": spell,
                                      "rem": int(remaining),
                                      "cd": int(base_cd), "haste": int(haste)})

    def broadcast_reset(self, champ: str, spell: str) -> None:
        self.publish(sync.MSG_RESET, {"champ": champ, "spell": spell})

    def broadcast_adjust(self, champ: str, spell: str, remaining: int) -> None:
        self.publish(sync.MSG_ADJUST, {"champ": champ, "spell": spell,
                                       "rem": int(remaining)})

    def _handle_sync_message(self, msg: Dict) -> None:
        kind = msg.get("type")

        if kind == sync.MSG_START:
            self._apply_remote_start(msg.get("champ"), msg.get("spell"),
                                     msg.get("rem"), msg.get("cd"),
                                     msg.get("haste"))

        elif kind == sync.MSG_RESET:
            w = self.widgets.get((msg.get("champ"), msg.get("spell")))
            if w and w.is_active:
                w.reset(broadcast=False)

        elif kind == sync.MSG_ADJUST:
            w = self.widgets.get((msg.get("champ"), msg.get("spell")))
            if w and w.is_active:
                w.set_remaining(msg.get("rem"), broadcast=False)

        elif kind == sync.MSG_CI:
            champ = msg.get("champ")
            if champ:
                self.set_cosmic_insight(champ, bool(msg.get("on")), broadcast=False)

        elif kind == sync.MSG_HELLO:
            self._send_state()

        elif kind == sync.MSG_STATE:
            for champ in (msg.get("ci") or []):
                self.set_cosmic_insight(champ, True, broadcast=False)
            for t in (msg.get("timers") or []):
                self._apply_remote_start(t.get("champ"), t.get("spell"),
                                         t.get("rem"), t.get("cd"),
                                         t.get("haste"))

    def _send_state(self) -> None:
        timers = [
            {"champ": c, "spell": s, "rem": w.remaining,
             "cd": w.timer.base_cd, "haste": w.timer.haste}
            for (c, s), w in self.widgets.items()
            if w.is_active and w.remaining > 0
        ]
        ci = [c for c, on in self.cosmic_insight.items() if on]
        if timers or ci:
            self.publish(sync.MSG_STATE, {"timers": timers, "ci": ci})

    def _apply_remote_start(self, champ: Optional[str], spell: Optional[str],
                            remaining: Any, base_cd: Any = None,
                            haste: Any = 0) -> None:
        w = self.widgets.get((champ, spell))
        if not w:
            return
        try:
            remaining = int(remaining)
        except (TypeError, ValueError):
            return
        if remaining <= 0:
            return
        # Keep whichever timer has longer to run: a partner echoing an older
        # state should not shorten a cooldown we started more recently.
        if w.is_active and w.remaining >= remaining:
            return
        try:
            base_cd = int(base_cd) if base_cd else None
        except (TypeError, ValueError):
            base_cd = None
        try:
            haste = int(haste or 0)
        except (TypeError, ValueError):
            haste = 0

        w.cancel()
        if w.adopt(remaining, base_cd, haste):
            log.info("%s %s -> %ds (from duo)", champ, spell, remaining)

    def _restart_sync(self) -> None:
        self.sync.stop()
        self.sync = self._new_sync_client()
        self.sync.start()
        self._last_handle_state = None
        self._update_handle_visual()

    # -- the two poll loops ----------------------------------------------

    def _poll_loop(self) -> None:
        """Everything that arrives from another thread lands here."""
        if self._shutting_down:
            return

        for msg in self.sync.poll():
            try:
                self._handle_sync_message(msg)
            except Exception as e:
                log.warning("Bad message ignored: %s", e)

        for status, found in self.rune_lookup.poll():
            self._apply_rune_results(status, found)

        for slot, spell_idx in self.hotkeys.poll():
            self._fire_hotkey(slot, spell_idx)

        while True:
            try:
                name, value = self._actions.get_nowait()
            except queue.Empty:
                break
            self._dispatch(name, value)

        self._update_handle_visual()
        self.root.after(Config.UI_POLL_INTERVAL, self._poll_loop)

    def _monitor_game_loop(self) -> None:
        if self._shutting_down:
            return
        if self.game_active or self.demo_mode:
            self.topmost.tick()

        item = self.game_poller.poll()
        if item is MATCH_ENDED:
            self._on_match_ended()
        elif item is not None:
            self._on_game_data(item)

        self.root.after(Config.UI_POLL_INTERVAL, self._monitor_game_loop)

    def _fire_hotkey(self, slot: int, spell_idx: int) -> None:
        if not self.game_active or slot >= len(self.enemy_order):
            return
        champ = self.enemy_order[slot]
        spells = self.enemy_spells.get(champ, [])
        if spell_idx >= len(spells):
            return
        w = self.widgets.get((champ, spells[spell_idx]))
        if w:
            w.arm()

    # -- tray actions ----------------------------------------------------

    def request(self, name: str, value: Any = None) -> None:
        """Called from the tray thread. Only queues; never touches Tk."""
        self._actions.put((name, value))

    def _dispatch(self, name: str, value: Any) -> None:
        handlers = {
            tray_module.ROOM: self._open_room_dialog,
            tray_module.APIKEY: self._open_apikey_dialog,
            tray_module.HOTKEYS: self._open_hotkey_dialog,
            tray_module.RESET_POSITION: self._reset_position,
            tray_module.TOGGLE_SEND: self._toggle_send_callouts,
            tray_module.TOGGLE_PASTE: self._toggle_type_on_paste,
            tray_module.TOGGLE_DEMO: self._toggle_demo,
            tray_module.OPEN_LOG: self._open_log,
            tray_module.QUIT: self._shutdown,
        }
        if name == tray_module.SCALE:
            self._set_scale(value)
            return
        handler = handlers.get(name)
        if handler is None:
            log.warning("Unknown tray action '%s'.", name)
            return
        handler()

    def _toggle_send_callouts(self) -> None:
        self.settings.send_callouts = not self.settings.send_callouts
        self.typer.send_enabled = self.settings.send_callouts
        self.settings.save()
        log.info("Send call-outs to chat: %s",
                 "on" if self.settings.send_callouts else "off")

    def _toggle_type_on_paste(self) -> None:
        self.settings.type_on_paste = not self.settings.type_on_paste
        self.typer.enabled = self.settings.type_on_paste
        self.settings.save()
        log.info("Type clipboard on Ctrl+V: %s",
                 "on" if self.settings.type_on_paste else "off")

    def _open_log(self) -> None:
        try:
            os.startfile(Config.LOG_FILE)       # noqa: S606 - Windows only
        except Exception as e:
            log.warning("Could not open the log file: %s", e)

    def _set_scale(self, value: Any) -> None:
        scale = Config.clamp_scale(value)
        if abs(scale - self.settings.ui_scale) < 0.01:
            return
        self.settings.ui_scale = scale
        Config.UI_SCALE = scale
        self.settings.save()
        log.info("Overlay scale set to %gx.", scale)
        self._rebuild_rows_preserving_timers()

    def _toggle_demo(self) -> None:
        if self.game_active:
            # A real match outranks the demo, and already shows real rows.
            log.info("Demo rows are not needed while a match is running.")
            self.demo_mode = False
            return
        self.demo_mode = not self.demo_mode
        if self.demo_mode:
            log.info("Showing demo rows.")
            self._build_enemy_rows(DEMO_ENEMIES)
            self._show_overlay()
        else:
            log.info("Hiding demo rows.")
            self.root.withdraw()
            self._clear_rows()

    # -- dialogs ---------------------------------------------------------

    def _dialog_position(self) -> Tuple[int, int]:
        return self.settings.x, self.settings.y

    def _open_room_dialog(self) -> None:
        def apply(value):
            new_room = sync.sanitize_room(value)
            if new_room == self.settings.room:
                return
            self.settings.room = new_room
            self.settings.save()
            self._restart_sync()

        self.dialogs.open_input(
            "Duo Sync",
            "Room code (both of you type the same thing):",
            "Leave empty to turn sync off.",
            self.settings.room, apply, self._dialog_position())

    def _open_apikey_dialog(self) -> None:
        def apply(value):
            key = (value or "").strip()
            if key == self.settings.riot_api_key:
                return
            self.settings.riot_api_key = key
            self.settings.save()
            self.rune_lookup = runes.RuneLookup(key, self.settings.region,
                                                Config.APP_DIR)
            self.rune_status = None
            log.info("API key %s.", "set" if key else "cleared")
            if self.game_active and self.rune_lookup.enabled and self._last_snapshot:
                self.rune_lookup.start(GameDataManager.player_ids(self._last_snapshot))

        self.dialogs.open_input(
            "Riot API Key",
            "Riot API key -- auto-detects enemy Cosmic Insight:",
            "Optional. Get one at developer.riotgames.com (dev keys last 24h).\n"
            "Leave empty to rely on Ctrl+click only.",
            self.settings.riot_api_key, apply, self._dialog_position())

    def _open_hotkey_dialog(self) -> None:
        def apply(value):
            new_mod = (value or "").strip().lower()
            if new_mod == self.settings.hotkey_mod:
                return
            _, err = hotkeys.parse_modifier(new_mod)
            if new_mod and err:
                log.warning("Ignored '%s': %s.", new_mod, err)
                return
            self.settings.hotkey_mod = new_mod
            self.settings.save()
            self.hotkeys.stop()
            self.hotkeys = hotkeys.HotkeyManager(new_mod)
            self.hotkeys.start()

        self.dialogs.open_input(
            "Hotkeys",
            "Modifier for slot hotkeys (<mod>+1-5 arms a spell):",
            "e.g. alt, ctrl, ctrl+alt. Add Shift for the second spell. "
            "Empty turns hotkeys off. Shift alone is not allowed.",
            self.settings.hotkey_mod, apply, self._dialog_position())

    # -- window placement ------------------------------------------------

    def _show_overlay(self) -> None:
        self.root.geometry(f"+{self.settings.x}+{self.settings.y}")
        Win32Utils.set_no_focus(self.hwnd)
        Win32Utils.show_no_activate(self.hwnd)
        self.topmost.reset()

    def _reset_position(self) -> None:
        self.settings.x, self.settings.y = default_position()
        self.settings.pinned = False
        if self.game_active or self.demo_mode:
            self.root.geometry(f"+{self.settings.x}+{self.settings.y}")
        self._update_handle_visual()
        self.settings.save()
        log.info("Position reset to (%s,%s).", self.settings.x, self.settings.y)

    def _toggle_pin(self, event):
        self.settings.pinned = not self.settings.pinned
        self._update_handle_visual()
        self.settings.save()

    def _update_handle_visual(self) -> None:
        state = (self.settings.pinned, self.sync.enabled, self.sync.connected)
        if state == self._last_handle_state:
            return
        self._last_handle_state = state

        if self.sync.enabled:
            linked = self.sync.connected
            suffix = "  \U0001F517" if linked else "  ⌛"
            fg = Config.COLOR_SYNCED if linked else Config.COLOR_HANDLE
        else:
            suffix = ""
            fg = Config.COLOR_HANDLE

        if self.settings.pinned:
            self.handle.config(text="\U0001F512 PINNED" + suffix,
                               fg=Config.COLOR_PINNED, cursor="arrow")
        else:
            self.handle.config(text="::::" + suffix, fg=fg, cursor="fleur")

    def _start_drag(self, event):
        if self.settings.pinned:
            return
        self._drag_data["x"] = event.x
        self._drag_data["y"] = event.y

    def _do_drag(self, event):
        if self.settings.pinned:
            return
        x = self.root.winfo_x() + event.x - self._drag_data["x"]
        y = self.root.winfo_y() + event.y - self._drag_data["y"]
        self.root.geometry(f"+{x}+{y}")
        self.settings.x, self.settings.y = x, y

    # -- match lifecycle -------------------------------------------------

    def _on_game_data(self, data: Dict) -> None:
        self._last_snapshot = data
        enemies = GameDataManager.parse_enemies(data)
        for enemy in enemies:
            self.enemy_data_cache[enemy["champ"]] = enemy

        if not self.game_active:
            if not enemies:
                return
            if self.demo_mode:
                self.demo_mode = False
                self._clear_rows()
            log.info("Match found (%d enemies).", len(enemies))
            self._build_enemy_rows(enemies)
            self._show_overlay()
            self.game_active = True
            self._check_display_mode()
            self.publish(sync.MSG_HELLO, {})
            self.rune_lookup.start(GameDataManager.player_ids(data))
            return

        self._refresh_haste(enemies)

    def _on_match_ended(self) -> None:
        if not self.game_active:
            return
        log.info("Match ended.")
        self.root.withdraw()
        self.settings.save()
        self.game_active = False
        self.enemy_data_cache.clear()
        self.cosmic_insight.clear()
        self.rune_status = None
        self._last_snapshot = None
        self._clear_rows()

    def _refresh_haste(self, enemies: List[Dict]) -> None:
        for enemy in enemies:
            champ = enemy["champ"]
            haste = self.get_haste(champ)
            for w in self._by_champ.get(champ, ()):
                w.recalculate(haste)

    def _check_display_mode(self) -> None:
        if self._warned_fullscreen:
            return
        if gamewindow.mode() != gamewindow.FULLSCREEN:
            return
        self._warned_fullscreen = True
        log.warning(
            "League is in Fullscreen. It marks itself topmost there, so the "
            "overlay cannot draw above it and you will not see any timers. "
            "Switch League to Borderless in Settings -> Video.")
        self.tray.notify("Overlay hidden in Fullscreen",
                         "League is set to Fullscreen, so the overlay cannot "
                         "draw over it. Switch to Borderless in Settings -> Video.")

    def call_out(self, champ: str, spell: str, remaining: int) -> None:
        msg = chat.format_message(champ, spell, remaining,
                                  GameDataManager.game_time(self._last_snapshot))
        if not chat.copy(self.root, msg):
            return
        log.info("Copied: %s", msg)
        # The overlay is NOACTIVATE, so League still holds focus here and the
        # typer can go straight into it.
        self.typer.send(msg)

    # -- rows ------------------------------------------------------------

    def _clear_rows(self) -> None:
        # cancel() first: destroying a canvas with a live `after` leaves the
        # callback pointing at a Tcl command that no longer exists.
        for w in self.widgets.values():
            w.cancel()
        for widget in self.enemies_frame.winfo_children():
            widget.destroy()
        self._img_refs.clear()
        self.widgets.clear()
        self._by_champ.clear()
        self.champ_icons.clear()
        self.enemy_order.clear()
        self.enemy_spells.clear()
        self._current_enemies = []

    def _build_enemy_rows(self, enemies: List[Dict]) -> None:
        self._clear_rows()
        if not enemies:
            return
        self._current_enemies = list(enemies)

        size = Config.icon_size()
        for i, enemy in enumerate(enemies):
            champ = enemy["champ"]
            if i > 0 and Config.SHOW_SEPARATOR:
                sep = tk.Frame(self.enemies_frame, bg=Config.COLOR_SEPARATOR, height=1)
                sep.pack(fill="x", padx=10, pady=2)

            row = tk.Frame(self.enemies_frame, bg=Config.COLOR_BG)
            row.pack(fill="x", pady=Config.scaled(Config.ROW_PADDING_Y))

            champ_icon = AssetManager.load_icon("champions", champ, (size, size),
                                                is_round=True)
            self._img_refs.append(champ_icon)
            portrait = ChampionIcon(row, champ, self, champ_icon)
            portrait.set_flag(self.cosmic_insight.get(champ, False))
            portrait.pack(side="left", padx=(0, Config.scaled(8)))
            self.champ_icons[champ] = portrait
            self.enemy_order.append(champ)
            self.enemy_spells[champ] = [enemy["spell1"], enemy["spell2"]]

            for spell_name in (enemy["spell1"], enemy["spell2"]):
                sw = SpellTimerWidget(row, champ, spell_name, self)
                sw.pack(side="left", padx=Config.scaled(3))
                self.widgets[(champ, spell_name)] = sw
                self._by_champ.setdefault(champ, []).append(sw)
                self._img_refs.append(sw.icon_img)
                self._img_refs.append(sw.dim_img)

    def _rebuild_rows_preserving_timers(self) -> None:
        """Redraw at a new size without throwing away running cooldowns."""
        if not (self.game_active or self.demo_mode):
            return
        snapshot = [
            (champ, spell, w.remaining, w.timer.base_cd, w.timer.haste)
            for (champ, spell), w in self.widgets.items()
            if w.is_active and w.remaining > 0
        ]
        enemies = self._current_enemies
        self._build_enemy_rows(enemies)
        for champ, spell, remaining, base_cd, haste in snapshot:
            w = self.widgets.get((champ, spell))
            if w:
                w.adopt(remaining, base_cd, haste)
        self.root.geometry(f"+{self.settings.x}+{self.settings.y}")

    # -- shutdown --------------------------------------------------------

    def _on_signal(self, signum, frame) -> None:
        log.info("Signal %s received.", signum)
        self._shutdown()

    def _shutdown(self) -> None:
        """The one way out. Safe to call twice."""
        if self._shutting_down:
            return
        self._shutting_down = True
        log.info("Quitting.")
        self.settings.save()
        for name, stop in (("poller", self.game_poller.stop),
                           ("sync", self.sync.stop),
                           ("hotkeys", self.hotkeys.stop),
                           ("typer", self.typer.stop),
                           ("tray", self.tray.stop)):
            try:
                stop()
            except Exception as e:
                log.warning("Stopping %s failed: %s", name, e)
        try:
            self.root.quit()
        except Exception:
            pass

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            self._shutdown()
            try:
                self.root.destroy()
            except Exception:
                pass


def main() -> int:
    checker = SingleInstanceChecker()
    if checker.is_already_running():
        return 0

    applog.setup(Config.LOG_FILE)
    log.info("--- %s %s starting ---", APP_NAME, __version__)

    cooldowns = SpellCooldowns()
    cooldowns.apply_cached()
    cooldowns.refresh_async()

    settings = Settings.load()
    log.info("Loaded: %s", settings.describe())

    try:
        app = OverlayApp(settings)
        app.run()
    except Exception:
        log.exception("Fatal error")
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
