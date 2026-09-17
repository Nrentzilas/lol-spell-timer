from __future__ import annotations
import os
import sys
import json
import math
import time
import signal
import ctypes
import logging
import threading
import tkinter as tk
from typing import Any, Dict, List, Optional, Tuple
import urllib3
from PIL import Image, ImageTk, ImageDraw, ImageOps
import pystray
import applog
import sync
import runes
import hotkeys
import chat
import gamewindow
import clipboardtyper
from config import Config, resource_path
from gamedata import GameDataManager, GamePoller, SpellCooldowns, MATCH_ENDED

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = logging.getLogger("app")


class SingleInstanceChecker:
    def __init__(self, app_name="Global\\LoLSpellTimer_v9_5"):
        self.mutex_name = app_name
        self.mutex = None

    def is_already_running(self):
        kernel32 = ctypes.windll.kernel32
        self.mutex = kernel32.CreateMutexW(None, False, self.mutex_name)
        last_error = kernel32.GetLastError()
        # ERROR_ALREADY_EXISTS = 183
        if last_error == 183:
            return True
        return False


class Win32Utils:
    GWL_EXSTYLE = -20
    WS_EX_NOACTIVATE = 0x08000000
    WS_EX_TOPMOST = 0x00000008

    HWND_TOPMOST = -1
    SW_SHOWNOACTIVATE = 4
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOACTIVATE = 0x0010
    SWP_SHOWWINDOW = 0x0040

    SM_XVIRTUALSCREEN = 76
    SM_YVIRTUALSCREEN = 77
    SM_CXVIRTUALSCREEN = 78
    SM_CYVIRTUALSCREEN = 79

    @staticmethod
    def virtual_screen() -> Tuple[int, int, int, int]:
        try:
            get = ctypes.windll.user32.GetSystemMetrics
            w = get(Win32Utils.SM_CXVIRTUALSCREEN)
            h = get(Win32Utils.SM_CYVIRTUALSCREEN)
            if w and h:
                return (get(Win32Utils.SM_XVIRTUALSCREEN),
                        get(Win32Utils.SM_YVIRTUALSCREEN), w, h)
        except Exception:
            pass
        return (0, 0, 1920, 1080)

    @staticmethod
    def set_no_focus(hwnd: int):
        try:
            style = ctypes.windll.user32.GetWindowLongW(hwnd, Win32Utils.GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(
                hwnd,
                Win32Utils.GWL_EXSTYLE,
                style | Win32Utils.WS_EX_NOACTIVATE | Win32Utils.WS_EX_TOPMOST
            )
        except Exception:
            pass

    @staticmethod
    def hwnd_of(window) -> int:
        window.update_idletasks()
        return ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()

    @staticmethod
    def show_no_activate(hwnd: int):
        try:
            u = ctypes.windll.user32
            u.ShowWindow(hwnd, Win32Utils.SW_SHOWNOACTIVATE)
            u.SetWindowPos(hwnd, Win32Utils.HWND_TOPMOST, 0, 0, 0, 0,
                           Win32Utils.SWP_NOMOVE | Win32Utils.SWP_NOSIZE |
                           Win32Utils.SWP_NOACTIVATE | Win32Utils.SWP_SHOWWINDOW)
        except Exception:
            pass

    @staticmethod
    def raise_topmost(hwnd: int):
        try:
            ctypes.windll.user32.SetWindowPos(
                hwnd, Win32Utils.HWND_TOPMOST, 0, 0, 0, 0,
                Win32Utils.SWP_NOMOVE | Win32Utils.SWP_NOSIZE | Win32Utils.SWP_NOACTIVATE)
        except Exception:
            pass


class AssetManager:
    @staticmethod
    def load_icon(folder: str, name: str, size: Tuple[int, int], is_round: bool = False) -> ImageTk.PhotoImage:
        path = resource_path(os.path.join("assets", folder, name + ".png"))
        try:
            img = Image.open(path).convert("RGBA")
        except FileNotFoundError:
            img = Image.new("RGBA", size, "#222")
            draw = ImageDraw.Draw(img)
            draw.rectangle([0,0, size[0]-1, size[1]-1], outline="#555")
            text = name[:2] if name else "??"
            draw.text((size[0]//2, size[1]//2), text, fill="#888", anchor="mm")

        img = img.resize(size, Image.Resampling.LANCZOS)
        if is_round:
            mask = Image.new("L", size, 0)
            draw = ImageDraw.Draw(mask)
            draw.ellipse((0, 0) + size, fill=255)
            img = ImageOps.fit(img, mask.size, centering=(0.5, 0.5))
            img.putalpha(mask)
        return ImageTk.PhotoImage(img)

    @staticmethod
    def create_dim_layer(size: Tuple[int, int]) -> ImageTk.PhotoImage:
        img = Image.new("RGBA", size, (0, 0, 0, 180))
        return ImageTk.PhotoImage(img)


class ChampionIcon(tk.Canvas):
    def __init__(self, parent, champ_name: str, app_ref, image):
        super().__init__(parent, width=Config.ICON_SIZE, height=Config.ICON_SIZE,
                         bg=Config.COLOR_BG, highlightthickness=0)
        self.champ_name = champ_name
        self.app_ref = app_ref
        self.icon_img = image
        self.create_image(0, 0, image=self.icon_img, anchor="nw")

        s = Config.ICON_SIZE
        self.badge_bg = self.create_oval(s - 16, s - 16, s - 1, s - 1,
                                         fill=Config.COLOR_CI_BADGE,
                                         outline=Config.COLOR_TEXT_OUTLINE,
                                         state="hidden")
        self.badge_txt = self.create_text(s - 8, s - 8, text="CI",
                                          fill=Config.COLOR_TEXT_ACTIVE,
                                          font=(Config.FONT_FAMILY, 6, "bold"),
                                          state="hidden")

        self.bind("<Control-Button-1>", self._toggle)
        self.bind("<Control-Button-3>", self._toggle)

    def _toggle(self, event):
        self.app_ref.set_cosmic_insight(self.champ_name)
        return "break"

    def set_flag(self, on: bool):
        state = "normal" if on else "hidden"
        self.itemconfig(self.badge_bg, state=state)
        self.itemconfig(self.badge_txt, state=state)


class SpellTimerWidget(tk.Canvas):
    def __init__(self, parent, champ_name: str, spell_name: str, app_ref):
        super().__init__(parent, width=Config.ICON_SIZE, height=Config.ICON_SIZE,
                         bg=Config.COLOR_BG, highlightthickness=0)
        self.champ_name = champ_name
        self.spell_name = spell_name
        self.app_ref = app_ref
        self.is_active = False
        self.timer_job = None
        self.remaining = 0
        self._deadline = 0.0
        self._base_cd = None
        self._haste_used = 0

        self.icon_img = AssetManager.load_icon("spells", spell_name, (Config.ICON_SIZE, Config.ICON_SIZE))
        self.create_image(0, 0, image=self.icon_img, anchor="nw")

        self.dim_img = AssetManager.create_dim_layer((Config.ICON_SIZE, Config.ICON_SIZE))
        self.dim_id = self.create_image(0, 0, image=self.dim_img, anchor="nw", state="hidden")
        self.text_id = self.create_text(Config.ICON_SIZE//2, Config.ICON_SIZE//2, text="", state="hidden")

        self.warn_ring = self.create_rectangle(
            1, 1, Config.ICON_SIZE - 1, Config.ICON_SIZE - 1,
            outline=Config.COLOR_TEXT_WARN, width=2, state="hidden")

        self.bind("<Button-1>", self._on_left_click)
        self.bind("<Button-3>", self._on_right_click)
        self.bind("<Button-2>", self._on_middle_click)
        self.bind("<MouseWheel>", self._on_scroll)

    def _on_left_click(self, event):
        self.arm()

    def _on_middle_click(self, event):
        self.app_ref.call_out(self.champ_name, self.spell_name,
                              self.remaining if self.is_active else 0)

    def arm(self):
        if self.is_active: return
        key = self.spell_name.lower()
        base_cd = Config.SPELL_TIMERS.get(key, 300)

        current_haste = self.app_ref.get_haste(self.champ_name)

        if current_haste > 0:
            final_cd = base_cd * (100 / (100 + current_haste))
            final_cd = int(final_cd)
            log.info(f"{self.champ_name} ({self.spell_name}): Base {base_cd}s -> Haste {current_haste} -> {final_cd}s")
        else:
            final_cd = base_cd

        self._base_cd = base_cd
        self._haste_used = current_haste
        self._start_timer(final_cd)

    def _on_right_click(self, event):
        if self.is_active: self._reset()

    def _on_scroll(self, event):
        if not self.is_active:
            return "break"
        self.nudge(Config.NUDGE_STEP if event.delta > 0 else -Config.NUDGE_STEP)
        return "break"

    def nudge(self, delta: int, broadcast: bool = True):
        if not self.is_active:
            return
        new_rem = self.remaining + delta
        if self._base_cd is not None:
            new_rem = min(new_rem, self._apply_haste(self._haste_used))
        self.set_remaining(new_rem, broadcast=broadcast)

    def set_remaining(self, remaining: int, broadcast: bool = False):
        if not self.is_active:
            return
        if self.timer_job:
            self.after_cancel(self.timer_job)
            self.timer_job = None
        if remaining <= 0:
            self._reset(broadcast=broadcast)
            return
        self._schedule(remaining)
        if broadcast:
            self.app_ref.broadcast_adjust(self.champ_name, self.spell_name, remaining)

    def _start_timer(self, duration, broadcast: bool = True):
        self.is_active = True
        if not broadcast:
            self._base_cd = None
        self.itemconfig(self.dim_id, state="normal")
        self.itemconfig(self.text_id, state="normal")
        if broadcast:
            self.app_ref.broadcast_start(self.champ_name, self.spell_name, duration)
        self._schedule(duration)

    def _apply_haste(self, haste: int) -> int:
        return int(self._base_cd * (100 / (100 + haste))) if haste > 0 else self._base_cd

    def recalculate(self, new_haste: int):
        if not self.is_active or self._base_cd is None or new_haste == self._haste_used:
            return
        elapsed = self._apply_haste(self._haste_used) - self.remaining
        self._haste_used = new_haste
        new_remaining = self._apply_haste(new_haste) - elapsed

        if self.timer_job:
            self.after_cancel(self.timer_job)
            self.timer_job = None
        if new_remaining <= 0:
            self._reset(broadcast=False)
        else:
            self._schedule(new_remaining)

    def _get_adaptive_font(self, text: str) -> Tuple[str, int, str]:
        length = len(text)
        size = Config.BASE_FONT_SIZE
        if length <= 2: size = Config.BASE_FONT_SIZE + 2
        elif length == 3: size = Config.BASE_FONT_SIZE + 1
        elif length == 4: size = Config.BASE_FONT_SIZE - 1
        elif length >= 5: size = Config.BASE_FONT_SIZE - 3
        return (Config.FONT_FAMILY, size, "bold")

    def _draw_outlined_text(self, text, fill: str = Config.COLOR_TEXT_ACTIVE):
        self.delete("timer_text")
        cx, cy = Config.ICON_SIZE // 2, Config.ICON_SIZE // 2
        font_spec = self._get_adaptive_font(text)
        offsets = [(-1, -1), (0, -1), (1, -1), (-1,  0), (1,  0), (-1,  1), (0,  1), (1,  1)]
        for ox, oy in offsets:
            self.create_text(cx + ox, cy + oy, text=text, font=font_spec, fill=Config.COLOR_TEXT_OUTLINE, tags="timer_text", anchor="center")
        self.create_text(cx, cy, text=text, font=font_spec, fill=fill, tags="timer_text", anchor="center")

    def _schedule(self, duration):
        self._deadline = time.monotonic() + duration
        self._tick()

    def _tick(self):
        self.timer_job = None
        remaining = math.ceil(self._deadline - time.monotonic())
        self.remaining = max(0, remaining)
        if remaining <= 0:
            self._reset(broadcast=False)
            return
        warn = remaining <= Config.WARN_THRESHOLD
        self.itemconfig(self.warn_ring, state="normal" if warn else "hidden")

        m, s = divmod(remaining, 60)
        text = f"{m}:{s:02}" if remaining >= 60 else str(remaining)
        self._draw_outlined_text(
            text, Config.COLOR_TEXT_WARN if warn else Config.COLOR_TEXT_ACTIVE)

        delay = (self._deadline - time.monotonic() - (remaining - 1)) * 1000
        self.timer_job = self.after(int(min(1000, max(20, delay))), self._tick)

    def _reset(self, broadcast: bool = True):
        was_active = self.is_active
        self.is_active = False
        self.remaining = 0
        if self.timer_job: self.after_cancel(self.timer_job)
        self.timer_job = None
        self.delete("timer_text")
        self.itemconfig(self.dim_id, state="hidden")
        self.itemconfig(self.warn_ring, state="hidden")
        if broadcast and was_active:
            self.app_ref.broadcast_reset(self.champ_name, self.spell_name)


class OverlayApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Spell Timer")
        self.root.configure(bg=Config.COLOR_BG)
        self.root.overrideredirect(True)
        self.root.wm_attributes("-topmost", True)
        self.root.wm_attributes("-alpha", Config.GLOBAL_OPACITY)

        self.hwnd = Win32Utils.hwnd_of(self.root)
        Win32Utils.set_no_focus(self.hwnd)
        self._warned_fullscreen = False

        self.game_active = False
        self.enemy_data_cache = {}
        self._img_refs = []
        self.widgets = {}
        self.champ_icons = {}
        self.cosmic_insight = {}
        self.enemy_order = []
        self.enemy_spells = {}

        self.saved_x = 0
        self.saved_y = 0
        self.is_pinned = False
        self.room = ""
        self.broker = Config.SYNC_BROKER
        self.broker_port = Config.SYNC_PORT
        self.riot_api_key = ""
        self.region = ""
        self.hotkey_mod = Config.DEFAULT_HOTKEY_MOD
        self.type_on_paste = False
        self._load_config()

        self.rune_lookup = runes.RuneLookup(self.riot_api_key, self.region, Config.APP_DIR)
        self.rune_status = None

        self.hotkeys = hotkeys.HotkeyManager(self.hotkey_mod)
        self.hotkeys.start()

        self.typer = clipboardtyper.ClipboardTyper(enabled=self.type_on_paste)
        self.typer.start()

        self.sync = sync.SyncClient(self.room, self.broker, self.broker_port)
        self.sync.start()
        self._last_sync_state = None

        self.game_poller = GamePoller()
        self.game_poller.start()
        self._last_snapshot = None
        self._dialog = None
        self._room_dialog_requested = False
        self._apikey_dialog_requested = False
        self._hotkey_dialog_requested = False
        self._reset_position_requested = False

        self._drag_data = {"x": 0, "y": 0}

        self.container = tk.Frame(self.root, bg=Config.COLOR_BORDER, padx=1, pady=1)
        self.container.pack()
        self.inner = tk.Frame(self.container, bg=Config.COLOR_BG, padx=4, pady=4)
        self.inner.pack()

        self.handle = tk.Label(self.inner, text="::::", bg=Config.COLOR_BG, fg=Config.COLOR_HANDLE, font=("Arial", 7, "bold"), cursor="fleur")
        self.handle.pack(fill="x", pady=(0, 2))

        self.handle.bind("<ButtonPress-1>", self._start_drag)
        self.handle.bind("<B1-Motion>", self._do_drag)
        self.handle.bind("<Button-3>", self._toggle_pin)

        self._update_pin_visual()

        self.enemies_frame = tk.Frame(self.inner, bg=Config.COLOR_BG)
        self.enemies_frame.pack()

        self.root.withdraw()

        self._setup_tray()

        signal.signal(signal.SIGINT, self._graceful_exit)

        self._monitor_game_loop()
        self._poll_sync()

    def get_haste(self, champ_name: str) -> int:
        data = self.enemy_data_cache.get(champ_name, {})
        haste = data.get('haste', 0)
        if self.cosmic_insight.get(champ_name):
            haste += runes.COSMIC_INSIGHT_HASTE
        return haste

    def set_cosmic_insight(self, champ: str, value: Optional[bool] = None,
                           broadcast: bool = True):
        current = self.cosmic_insight.get(champ, False)
        new = (not current) if value is None else bool(value)
        if new == current and value is not None:
            return

        self.cosmic_insight[champ] = new
        icon = self.champ_icons.get(champ)
        if icon:
            icon.set_flag(new)

        haste = self.get_haste(champ)
        for (c, _), w in self.widgets.items():
            if c == champ:
                w.recalculate(haste)

        if broadcast:
            self.sync.publish(sync.MSG_CI, {"champ": champ, "on": new})
        log.info(f"{champ}: Cosmic Insight {'ON' if new else 'OFF'} -> {haste} haste")


    def broadcast_start(self, champ: str, spell: str, remaining: int):
        self.sync.publish(sync.MSG_START, {"champ": champ, "spell": spell, "rem": int(remaining)})

    def broadcast_reset(self, champ: str, spell: str):
        self.sync.publish(sync.MSG_RESET, {"champ": champ, "spell": spell})

    def broadcast_adjust(self, champ: str, spell: str, remaining: int):
        self.sync.publish(sync.MSG_ADJUST,
                          {"champ": champ, "spell": spell, "rem": int(remaining)})

    def _poll_sync(self):
        for msg in self.sync.poll():
            try:
                self._handle_sync_message(msg)
            except Exception as e:
                log.warning(f"Bad message ignored: {e}")

        for status, found in self.rune_lookup.poll():
            self._apply_rune_results(status, found)

        for slot, spell_idx in self.hotkeys.poll():
            self._fire_hotkey(slot, spell_idx)

        if self._room_dialog_requested:
            self._room_dialog_requested = False
            self._open_room_dialog()

        if self._apikey_dialog_requested:
            self._apikey_dialog_requested = False
            self._open_apikey_dialog()

        if self._hotkey_dialog_requested:
            self._hotkey_dialog_requested = False
            self._open_hotkey_dialog()

        if self._reset_position_requested:
            self._reset_position_requested = False
            self._reset_position()

        self._update_pin_visual()
        self.root.after(Config.UI_POLL_INTERVAL, self._poll_sync)

    def _handle_sync_message(self, msg: Dict):
        kind = msg.get("type")

        if kind == sync.MSG_START:
            self._apply_remote_start(msg.get("champ"), msg.get("spell"), msg.get("rem"))

        elif kind == sync.MSG_RESET:
            w = self.widgets.get((msg.get("champ"), msg.get("spell")))
            if w and w.is_active:
                w._reset(broadcast=False)

        elif kind == sync.MSG_ADJUST:
            w = self.widgets.get((msg.get("champ"), msg.get("spell")))
            if w and w.is_active:
                try:
                    w.set_remaining(int(msg.get("rem")), broadcast=False)
                except (TypeError, ValueError):
                    pass

        elif kind == sync.MSG_CI:
            champ = msg.get("champ")
            if champ:
                self.set_cosmic_insight(champ, bool(msg.get("on")), broadcast=False)

        elif kind == sync.MSG_HELLO:
            timers = [
                {"champ": c, "spell": s, "rem": w.remaining}
                for (c, s), w in self.widgets.items() if w.is_active and w.remaining > 0
            ]
            ci = [c for c, on in self.cosmic_insight.items() if on]
            if timers or ci:
                self.sync.publish(sync.MSG_STATE, {"timers": timers, "ci": ci})

        elif kind == sync.MSG_STATE:
            for champ in msg.get("ci", []):
                self.set_cosmic_insight(champ, True, broadcast=False)
            for t in msg.get("timers", []):
                self._apply_remote_start(t.get("champ"), t.get("spell"), t.get("rem"))

    def _apply_remote_start(self, champ: Optional[str], spell: Optional[str], remaining: Any):
        w = self.widgets.get((champ, spell))
        if not w:
            return
        try:
            remaining = int(remaining)
        except (TypeError, ValueError):
            return
        if remaining <= 0:
            return
        if w.is_active and w.remaining >= remaining:
            return
        if w.is_active:
            w._reset(broadcast=False)
        w._start_timer(remaining, broadcast=False)
        log.info(f"{champ} {spell} -> {remaining}s (from duo)")

    def _fire_hotkey(self, slot: int, spell_idx: int):
        if not self.game_active or slot >= len(self.enemy_order):
            return
        champ = self.enemy_order[slot]
        spells = self.enemy_spells.get(champ, [])
        if spell_idx >= len(spells):
            return
        w = self.widgets.get((champ, spells[spell_idx]))
        if w:
            w.arm()

    def _apply_rune_results(self, status: str, found: Dict[str, bool]):
        self.rune_status = status
        if status != runes.OK:
            reason = {
                runes.NOT_FOUND: "custom/practice game, or not visible to spectator yet",
                runes.FORBIDDEN: "API key rejected -- dev keys expire every 24h",
                runes.NO_REGION: "region unknown -- set 'region' in config.json",
                runes.ERROR: "lookup failed",
            }.get(status, status)
            log.info(f"No auto-detect ({reason}). Ctrl+click a portrait to set it manually.")
            return

        for champ in self.champ_icons:
            self.set_cosmic_insight(champ, found.get(champ, False), broadcast=True)
        hits = [c for c in self.champ_icons if found.get(c)]
        log.info(f"Cosmic Insight: {', '.join(hits) if hits else 'nobody'}")

    def _setup_tray(self):
        def quit_app(icon, item):
            log.info("Quitting.")
            self._save_config()
            self.game_poller.stop()
            self.sync.stop()
            self.hotkeys.stop()
            icon.stop()
            self.root.quit()
            sys.exit(0)

        def open_room(icon, item):
            self._room_dialog_requested = True

        def open_apikey(icon, item):
            self._apikey_dialog_requested = True

        def open_hotkeys(icon, item):
            self._hotkey_dialog_requested = True

        def reset_position(icon, item):
            self._reset_position_requested = True

        def toggle_type_on_paste(icon, item):
            self.type_on_paste = not self.type_on_paste
            self.typer.enabled = self.type_on_paste
            self._save_config()
            log.info("Type clipboard on Ctrl+V: %s",
                     "on" if self.type_on_paste else "off")

        custom_icon = resource_path("ico/icon.ico")
        flash_icon = resource_path("assets/spells/SummonerFlash.png")

        image = None
        if os.path.exists(custom_icon):
            try:
                image = Image.open(custom_icon)
            except Exception:
                pass

        if image is None and os.path.exists(flash_icon):
            image = Image.open(flash_icon)

        if image is None:
            image = Image.new('RGB', (64, 64), color=(255, 255, 0))

        menu = pystray.Menu(
            pystray.MenuItem("Duo Sync / Room Code...", open_room),
            pystray.MenuItem("Riot API Key (rune auto-detect)...", open_apikey),
            pystray.MenuItem("Hotkeys...", open_hotkeys),
            pystray.MenuItem("Type clipboard on Ctrl+V (in game)",
                             toggle_type_on_paste,
                             checked=lambda item: self.type_on_paste),
            pystray.MenuItem("Reset Position", reset_position),
            pystray.MenuItem("Quit", quit_app)
        )
        self.tray_icon = pystray.Icon("SpellTimer", image, "Spell Timer", menu)

        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _load_config(self):
        if os.path.exists(Config.CONFIG_FILE):
            try:
                with open(Config.CONFIG_FILE, 'r') as f:
                    data = json.load(f)
                    self.saved_x = data.get('x', 0)
                    self.saved_y = data.get('y', 0)
                    self.is_pinned = data.get('pinned', False)
                    self.room = sync.sanitize_room(data.get('room', ''))
                    self.broker = data.get('broker', Config.SYNC_BROKER)
                    self.broker_port = int(data.get('broker_port', Config.SYNC_PORT))
                    self.riot_api_key = (data.get('riot_api_key', '') or '').strip()
                    self.region = (data.get('region', '') or '').strip()
                    self.hotkey_mod = (data.get('hotkey_mod', Config.DEFAULT_HOTKEY_MOD) or '').strip()
                    self.type_on_paste = bool(data.get('type_on_paste', False))
                    log.info(f"Loaded: Pos({self.saved_x},{self.saved_y}), Pinned({self.is_pinned}), "
                             f"Room({self.room or 'none'}), RuneKey({'set' if self.riot_api_key else 'none'}), "
                             f"Hotkeys({hotkeys.describe(self.hotkey_mod)})")
            except Exception as e:
                log.warning(f"Load error: {e}")
        else:
            self.saved_x, self.saved_y = self._default_position()

        self.saved_x, self.saved_y = self._clamp_to_screen(self.saved_x, self.saved_y)

    @staticmethod
    def _default_position() -> Tuple[int, int]:
        vx, vy, vw, _ = Win32Utils.virtual_screen()
        return vx + vw - 250, vy + 100

    @staticmethod
    def _clamp_to_screen(x: int, y: int) -> Tuple[int, int]:
        vx, vy, vw, vh = Win32Utils.virtual_screen()
        margin_x, margin_y = 120, 40
        cx = max(vx, min(int(x), vx + vw - margin_x))
        cy = max(vy, min(int(y), vy + vh - margin_y))
        if (cx, cy) != (int(x), int(y)):
            log.warning("Saved position (%s,%s) is off-screen; moved to (%s,%s).",
                        x, y, cx, cy)
        return cx, cy

    def _save_config(self):
        data = {
            'x': self.saved_x,
            'y': self.saved_y,
            'pinned': self.is_pinned,
            'room': self.room,
            'broker': self.broker,
            'broker_port': self.broker_port,
            'riot_api_key': self.riot_api_key,
            'region': self.region,
            'hotkey_mod': self.hotkey_mod,
            'type_on_paste': self.type_on_paste
        }
        try:
            with open(Config.CONFIG_FILE, 'w') as f:
                json.dump(data, f)
            log.info("Settings saved.")
        except Exception as e:
            log.warning(f"Save error: {e}")

    def _graceful_exit(self, signum, frame):
        log.info("Stopping.")
        self._save_config()
        self.game_poller.stop()
        self.sync.stop()
        self.hotkeys.stop()
        self.typer.stop()
        if hasattr(self, 'tray_icon'):
            self.tray_icon.stop()
        self.root.destroy()
        sys.exit(0)

    def _toggle_pin(self, event):
        self.is_pinned = not self.is_pinned
        self._update_pin_visual()
        self._save_config()

    def _update_pin_visual(self):
        state = (self.is_pinned, self.sync.enabled, self.sync.connected)
        if state == self._last_sync_state:
            return
        self._last_sync_state = state

        if self.sync.enabled:
            linked = self.sync.connected
            suffix = "  🔗" if linked else "  ⌛"
            fg = Config.COLOR_SYNCED if linked else Config.COLOR_HANDLE
        else:
            suffix = ""
            fg = Config.COLOR_HANDLE

        if self.is_pinned:
            self.handle.config(text="🔒 PINNED" + suffix, fg=Config.COLOR_PINNED, cursor="arrow")
        else:
            self.handle.config(text="::::" + suffix, fg=fg, cursor="fleur")

    def _open_input_dialog(self, title, label, hint, initial, on_save):
        if getattr(self, '_dialog', None) is not None:
            try:
                self._dialog.lift()
                return
            except Exception:
                pass

        dlg = tk.Toplevel(self.root)
        self._dialog = dlg
        dlg.title(title)
        dlg.configure(bg=Config.COLOR_BG)
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        dlg.geometry(f"+{self.saved_x - 120}+{self.saved_y + 40}")

        tk.Label(dlg, text=label, bg=Config.COLOR_BG, fg="#CDBE91",
                 font=(Config.FONT_FAMILY, 9)).pack(padx=14, pady=(14, 6))

        entry = tk.Entry(dlg, width=38, font=(Config.FONT_FAMILY, 10), justify="center",
                         bg="#10203C", fg="#FFFFFF", insertbackground="#FFFFFF", relief="flat")
        entry.insert(0, initial)
        entry.pack(padx=14, pady=(0, 4))
        entry.focus_force()

        tk.Label(dlg, text=hint, bg=Config.COLOR_BG, fg=Config.COLOR_HANDLE,
                 font=(Config.FONT_FAMILY, 8)).pack(padx=14, pady=(0, 10))

        def close():
            self._dialog = None
            dlg.destroy()

        def save():
            value = entry.get()
            close()
            on_save(value)

        btns = tk.Frame(dlg, bg=Config.COLOR_BG)
        btns.pack(padx=14, pady=(0, 14))
        tk.Button(btns, text="Save", command=save, relief="flat", bg="#1E3A5F", fg="#FFFFFF",
                  activebackground="#2A5080", activeforeground="#FFFFFF", width=10, bd=0).pack(side="left", padx=4)
        tk.Button(btns, text="Cancel", command=close, relief="flat", bg="#2A2A2A", fg="#BBBBBB",
                  activebackground="#3A3A3A", activeforeground="#FFFFFF", width=10, bd=0).pack(side="left", padx=4)

        entry.bind("<Return>", lambda e: save())
        dlg.bind("<Escape>", lambda e: close())
        dlg.protocol("WM_DELETE_WINDOW", close)

    def _open_room_dialog(self):
        def apply(value):
            new_room = sync.sanitize_room(value)
            if new_room != self.room:
                self.room = new_room
                self._save_config()
                self._restart_sync()

        self._open_input_dialog(
            "Duo Sync",
            "Room code (both of you type the same thing):",
            "Leave empty to turn sync off.",
            self.room, apply)

    def _open_apikey_dialog(self):
        def apply(value):
            key = (value or "").strip()
            if key == self.riot_api_key:
                return
            self.riot_api_key = key
            self._save_config()
            self.rune_lookup = runes.RuneLookup(self.riot_api_key, self.region, Config.APP_DIR)
            self.rune_status = None
            log.info(f"API key {'set' if key else 'cleared'}.")
            if self.game_active and self.rune_lookup.enabled and self._last_snapshot:
                self.rune_lookup.start(
                    GameDataManager.player_ids(self._last_snapshot))

        self._open_input_dialog(
            "Riot API Key",
            "Riot API key -- auto-detects enemy Cosmic Insight:",
            "Optional. Get one at developer.riotgames.com (dev keys last 24h).\n"
            "Leave empty to rely on Ctrl+click only.",
            self.riot_api_key, apply)

    def _open_hotkey_dialog(self):
        def apply(value):
            new_mod = (value or "").strip().lower()
            if new_mod == self.hotkey_mod:
                return
            _, err = hotkeys.parse_modifier(new_mod)
            if new_mod and err:
                log.warning(f"Ignored '{new_mod}': {err}.")
                return
            self.hotkey_mod = new_mod
            self._save_config()
            self.hotkeys.stop()
            self.hotkeys = hotkeys.HotkeyManager(self.hotkey_mod)
            self.hotkeys.start()

        self._open_input_dialog(
            "Hotkeys",
            "Modifier for slot hotkeys (<mod>+1-5 arms a spell):",
            "e.g. alt, ctrl, ctrl+alt. Add Shift for the second spell. "
            "Empty turns hotkeys off. Shift alone is not allowed.",
            self.hotkey_mod, apply)

    def _reset_position(self):
        self.saved_x, self.saved_y = self._default_position()
        self.is_pinned = False
        if self.game_active:
            self.root.geometry(f"+{self.saved_x}+{self.saved_y}")
        self._update_pin_visual()
        self._save_config()
        log.info("Position reset to (%s,%s).", self.saved_x, self.saved_y)

    def _restart_sync(self):
        self.sync.stop()
        self.sync = sync.SyncClient(self.room, self.broker, self.broker_port)
        self.sync.start()
        self._last_sync_state = None
        self._update_pin_visual()

    def _monitor_game_loop(self):
        if self.game_active:
            Win32Utils.raise_topmost(self.hwnd)

        item = self.game_poller.poll()

        if item is MATCH_ENDED:
            self._on_match_ended()
        elif item is not None:
            self._on_game_data(item)

        self.root.after(Config.UI_POLL_INTERVAL, self._monitor_game_loop)

    def _on_game_data(self, data: Dict):
        self._last_snapshot = data
        enemies = GameDataManager.parse_enemies(data)
        for enemy in enemies:
            self.enemy_data_cache[enemy['champ']] = enemy

        if not self.game_active:
            if not enemies:
                return
            log.info("Match found (%d enemies).", len(enemies))
            self._build_enemy_rows(enemies)
            self.root.geometry(f"+{self.saved_x}+{self.saved_y}")
            Win32Utils.set_no_focus(self.hwnd)
            Win32Utils.show_no_activate(self.hwnd)
            self.game_active = True
            self._check_display_mode()
            self.sync.publish(sync.MSG_HELLO, {})
            self.rune_lookup.start(GameDataManager.player_ids(data))
            return

        self._refresh_haste(enemies)

    def _check_display_mode(self):
        if self._warned_fullscreen:
            return
        if gamewindow.mode() != gamewindow.FULLSCREEN:
            return
        self._warned_fullscreen = True
        log.warning(
            "League is in Fullscreen. It marks itself topmost there, so the "
            "overlay cannot draw above it and you will not see any timers. "
            "Switch League to Borderless in Settings -> Video.")
        self._notify("Overlay hidden in Fullscreen",
                     "League is set to Fullscreen, so the overlay cannot draw "
                     "over it. Switch to Borderless in Settings -> Video.")

    def _notify(self, title: str, message: str):
        try:
            if getattr(self, "tray_icon", None):
                self.tray_icon.notify(message, title)
        except Exception as e:
            log.debug("Tray notification failed: %s", e)

    def call_out(self, champ: str, spell: str, remaining: int):
        msg = chat.format_message(champ, spell, remaining,
                                  GameDataManager.game_time(self._last_snapshot))
        if not chat.copy(self.root, msg):
            return
        log.info("Copied: %s", msg)

    def _refresh_haste(self, enemies: List[Dict]):
        for enemy in enemies:
            champ = enemy['champ']
            haste = self.get_haste(champ)
            for (c, _), w in self.widgets.items():
                if c == champ:
                    w.recalculate(haste)

    def _on_match_ended(self):
        if not self.game_active:
            return
        log.info("Match ended.")
        self.root.withdraw()
        self._save_config()
        self.game_active = False
        self.enemy_data_cache.clear()
        self.widgets.clear()
        self.champ_icons.clear()
        self.cosmic_insight.clear()
        self.enemy_order.clear()
        self.enemy_spells.clear()
        self.rune_status = None

    def _build_enemy_rows(self, enemies: List[Dict]):
        for widget in self.enemies_frame.winfo_children(): widget.destroy()
        self._img_refs.clear()
        self.widgets.clear()
        self.champ_icons.clear()
        self.enemy_order.clear()
        self.enemy_spells.clear()

        if not enemies: return

        for i, enemy in enumerate(enemies):
            if i > 0 and Config.SHOW_SEPARATOR:
                sep = tk.Frame(self.enemies_frame, bg=Config.COLOR_SEPARATOR, height=1)
                sep.pack(fill="x", padx=10, pady=2)

            row = tk.Frame(self.enemies_frame, bg=Config.COLOR_BG)
            row.pack(fill="x", pady=Config.ROW_PADDING_Y)

            champ_icon = AssetManager.load_icon("champions", enemy['champ'], (Config.ICON_SIZE, Config.ICON_SIZE), is_round=True)
            self._img_refs.append(champ_icon)
            portrait = ChampionIcon(row, enemy['champ'], self, champ_icon)
            portrait.set_flag(self.cosmic_insight.get(enemy['champ'], False))
            portrait.pack(side="left", padx=(0, 8))
            self.champ_icons[enemy['champ']] = portrait
            self.enemy_order.append(enemy['champ'])
            self.enemy_spells[enemy['champ']] = [enemy['spell1'], enemy['spell2']]

            for s_name in [enemy['spell1'], enemy['spell2']]:
                sw = SpellTimerWidget(row, enemy['champ'], s_name, self)
                sw.pack(side="left", padx=3)
                self.widgets[(enemy['champ'], s_name)] = sw
                self._img_refs.append(sw.icon_img)
                self._img_refs.append(sw.dim_img)

    def _apply_native_styles(self):
        hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
        if hwnd == 0: hwnd = self.root.winfo_id()
        Win32Utils.set_no_focus(hwnd)

    def _start_drag(self, event):
        if self.is_pinned: return
        self._drag_data["x"] = event.x
        self._drag_data["y"] = event.y

    def _do_drag(self, event):
        if self.is_pinned: return
        dx = event.x - self._drag_data["x"]
        dy = event.y - self._drag_data["y"]
        x = self.root.winfo_x() + dx
        y = self.root.winfo_y() + dy

        self.root.geometry(f"+{x}+{y}")
        self.saved_x = x
        self.saved_y = y

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    checker = SingleInstanceChecker()
    if checker.is_already_running():
        sys.exit(0)

    applog.setup(Config.LOG_FILE)
    log.info("--- Spell Timer starting ---")

    cooldowns = SpellCooldowns()
    cooldowns.apply_cached()
    cooldowns.refresh_async()

    try:
        app = OverlayApp()
        app.run()
    except Exception:
        log.exception("Fatal error")
        raise
