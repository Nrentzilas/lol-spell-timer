"""The system tray icon and its menu.

pystray runs its own thread, so nothing here touches tkinter. Menu items hand
an action name back to the app, which acts on it from the Tk thread.
"""

from __future__ import annotations
import logging
import os
from typing import Callable

import pystray
from PIL import Image

from config import Config, resource_path
from version import APP_NAME, __version__

log = logging.getLogger("tray")

# Action names the app is expected to understand.
ROOM = "room"
APIKEY = "apikey"
HOTKEYS = "hotkeys"
RESET_POSITION = "reset_position"
TOGGLE_SEND = "toggle_send"
TOGGLE_PASTE = "toggle_paste"
TOGGLE_DEMO = "toggle_demo"
TOGGLE_SOUND = "toggle_sound"
OPEN_LOG = "open_log"
QUIT = "quit"
SCALE = "scale"


def _load_image() -> Image.Image:
    for candidate in (resource_path("ico/icon.ico"),
                      resource_path("assets/spells/SummonerFlash.png")):
        if not os.path.exists(candidate):
            continue
        try:
            return Image.open(candidate)
        except Exception as e:
            log.debug("Tray image %s unusable: %s", candidate, e)
    return Image.new("RGB", (64, 64), color=(255, 255, 0))


class Tray:
    """Wraps pystray so the app only deals in action names.

    ``state`` is read live by the checkmarks; pystray calls those predicates
    each time the menu opens, so nothing has to be pushed at it.
    """

    def __init__(self, on_action: Callable[[str, object], None], state):
        self.on_action = on_action
        self.state = state
        self.icon = pystray.Icon(
            "SpellTimer", _load_image(), f"{APP_NAME} {__version__}",
            self._build_menu())

    def _act(self, name, value=None):
        def handler(icon, item):
            try:
                self.on_action(name, value)
            except Exception:
                log.exception("Tray action '%s' failed", name)
        return handler

    def _scale_menu(self) -> pystray.Menu:
        def item(value):
            return pystray.MenuItem(
                f"{value:g}x",
                self._act(SCALE, value),
                checked=lambda _i, v=value: abs(self.state.ui_scale - v) < 0.01,
                radio=True)
        return pystray.Menu(*(item(v) for v in Config.UI_SCALE_CHOICES))

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem("Duo Sync / Room Code...", self._act(ROOM)),
            pystray.MenuItem("Riot API Key (rune auto-detect)...", self._act(APIKEY)),
            pystray.MenuItem("Hotkeys...", self._act(HOTKEYS)),
            pystray.MenuItem("Overlay size", self._scale_menu()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Send call-outs to chat (middle-click)",
                             self._act(TOGGLE_SEND),
                             checked=lambda _i: self.state.send_callouts),
            pystray.MenuItem("Type clipboard on Ctrl+V (in game)",
                             self._act(TOGGLE_PASTE),
                             checked=lambda _i: self.state.type_on_paste),
            pystray.MenuItem("Beep when a spell comes back up",
                             self._act(TOGGLE_SOUND),
                             checked=lambda _i: self.state.sound_cue),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Show demo rows (to position it)",
                             self._act(TOGGLE_DEMO),
                             checked=lambda _i: self.state.demo_mode),
            pystray.MenuItem("Reset Position", self._act(RESET_POSITION)),
            pystray.MenuItem("Open log file", self._act(OPEN_LOG)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._act(QUIT)),
        )

    def run_detached(self) -> None:
        """Start the tray on its own thread."""
        import threading
        threading.Thread(target=self.icon.run, daemon=True, name="tray").start()

    def refresh(self) -> None:
        """Rebuild the menu so its ticks match the current state.

        pystray does this itself after a menu click. It is needed only when
        something else changes what an item shows -- a match starting turns
        demo rows off, and the tick has to follow.
        """
        try:
            self.icon.update_menu()
        except Exception as e:
            log.debug("Menu refresh failed: %s", e)

    def notify(self, title: str, message: str) -> None:
        try:
            self.icon.notify(message, title)
        except Exception as e:
            log.debug("Tray notification failed: %s", e)

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:
            pass
