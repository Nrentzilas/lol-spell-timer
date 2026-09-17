"""The small settings prompts the tray menu opens.

One at a time: a second Toplevel behind the first, both -topmost, is a good way
to lose a dialog you cannot see. Every one of these must be built on the Tk
thread, so the tray asks the app to open one and the app does it from its poll
loop.
"""

from __future__ import annotations
import logging
import tkinter as tk
from typing import Callable, Optional

from config import Config

log = logging.getLogger("dialogs")

ENTRY_BG = "#10203C"
LABEL_FG = "#CDBE91"
BUTTON_BG = "#1E3A5F"
BUTTON_ACTIVE = "#2A5080"
CANCEL_BG = "#2A2A2A"


class DialogHost:
    def __init__(self, root: tk.Misc):
        self.root = root
        self._current: Optional[tk.Toplevel] = None

    def open_input(self, title: str, label: str, hint: str, initial: str,
                   on_save: Callable[[str], None],
                   position: tuple = (0, 0)) -> None:
        if self._current is not None:
            try:
                self._current.lift()
                self._current.focus_force()
                return
            except Exception:
                self._current = None

        dlg = tk.Toplevel(self.root)
        self._current = dlg
        dlg.title(title)
        dlg.configure(bg=Config.COLOR_BG)
        dlg.resizable(False, False)
        dlg.attributes("-topmost", True)
        dlg.geometry("+{}+{}".format(position[0] - 120, position[1] + 40))

        tk.Label(dlg, text=label, bg=Config.COLOR_BG, fg=LABEL_FG,
                 font=(Config.FONT_FAMILY, 9)).pack(padx=14, pady=(14, 6))

        entry = tk.Entry(dlg, width=38, font=(Config.FONT_FAMILY, 10),
                         justify="center", bg=ENTRY_BG, fg="#FFFFFF",
                         insertbackground="#FFFFFF", relief="flat")
        entry.insert(0, initial or "")
        entry.pack(padx=14, pady=(0, 4))
        entry.focus_force()

        tk.Label(dlg, text=hint, bg=Config.COLOR_BG, fg=Config.COLOR_HANDLE,
                 font=(Config.FONT_FAMILY, 8)).pack(padx=14, pady=(0, 10))

        def close():
            self._current = None
            try:
                dlg.destroy()
            except Exception:
                pass

        def save():
            value = entry.get()
            close()
            try:
                on_save(value)
            except Exception:
                log.exception("Saving '%s' failed", title)

        btns = tk.Frame(dlg, bg=Config.COLOR_BG)
        btns.pack(padx=14, pady=(0, 14))
        tk.Button(btns, text="Save", command=save, relief="flat", bg=BUTTON_BG,
                  fg="#FFFFFF", activebackground=BUTTON_ACTIVE,
                  activeforeground="#FFFFFF", width=10, bd=0).pack(side="left", padx=4)
        tk.Button(btns, text="Cancel", command=close, relief="flat", bg=CANCEL_BG,
                  fg="#BBBBBB", activebackground="#3A3A3A",
                  activeforeground="#FFFFFF", width=10, bd=0).pack(side="left", padx=4)

        entry.bind("<Return>", lambda e: save())
        dlg.bind("<Escape>", lambda e: close())
        dlg.protocol("WM_DELETE_WINDOW", close)

    def close(self) -> None:
        if self._current is not None:
            try:
                self._current.destroy()
            except Exception:
                pass
            self._current = None
