"""Single source of truth for the version, so a log file identifies its build."""

from __future__ import annotations

APP_NAME = "Spell Timer"
__version__ = "1.3.0"


def banner() -> str:
    return f"{APP_NAME} {__version__}"
