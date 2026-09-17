"""Rotating file logging, since the shipped build has no console."""

from __future__ import annotations
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

_FORMAT = "%(asctime)s %(levelname)-7s %(name)-8s %(message)s"
_DATEFMT = "%H:%M:%S"


def setup(log_file: str, level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(level)
    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    try:
        os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file, maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except Exception:
        pass

    if sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        root.addHandler(stream)

    # Library chatter, kept out of a log people are asked to send in.
    for noisy in ("urllib3", "PIL", "paho"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
