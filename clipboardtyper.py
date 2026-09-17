"""Types the clipboard into League's chat, and sends call-outs outright.

Two things live here. Ctrl+V in game types the clipboard out character by
character, because League refuses a paste that came from outside it. On top of
that, send_to_chat drives the whole exchange -- Enter, the text, Enter -- so a
call-out needs no macro software and no second keypress.

Approach from Zeunig/lol_clipboard (MIT): github.com/Zeunig/lol_clipboard

WARNING: the hook callback must never block. Every keystroke on the machine
waits behind it, so blocking there freezes all keyboard input until
Ctrl+Alt+Del. Reading the clipboard and typing happen on the worker thread
for that reason. Do not move either into _callback.
"""

from __future__ import annotations
import ctypes
import logging
import queue
import threading
import time
from ctypes import wintypes
from typing import Optional, Tuple

import gamewindow

log = logging.getLogger("typer")

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_QUIT = 0x0012
VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_V = 0x56
VK_RETURN = 0x0D
LLKHF_INJECTED = 0x10
CF_UNICODETEXT = 13

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008
MAPVK_VK_TO_VSC = 0

# League needs a beat to focus the chat box. Without the first delay it drops
# the opening characters of the message; without the second it sends early.
CHAT_OPEN_DELAY = 0.12
CHAT_SEND_DELAY = 0.05

# A guard against sending an essay, not League's own limit, which is lower.
MAX_CHAT_LEN = 180

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

LRESULT = ctypes.c_ssize_t          # LONG_PTR, not int, on 64-bit

user32.GetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardData.argtypes = (wintypes.UINT,)
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = (wintypes.HANDLE,)
kernel32.GlobalUnlock.argtypes = (wintypes.HANDLE,)
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)

# Without these, lparam (a pointer) overflows a C int on 64-bit and the
# callback raises on every keystroke that is not Ctrl+V.
user32.CallNextHookEx.restype = LRESULT
user32.CallNextHookEx.argtypes = (wintypes.HANDLE, ctypes.c_int,
                                  wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.restype = wintypes.HANDLE


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


_SendInput = user32.SendInput
_SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
_SendInput.restype = wintypes.UINT


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int,
                              wintypes.WPARAM, wintypes.LPARAM)


def _char(code: int, up: bool) -> _INPUT:
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    return _INPUT(type=INPUT_KEYBOARD,
                  u=_INPUTUNION(ki=_KEYBDINPUT(wVk=0, wScan=code,
                                               dwFlags=flags, time=0,
                                               dwExtraInfo=None)))


def type_text(text: str) -> int:
    done = 0
    for ch in text:
        raw = ch.encode("utf-16-le")
        units = [int.from_bytes(raw[i:i + 2], "little")
                 for i in range(0, len(raw), 2)]
        ok = True
        for unit in units:
            arr = (_INPUT * 2)(_char(unit, False), _char(unit, True))
            if _SendInput(2, arr, ctypes.sizeof(_INPUT)) != 2:
                ok = False
                break
        if not ok:
            break
        done += 1
    return done


def _key(vk: int, up: bool) -> _INPUT:
    scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    return _INPUT(type=INPUT_KEYBOARD,
                  u=_INPUTUNION(ki=_KEYBDINPUT(wVk=0, wScan=scan,
                                               dwFlags=flags, time=0,
                                               dwExtraInfo=None)))


def tap(vk: int) -> bool:
    """A press and release of one key.

    Scan codes rather than Unicode: Enter is not a character, and games that
    read raw input want the scan code anyway.
    """
    arr = (_INPUT * 2)(_key(vk, False), _key(vk, True))
    return _SendInput(2, arr, ctypes.sizeof(_INPUT)) == 2


def sanitize(text: str) -> Tuple[str, Optional[str]]:
    """One line of chat-safe text, or the reason it cannot be sent."""
    flat = " ".join((text or "").split())
    if not flat:
        return "", "the clipboard was empty"
    if flat.startswith("/"):
        return "", "it starts with '/', which League reads as a command"
    return flat[:MAX_CHAT_LEN], None


def send_to_chat(text: str) -> int:
    """Open chat, type the message, send it. Never call this from the hook."""
    tap(VK_RETURN)
    time.sleep(CHAT_OPEN_DELAY)
    typed = type_text(text)
    time.sleep(CHAT_SEND_DELAY)
    tap(VK_RETURN)
    return typed


