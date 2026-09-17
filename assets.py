"""Loading champion and spell icons, with a placeholder when one is missing."""

from __future__ import annotations
import logging
import os
from typing import Tuple

from PIL import Image, ImageDraw, ImageOps, ImageTk

from config import resource_path

log = logging.getLogger("assets")


def _placeholder(name: str, size: Tuple[int, int]) -> Image.Image:
    img = Image.new("RGBA", size, "#222")
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, size[0] - 1, size[1] - 1], outline="#555")
    draw.text((size[0] // 2, size[1] // 2), (name or "??")[:2],
              fill="#888", anchor="mm")
    return img


class AssetManager:
    @staticmethod
    def load_icon(folder: str, name: str, size: Tuple[int, int],
                  is_round: bool = False) -> ImageTk.PhotoImage:
        path = resource_path(os.path.join("assets", folder, name + ".png"))
        try:
            img = Image.open(path).convert("RGBA")
        except Exception as e:
            # Not just a missing file: an interrupted download leaves a
            # truncated PNG, and letting that propagate takes out the whole
            # row build and with it the overlay.
            if not isinstance(e, FileNotFoundError):
                log.warning("Could not load %s/%s.png (%s); using a placeholder.",
                            folder, name, e)
            img = _placeholder(name, size)

        img = img.resize(size, Image.Resampling.LANCZOS)
        if is_round:
            mask = Image.new("L", size, 0)
            ImageDraw.Draw(mask).ellipse((0, 0) + size, fill=255)
            img = ImageOps.fit(img, mask.size, centering=(0.5, 0.5))
            img.putalpha(mask)
        return ImageTk.PhotoImage(img)

    @staticmethod
    def create_dim_layer(size: Tuple[int, int]) -> ImageTk.PhotoImage:
        return ImageTk.PhotoImage(Image.new("RGBA", size, (0, 0, 0, 180)))
