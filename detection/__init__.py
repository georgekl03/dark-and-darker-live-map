"""detection – Pure-function map detection package for Dark & Darker live map.

Public API
----------
find_map_bbox(image, config=None) -> BboxResult
    Locate the dungeon-map overlay rectangle in a screenshot.

detect_microgrid(map_img, config=None) -> MicrogridResult
    Detect the faint 10×10 micro-grid spacing inside the map crop.

preprocess_for_detection(img, ...) -> dict
    Apply optional preprocessing steps (gamma, CLAHE, unsharp, edges).
"""

from __future__ import annotations

from .bbox import BboxConfig, BboxResult, find_map_bbox
from .microgrid import MicrogridConfig, MicrogridResult, detect_microgrid, search_microgrid_first
from .preprocess import preprocess_for_detection

__all__ = [
    "find_map_bbox",
    "BboxConfig",
    "BboxResult",
    "detect_microgrid",
    "search_microgrid_first",
    "MicrogridConfig",
    "MicrogridResult",
    "preprocess_for_detection",
]