def clipboard_text() -> str:
    if not user32.OpenClipboard(None):
        return ""
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            return ctypes.c_wchar_p(ptr).value or ""
        finally:
            kernel32.GlobalUnlock(handle)
    except Exception:
        return ""
    finally:
        user32.CloseClipboard()


class ClipboardTyper:
    def __init__(self, enabled: bool = False, send_enabled: bool = False):
        self.enabled = enabled
        self.send_enabled = send_enabled
        self._pending: "queue.Queue[Tuple[str, Optional[str]]]" = queue.Queue()
        self._league_hwnd = 0
        self._typing = False
        self._stop = threading.Event()
        self._hook_thread: Optional[threading.Thread] = None
        self._work_thread: Optional[threading.Thread] = None
        self._hook = None
        self._proc = None
        self._tid = 0

    def start(self):
        if self._hook_thread:
            return
        self._stop.clear()
        self._work_thread = threading.Thread(target=self._worker, daemon=True,
                                             name="typer-work")
        self._work_thread.start()
        self._hook_thread = threading.Thread(target=self._run_hook, daemon=True,
                                             name="typer-hook")
        self._hook_thread.start()

    def stop(self):
        self._stop.set()
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        for t in (self._hook_thread, self._work_thread):
            if t:
                t.join(timeout=1.5)
        self._hook_thread = self._work_thread = None

    def _callback(self, code, wparam, lparam):
        if code != 0 or not (self.enabled or self.send_enabled):
            return user32.CallNextHookEx(None, code, wparam, lparam)
        if wparam != WM_KEYDOWN and wparam != WM_SYSKEYDOWN:
            return user32.CallNextHookEx(None, code, wparam, lparam)
        kb = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        if kb.vkCode != VK_V:
            # Injected events are not filtered: a mouse macro's Ctrl+V is one.
            return user32.CallNextHookEx(None, code, wparam, lparam)
        if self._typing:
            return user32.CallNextHookEx(None, code, wparam, lparam)
        if not (user32.GetAsyncKeyState(VK_CONTROL) & 0x8000):
            return user32.CallNextHookEx(None, code, wparam, lparam)
        if not self._league_hwnd or user32.GetForegroundWindow() != self._league_hwnd:
            return user32.CallNextHookEx(None, code, wparam, lparam)
        if user32.GetAsyncKeyState(VK_SHIFT) & 0x8000:
            if not self.send_enabled:
                return user32.CallNextHookEx(None, code, wparam, lparam)
            self._pending.put(("send", None))
            return 1
        if not self.enabled:
            return user32.CallNextHookEx(None, code, wparam, lparam)
        self._pending.put(("paste", None))
        return 1

    def send(self, text: str) -> bool:
        """Queue a chat send. Safe from the UI thread: it does not block."""
        if not self.send_enabled:
            return False
        clean, err = sanitize(text)
        if err:
            log.info("Not sent: %s", err)
            return False
        self._pending.put(("send", clean))
        return True

    def _worker(self):
        while not self._stop.is_set():
            try:
                kind, payload = self._pending.get(timeout=0.5)
            except queue.Empty:
                self._league_hwnd = gamewindow.find()
                continue
            if kind == "paste":
                self._paste()
            else:
                self._send(payload)

    def _paste(self):
        text = clipboard_text()
        if not text:
            log.info("Ctrl+V caught but the clipboard was empty.")
            return
        self._typing = True
        try:
            typed = type_text(text)
        finally:
            self._typing = False
        log.info("Ctrl+V caught, typed %d of %d characters.", typed, len(text))

    def _send(self, text: Optional[str]):
        if text is None:
            text, err = sanitize(clipboard_text())
            if err:
                log.info("Not sent: %s", err)
                return
        # Checked again here rather than trusted from the caller: a click can
        # queue this while League sits behind another window, and the Enter
        # that opens chat would land in that window instead.
        self._league_hwnd = gamewindow.find()
        if not gamewindow.is_foreground(self._league_hwnd):
            log.info("Not sent, League is not the focused window: %s", text)
            return
        self._typing = True
        try:
            typed = send_to_chat(text)
        finally:
            self._typing = False
        log.info("Sent to chat: %s (%d of %d characters).",
                 text, typed, len(text))

    def _run_hook(self):
        self._tid = kernel32.GetCurrentThreadId()
        self._proc = HOOKPROC(self._callback)
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
        if not self._hook:
            log.warning("Keyboard hook could not be installed (error %d).",
                        kernel32.GetLastError())
            return
        log.info("Clipboard hook active (Ctrl+V types, Ctrl+Shift+V sends).")
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
            log.info("Clipboard typer stopped.")
