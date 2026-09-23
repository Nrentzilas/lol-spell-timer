"""A once-per-launch check for a newer release on GitHub.

Nothing is downloaded or installed. The exe is unsigned and people run it from
wherever they saved it, so replacing it behind their back would be both
fragile and rude. The check only says a new version exists and where to get
it; the user decides.
"""

from __future__ import annotations
import logging
import queue
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

import requests

from version import APP_NAME, __version__

log = logging.getLogger("updates")

REPO = "Nrentzilas/lol-spell-timer"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"

_VERSION_RE = re.compile(r"^\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?\s*$", re.IGNORECASE)


def parse_version(text: Optional[str]) -> Optional[Tuple[int, int, int]]:
    """'v1.4.2' -> (1, 4, 2). None for anything that is not a plain release."""
    m = _VERSION_RE.match(text or "")
    if not m:
        return None
    return tuple(int(part or 0) for part in m.groups())


def is_newer(latest: Optional[str], current: str = __version__) -> bool:
    new, cur = parse_version(latest), parse_version(current)
    return new is not None and cur is not None and new > cur


class UpdateChecker:
    """Asks GitHub once, on its own thread; the app polls for the answer.

    A result is posted only when there is something to say, so a failed or
    rate-limited check is silent -- an offline player should not be told
    anything about it.
    """

    def __init__(self, enabled: bool = True, current: str = __version__):
        self.enabled = enabled
        self.current = current
        self.inbox: "queue.Queue[Dict[str, str]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if not self.enabled or self._thread:
            return
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="updates")
        self._thread.start()

    def poll(self) -> List[Dict[str, str]]:
        out = []
        while True:
            try:
                out.append(self.inbox.get_nowait())
            except queue.Empty:
                return out

    def _run(self) -> None:
        release = self.fetch()
        if release is None:
            return
        if is_newer(release["version"], self.current):
            log.info("%s %s is available (running %s): %s", APP_NAME,
                     release["version"], self.current, release["url"])
            self.inbox.put(release)
        else:
            log.info("Up to date (%s).", self.current)

    def fetch(self) -> Optional[Dict[str, str]]:
        try:
            resp = requests.get(
                LATEST_URL, timeout=5,
                headers={"Accept": "application/vnd.github+json",
                         "User-Agent": f"{APP_NAME}/{self.current}"})
        except Exception as e:
            log.debug("Update check failed: %s", e)
            return None
        if resp.status_code != 200:
            log.debug("Update check got HTTP %s.", resp.status_code)
            return None
        try:
            data: Dict[str, Any] = resp.json()
        except ValueError:
            return None
        return self.parse_release(data)

    @staticmethod
    def parse_release(data: Any) -> Optional[Dict[str, str]]:
        if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
            return None
        tag = (data.get("tag_name") or "").strip()
        if parse_version(tag) is None:
            return None
        url = data.get("html_url") or RELEASES_PAGE
        if not str(url).startswith("https://github.com/"):
            url = RELEASES_PAGE
        return {"version": tag.lstrip("vV"), "url": url}
