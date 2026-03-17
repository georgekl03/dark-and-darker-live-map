#!/usr/bin/env python3
"""
map_scanner_v2.py  –  Robust Dungeon-Map Scanner V2 + Standalone Debug Tool
============================================================================
Run as a standalone debug tool:
    python map_scanner_v2.py
    python map_scanner_v2.py  screenshot.png   # load an image directly

Also importable as a module:
    from map_scanner_v2 import MapScannerV2

How it works
------------
1. **Find map bbox** – scan the screenshot for a large dark rectangle (the
   in-game dungeon-map overlay) using a sliding-scale dark-area heuristic.

2. **Detect micro-grid** – every module tile contains a faint 10×10 sub-cell
   grid.  The scanner computes the Sobel edge magnitude of the map crop, sums
   edge values along rows / columns to obtain 1-D profiles, then applies an
   FFT to find the dominant periodic spacing.  Both the micro-cell period and
   the module period are searched; whichever gives a grid count closest to an
   integer in [2, 10] wins.

3. **Infer module tile grid** – module period × N = map width → grid is N cols
   wide (and similarly for rows).

4. **Edge-based template matching** – for each tile, extract the patch, resize
   to 64 × 64, compute Sobel edges, then compare against every known module
   template (all 4 × 90° rotations) using normalised edge MSE (NMSE).  Raw
   greyscale NMSE is avoided because it is sensitive to lighting; edge NMSE
   is stable across brightness and contrast differences.

5. **Greedy unique assignment** – sort all (tile, module, score) triples by
   score, then greedily assign: each module key can be assigned to at most
   one tile, and each tile to at most one module.  Tiles with no candidate
   below the match threshold are marked unknown.

Dependencies: Pillow  (numpy strongly recommended for speed)
"""

from __future__ import annotations

import json
import math
import sys
import threading
from pathlib import Path
from typing import Optional

try:
    import tkinter as tk
    from tkinter import filedialog, ttk
    _HAVE_TK = True
except ImportError:
    tk = None           # type: ignore[assignment]
    filedialog = None   # type: ignore[assignment]
    ttk = None          # type: ignore[assignment]
    _HAVE_TK = False

try:
    from PIL import Image, ImageDraw, ImageFilter
    _HAVE_PIL = True
except ImportError:
    _HAVE_PIL = False

try:
    from PIL import ImageTk as _ImageTk
    _HAVE_IMAGETK = True
except ImportError:
    _ImageTk = None   # type: ignore[assignment]
    _HAVE_IMAGETK = False

try:
    import numpy as np
    _HAVE_NUMPY = True
except ImportError:
    _HAVE_NUMPY = False

try:
    from PIL import ImageGrab
    _HAVE_IMAGEGRAB = True
except ImportError:
    _HAVE_IMAGEGRAB = False

try:
    import mss as _mss_mod
    _HAVE_MSS = True
except ImportError:
    _HAVE_MSS = False

# ── Paths ────────────────────────────────────────────────
ROOT    = Path(__file__).parent
DATA    = ROOT / "data"
RAW     = DATA / "raw"
MODULES = DATA / "modules"
MANIF   = DATA / "map_manifest.json"
DEBUG   = DATA / "debug"

# ── Scanner constants ────────────────────────────────────
MICRO_CELLS_PER_MODULE = 10   # sub-cells inside one module (always 10×10)
MIN_MICRO_PERIOD  = 2         # minimum micro-cell size in pixels
MAX_MICRO_PERIOD  = 30        # maximum micro-cell size in pixels
MIN_MODULE_PERIOD = 15        # minimum module size in pixels
MAX_MODULE_PERIOD = 500       # maximum module size in pixels
MIN_GRID_SIZE     = 2         # smallest supported grid dimension
MAX_GRID_SIZE     = 10        # largest supported grid dimension
MAP_DARK_THRESH   = 65        # max average brightness of the map area
TEMPLATE_SIZE     = 64        # pixels used for edge comparison
TOP_K             = 5         # top candidates kept per tile
MATCH_THRESHOLD   = 0.40      # edge NMSE distance; below = accept

PNG_SIG = b"\x89PNG\r\n\x1a\n"

# ── UI palette ────────────────────────────────────────────
BG     = "#141414"; PANEL  = "#1c1c1c"; PANEL2 = "#242424"
BORDER = "#303030"; BDR2   = "#424242"; ACCENT = "#c8a84b"
TEXT   = "#e0ddd8"; DIM    = "#808080"; BTN_BG = "#2a2a2a"
BTN_H  = "#3c3c3c"


# ══════════════════════════════════════════════════════════
#  Low-level helpers
# ══════════════════════════════════════════════════════════

def _is_valid_png(p: Path) -> bool:
    try:
        with open(p, "rb") as f:
            return f.read(8) == PNG_SIG
    except Exception:
        return False


def _grab_screen() -> Optional[Image.Image]:
    """Return a full-screen RGB screenshot or None."""
    if _HAVE_MSS:
        try:
            with _mss_mod.mss() as sct:
                mon = sct.monitors[1]
                raw = sct.grab(mon)
                return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        except Exception:
            pass
    if _HAVE_IMAGEGRAB:
        try:
            return ImageGrab.grab()
        except Exception:
            pass
    return None


def _mean_brightness(img: Image.Image) -> float:
    """Return mean pixel value of a greyscale PIL image."""
    if _HAVE_NUMPY:
        return float(np.asarray(img, dtype=np.float32).mean())
    data = list(img.convert("L").getdata())
    return sum(data) / max(len(data), 1)


# ── Edge computation ─────────────────────────────────────

def _sobel_numpy(arr: "np.ndarray") -> "np.ndarray":
    """Simple 3-tap gradient magnitude (requires numpy)."""
    f  = arr.astype(np.float32)
    gx = f[1:-1, 2:] - f[1:-1, :-2]
    gy = f[2:, 1:-1] - f[:-2, 1:-1]
    mag = np.sqrt(gx * gx + gy * gy)
    out = np.zeros(arr.shape, dtype=np.float32)
    out[1:-1, 1:-1] = mag
    return out


def _compute_edges(img: Image.Image):
    """Return edge magnitude as numpy array or PIL Image (fallback)."""
    if _HAVE_NUMPY:
        return _sobel_numpy(np.array(img.convert("L"), dtype=np.float32))
    return img.convert("L").filter(ImageFilter.FIND_EDGES)


# ── Period detection ─────────────────────────────────────

def _detect_period_numpy(profile: "np.ndarray", min_p: int, max_p: int
                          ) -> "tuple[int, float]":
    """Find dominant period in *profile* via FFT (requires numpy)."""
    n = len(profile)
    if n < min_p * 2:
        return min_p, 0.0
    centered = profile.astype(float) - profile.mean()
    # Pad to next power of two for efficiency
    n2 = 1
    while n2 < n:
        n2 <<= 1
    fft_mag = np.abs(np.fft.rfft(centered, n=n2))
    freqs   = np.fft.rfftfreq(n2)
    # Map period range → frequency range
    min_freq = 1.0 / max_p
    max_freq = 1.0 / max(min_p, 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        periods_arr = np.where(freqs > 0, 1.0 / freqs, np.inf)
    mask = (periods_arr >= min_p) & (periods_arr <= max_p)
    if not mask.any():
        return min_p, 0.0
    best_i     = int(np.argmax(fft_mag[mask]))
    peak_freq  = float(freqs[mask][best_i])
    period     = int(round(1.0 / peak_freq)) if peak_freq > 0 else min_p
    period     = max(min_p, min(max_p, period))
    return period, float(fft_mag[mask][best_i])


def _detect_period_pil(profile: list, min_p: int, max_p: int
                        ) -> "tuple[int, float]":
    """Pure-Python autocorrelation period detection (PIL fallback)."""
    n    = len(profile)
    mean = sum(profile) / max(n, 1)
    c    = [x - mean for x in profile]
    best_score, best_p = -1e18, min_p
    for p in range(min_p, min(max_p + 1, n // 2 + 1)):
        score = sum(c[i] * c[i + p] for i in range(n - p)) / max(n - p, 1)
        if score > best_score:
            best_score, best_p = score, p
    return best_p, best_score


def _find_best_offset_numpy(profile: "np.ndarray", period: int) -> int:
    """Find the phase offset with the highest comb-sum."""
    if period <= 0:
        return 0
    best, best_o = -1e18, 0
    for o in range(period):
        idx = np.arange(o, len(profile), period)
        s   = float(profile[idx].sum())
        if s > best:
            best, best_o = s, o
    return best_o


def _find_best_offset_pil(profile: list, period: int) -> int:
    if period <= 0:
        return 0
    best, best_o = -1e18, 0
    for o in range(period):
        s = sum(profile[i] for i in range(o, len(profile), period))
        if s > best:
            best, best_o = s, o
    return best_o


# ── Edge similarity metric ───────────────────────────────

def _edge_nmse(a, b) -> float:
    """
    Normalised MSE between two edge images (lower = more similar).
    Each image is max-normalised before comparison to remove brightness bias.
    Accepts either numpy arrays or PIL Images.
    """
    if _HAVE_NUMPY:
        an = a.astype(np.float32)
        bn = b.astype(np.float32)
        an = an / (float(an.max()) + 1e-8)
        bn = bn / (float(bn.max()) + 1e-8)
        return float(np.mean((an - bn) ** 2))
    # PIL fallback
    if not isinstance(a, list):
        a = list(a.getdata())
    if not isinstance(b, list):
        b = list(b.getdata())
    n   = len(a)
    if n == 0:
        return 1.0
    mx_a = max(a) or 1
    mx_b = max(b) or 1
    return sum(((x / mx_a - y / mx_b) ** 2) for x, y in zip(a, b)) / n


# ══════════════════════════════════════════════════════════
#  MapScannerV2
# ══════════════════════════════════════════════════════════

class MapScannerV2:
    """Robust dungeon-map scanner based on micro-grid periodicity detection.

    Parameters
    ----------
    map_name : str
        Name of the map whose module templates should be loaded (e.g. "Cave").
        Pass an empty string to skip template loading (grid detection still
        works; just no module classification).
    """

    # Tuneable config (can be overridden per instance after construction)
    micro_cells   = MICRO_CELLS_PER_MODULE
    min_micro     = MIN_MICRO_PERIOD
    max_micro     = MAX_MICRO_PERIOD
    min_module    = MIN_MODULE_PERIOD
    max_module    = MAX_MODULE_PERIOD
    dark_thresh   = MAP_DARK_THRESH
    tmpl_size     = TEMPLATE_SIZE
    top_k         = TOP_K
    match_thr     = MATCH_THRESHOLD

    def __init__(self, map_name: str = ""):
        self.map_name     = map_name
        self._tmpl_cache: dict = {}   # {map_name: {mk: [edge0, edge90, edge180, edge270]}}

    # ── Public API ────────────────────────────────────────

    def scan_screen(self) -> dict:
        """Take a screenshot and run the full pipeline."""
        img = _grab_screen()
        if img is None:
            return {"ok": False, "error": "Screen capture failed – install mss or Pillow[ImageGrab]"}
        result = self._pipeline(img)
        result["from_screen"] = True
        return result

    def scan_image(self, src) -> dict:
        """Run the full pipeline on a saved image or PIL Image object.

        Parameters
        ----------
        src : str | Path | PIL.Image.Image
        """
        try:
            if isinstance(src, (str, Path)):
                img = Image.open(src).convert("RGB")
            else:
                img = src.convert("RGB")
        except Exception as e:
            return {"ok": False, "error": f"Could not load image: {e}"}
        result = self._pipeline(img)
        result["from_screen"] = False
        return result

    # ── Pipeline ─────────────────────────────────────────

    def _pipeline(self, image: Image.Image) -> dict:
        result: dict = {"ok": False, "image_size": image.size}

        # Stage 1: locate map area
        bbox, bbox_cands = self._find_map_bbox(image)
        result["map_bbox"]        = bbox
        result["bbox_candidates"] = bbox_cands
        if bbox is None:
            result["error"] = (
                "Could not detect the dungeon map area.\n"
                "Make sure the in-game map overlay is open and fully visible."
            )
            return result

        x, y, w, h = bbox
        map_img = image.crop((x, y, x + w, y + h)).convert("L")
        result["map_image"] = map_img

        # Stage 2: micro-grid
        micro = self._detect_microgrid(map_img)
        result["microgrid"] = micro

        # Stage 3: module grid
        grid = self._infer_grid(map_img, micro)
        result["grid"] = grid

        # Stage 4: classify tiles (only when a map name is set)
        if self.map_name:
            templates = self._load_templates(self.map_name)
            if templates:
                tile_matches, layout = self._classify_tiles(map_img, grid, templates)
            else:
                tile_matches, layout = {}, {}
                result["warning"] = (
                    f"No module templates found for map '{self.map_name}'.\n"
                    "Run dad_downloader.py to download map PNG tiles first."
                )
        else:
            tile_matches, layout = {}, {}
            result["warning"] = "No map name set – grid detected but tiles not classified."

        result["tile_matches"] = tile_matches
        result["layout"]       = layout
        result["ok"]           = True
        return result

    # ── Stage 1: Map bbox ──────────────────────────────────

    def _find_map_bbox(self, image: Image.Image) -> "tuple[Optional[tuple], list]":
        """Return ((x,y,w,h), candidates_list) for the dungeon-map region."""
        sw, sh = image.size
        gray   = image.convert("L")

        # Restrict search to the inner 90 % (skip taskbar / window chrome)
        sx0, sy0 = int(sw * 0.05), int(sh * 0.05)
        sx1, sy1 = int(sw * 0.95), int(sh * 0.95)
        sw2, sh2 = sx1 - sx0, sy1 - sy0

        candidates: list = []  # each entry: (x, y, w, h, avg_brightness)

        fracs = [0.90, 0.85, 0.80, 0.75, 0.70, 0.65, 0.60, 0.55,
                 0.50, 0.45, 0.40, 0.35, 0.30, 0.25, 0.20]
        for frac in fracs:
            side = int(min(sw2, sh2) * frac)
            if side < 32:
                continue
            cx = sx0 + (sw2 - side) // 2
            cy = sy0 + (sh2 - side) // 2
            patch = gray.crop((cx, cy, cx + side, cy + side))
            avg   = _mean_brightness(patch)
            candidates.append((cx, cy, side, side, avg))
            if avg < self.dark_thresh:
                return (cx, cy, side, side), candidates

        return None, candidates

    # ── Stage 2: Micro-grid detection ─────────────────────

    def _detect_microgrid(self, map_img: Image.Image) -> dict:
        """Detect micro-grid spacing and alignment from the cropped map image."""
        W, H = map_img.size

        if _HAVE_NUMPY:
            arr    = np.array(map_img, dtype=np.float32)
            edges  = _sobel_numpy(arr)
            proj_x = edges.sum(axis=0)    # shape (W,)
            proj_y = edges.sum(axis=1)    # shape (H,)

            # Detect module period (dominant large period)
            mod_px, mod_sx = _detect_period_numpy(proj_x, self.min_module,
                                                   min(self.max_module, W // 2))
            mod_py, mod_sy = _detect_period_numpy(proj_y, self.min_module,
                                                   min(self.max_module, H // 2))
            # Detect micro-cell period (dominant small period)
            mic_px, mic_sx = _detect_period_numpy(proj_x, self.min_micro, self.max_micro)
            mic_py, mic_sy = _detect_period_numpy(proj_y, self.min_micro, self.max_micro)

            step_x, micro_step_x = self._resolve_period(
                W, mod_px, mod_sx, mic_px, mic_sx)
            step_y, micro_step_y = self._resolve_period(
                H, mod_py, mod_sy, mic_py, mic_sy)

            offset_x = _find_best_offset_numpy(proj_x, max(1, micro_step_x))
            offset_y = _find_best_offset_numpy(proj_y, max(1, micro_step_y))

            return {
                "module_step_x": step_x,
                "module_step_y": step_y,
                "micro_step_x":  micro_step_x,
                "micro_step_y":  micro_step_y,
                "offset_x":      offset_x,
                "offset_y":      offset_y,
                # Debug data
                "_proj_x": proj_x,
                "_proj_y": proj_y,
                "_edges":  edges,
            }

        # ── PIL fallback ──────────────────────────────────
        edges_pil = map_img.filter(ImageFilter.FIND_EDGES)
        W2, H2    = edges_pil.size
        pixels    = list(edges_pil.getdata())
        proj_x2: list = [0.0] * W2
        proj_y2: list = [0.0] * H2
        for r in range(H2):
            for c in range(W2):
                v = pixels[r * W2 + c]
                proj_x2[c] += v
                proj_y2[r] += v

        mod_px, mod_sx = _detect_period_pil(proj_x2, self.min_module,
                                             min(self.max_module, W2 // 2))
        mod_py, mod_sy = _detect_period_pil(proj_y2, self.min_module,
                                             min(self.max_module, H2 // 2))
        mic_px, _      = _detect_period_pil(proj_x2, self.min_micro, self.max_micro)
        mic_py, _      = _detect_period_pil(proj_y2, self.min_micro, self.max_micro)

        step_x, micro_step_x = self._resolve_period(W2, mod_px, mod_sx, mic_px, 0.0)
        step_y, micro_step_y = self._resolve_period(H2, mod_py, mod_sy, mic_py, 0.0)

        offset_x = _find_best_offset_pil(proj_x2, max(1, micro_step_x))
        offset_y = _find_best_offset_pil(proj_y2, max(1, micro_step_y))

        return {
            "module_step_x": step_x,
            "module_step_y": step_y,
            "micro_step_x":  micro_step_x,
            "micro_step_y":  micro_step_y,
            "offset_x":      offset_x,
            "offset_y":      offset_y,
            "_proj_x": proj_x2,
            "_proj_y": proj_y2,
            "_edges":  None,
        }

    def _resolve_period(self, size: int,
                        mod_p: int, mod_score: float,
                        mic_p: int, mic_score: float
                        ) -> "tuple[int, int]":
        """Choose between module-period and micro-period interpretations.

        Returns (module_step, micro_step) as integers in pixels.
        """
        def grid_quality(p: int) -> float:
            """How well does size / p map to an integer in [MIN, MAX]?"""
            if p <= 0:
                return 0.0
            gs = size / p
            if gs < MIN_GRID_SIZE or gs > MAX_GRID_SIZE:
                return 0.0
            return 1.0 - abs(gs - round(gs))

        q_mod = grid_quality(mod_p)
        q_mic = grid_quality(mic_p * self.micro_cells)

        if q_mod >= q_mic:
            return mod_p, max(1, round(mod_p / self.micro_cells))
        else:
            return mic_p * self.micro_cells, mic_p

    # ── Stage 3: Module grid ───────────────────────────────

    def _infer_grid(self, map_img: Image.Image, micro: dict) -> dict:
        """Divide the map image into a regular grid of module tiles."""
        W, H   = map_img.size
        step_x = max(1, micro.get("module_step_x") or W)
        step_y = max(1, micro.get("module_step_y") or H)

        n_cols = max(1, min(MAX_GRID_SIZE, round(W / step_x)))
        n_rows = max(1, min(MAX_GRID_SIZE, round(H / step_y)))
        cell_w = W / n_cols
        cell_h = H / n_rows

        tiles = [
            {
                "row": r, "col": c,
                "x":   int(c * cell_w),
                "y":   int(r * cell_h),
                "w":   int((c + 1) * cell_w) - int(c * cell_w),
                "h":   int((r + 1) * cell_h) - int(r * cell_h),
            }
            for r in range(n_rows)
            for c in range(n_cols)
        ]
        return {
            "n_rows": n_rows, "n_cols": n_cols,
            "cell_w": cell_w, "cell_h": cell_h,
            "tiles":  tiles,
        }

    # ── Stage 4: Templates ─────────────────────────────────

    def _load_templates(self, map_name: str) -> dict:
        """Load and cache edge-templates for *map_name*.

        Returns {module_key: [edge_arr_0°, edge_arr_90°, edge_arr_180°, edge_arr_270°]}.
        """
        if map_name in self._tmpl_cache:
            return self._tmpl_cache[map_name]

        mod_dir = MODULES / map_name
        if not mod_dir.is_dir():
            self._tmpl_cache[map_name] = {}
            return {}

        sz        = self.tmpl_size
        templates: dict = {}
        for png in mod_dir.glob("*.png"):
            if not _is_valid_png(png):
                continue
            try:
                base = Image.open(png).convert("L")
            except Exception:
                continue
            rots = []
            for deg in (0, 90, 180, 270):
                rot  = base.rotate(deg, expand=True).resize((sz, sz), Image.LANCZOS)
                rots.append(_compute_edges(rot))
            templates[png.stem] = rots

        self._tmpl_cache[map_name] = templates
        return templates

    # ── Stage 5: Tile classification ──────────────────────

    def _classify_tiles(self, map_img: Image.Image,
                        grid: dict, templates: dict
                        ) -> "tuple[dict, dict]":
        """Match each tile against all templates.

        Returns
        -------
        tile_matches : {(row, col): [(mk, rot_deg, score), ...]}
            Top-K candidates per tile, sorted best-first.
        layout : {(row, col): {"module": mk, "rot": rot_deg, "score": score}}
            Unique greedy assignment result.
        """
        sz    = self.tmpl_size
        tiles = grid["tiles"]

        tile_matches: dict = {}

        for tile in tiles:
            key   = (tile["row"], tile["col"])
            patch = map_img.crop((tile["x"], tile["y"],
                                  tile["x"] + tile["w"],
                                  tile["y"] + tile["h"]))
            patch       = patch.resize((sz, sz), Image.LANCZOS)
            patch_edges = _compute_edges(patch)

            candidates: list = []
            for mk, rot_edges in templates.items():
                for rot_i, tmpl_edge in enumerate(rot_edges):
                    score = _edge_nmse(patch_edges, tmpl_edge)
                    candidates.append((mk, rot_i * 90, score))

            candidates.sort(key=lambda t: t[2])
            tile_matches[key] = candidates[: self.top_k]

        layout = self._assign_unique(tile_matches)
        return tile_matches, layout

    def _assign_unique(self, tile_matches: dict) -> dict:
        """Greedy unique module assignment across all tiles."""
        pool: list = []
        for tile_key, cands in tile_matches.items():
            for mk, rot, score in cands:
                pool.append((score, tile_key, mk, rot))
        pool.sort(key=lambda t: t[0])

        used_tiles: set = set()
        used_mks:   set = set()
        layout:     dict = {}

        for score, tile_key, mk, rot in pool:
            if tile_key in used_tiles or mk in used_mks:
                continue
            if score > self.match_thr:
                continue
            layout[tile_key] = {"module": mk, "rot": rot, "score": score}
            used_tiles.add(tile_key)
            used_mks.add(mk)

        return layout


# ══════════════════════════════════════════════════════════
#  Debug overlay helpers
# ══════════════════════════════════════════════════════════

def _rgba(image: Image.Image) -> Image.Image:
    return image.convert("RGBA")


def draw_bbox_overlay(image: Image.Image, bbox: Optional[tuple],
                      candidates: list) -> Image.Image:
    """Highlight the detected map bbox (and all candidates) on the full image."""
    out  = _rgba(image)
    draw = ImageDraw.Draw(out)

    # Draw all candidates in dim yellow
    for cand in candidates:
        cx, cy, cw, ch, avg = cand
        alpha = 120
        draw.rectangle([cx, cy, cx + cw, cy + ch],
                       outline=(200, 180, 60, alpha), width=2)

    # Highlight the best (accepted) bbox in bright green
    if bbox:
        x, y, w, h = bbox
        draw.rectangle([x, y, x + w, y + h],
                       outline=(80, 230, 80, 240), width=4)
        draw.text((x + 6, y + 6), "Detected map area", fill=(80, 230, 80, 240))

    del draw
    return out


def draw_edge_overlay(edges, map_size: tuple) -> Image.Image:
    """Convert edge magnitude array to a visible greyscale image."""
    if _HAVE_NUMPY and isinstance(edges, np.ndarray):
        # Normalise to 0-255
        mx = float(edges.max()) or 1.0
        arr_u8 = (edges / mx * 255).clip(0, 255).astype(np.uint8)
        return Image.fromarray(arr_u8, mode="L").convert("RGBA")
    # PIL fallback: edges is already a PIL Image
    if edges is not None:
        return edges.convert("RGBA")
    return Image.new("RGBA", map_size, (0, 0, 0, 255))


def draw_microgrid_overlay(map_img: Image.Image, micro: dict) -> Image.Image:
    """Draw micro-grid lines (both axes) on the map crop."""
    out  = _rgba(map_img)
    draw = ImageDraw.Draw(out)
    W, H = map_img.size

    micro_step_x = micro.get("micro_step_x", 0)
    micro_step_y = micro.get("micro_step_y", 0)
    offset_x     = micro.get("offset_x", 0)
    offset_y     = micro.get("offset_y", 0)
    module_step_x = micro.get("module_step_x", 0)
    module_step_y = micro.get("module_step_y", 0)

    # Vertical micro-grid lines (faint)
    if micro_step_x > 0:
        x = offset_x
        while x < W:
            is_mod = (module_step_x > 0
                      and abs((x - offset_x) % module_step_x) < micro_step_x)
            color = (180, 100, 230, 200) if is_mod else (100, 60, 140, 90)
            w     = 2 if is_mod else 1
            draw.line([(int(x), 0), (int(x), H)], fill=color, width=w)
            x += micro_step_x

    # Horizontal micro-grid lines (faint)
    if micro_step_y > 0:
        y = offset_y
        while y < H:
            is_mod = (module_step_y > 0
                      and abs((y - offset_y) % module_step_y) < micro_step_y)
            color = (180, 100, 230, 200) if is_mod else (100, 60, 140, 90)
            w     = 2 if is_mod else 1
            draw.line([(0, int(y)), (W, int(y))], fill=color, width=w)
            y += micro_step_y

    del draw
    return out


def draw_grid_overlay(map_img: Image.Image, grid: dict) -> Image.Image:
    """Draw module tile boundaries on the map crop."""
    out  = _rgba(map_img)
    draw = ImageDraw.Draw(out)

    for tile in grid.get("tiles", []):
        x0, y0 = tile["x"], tile["y"]
        x1, y1 = x0 + tile["w"], y0 + tile["h"]
        draw.rectangle([x0, y0, x1, y1], outline=(200, 180, 60, 200), width=2)
        draw.text((x0 + 4, y0 + 4),
                  f"{tile['row']},{tile['col']}",
                  fill=(200, 180, 60, 200))

    del draw
    return out


def draw_matches_overlay(map_img: Image.Image, grid: dict,
                         layout: dict) -> Image.Image:
    """Colour tiles by match quality (green=matched, grey=unknown)."""
    out  = _rgba(map_img)
    draw = ImageDraw.Draw(out)

    for tile in grid.get("tiles", []):
        key  = (tile["row"], tile["col"])
        x0, y0 = tile["x"], tile["y"]
        x1, y1 = x0 + tile["w"], y0 + tile["h"]
        if key in layout:
            info  = layout[key]
            score = info["score"]
            # green if score is good, yellow-ish if borderline
            g = int(max(60, min(220, 220 - score * 300)))
            color = (60, g, 60, 160)
            label = f"{info['module'][:12]}\n{info['rot']}° {score:.3f}"
        else:
            color = (60, 60, 60, 100)
            label = "?"
        draw.rectangle([x0 + 1, y0 + 1, x1 - 1, y1 - 1],
                       outline=color, width=2)
        draw.text((x0 + 4, y0 + 4), label, fill=color)

    del draw
    return out


# ══════════════════════════════════════════════════════════
#  Standalone debug GUI  (requires tkinter + Pillow)
# ══════════════════════════════════════════════════════════

if _HAVE_TK:
    def flat_btn(parent, text, cmd, fg=TEXT, bg=BTN_BG,
                 font=("Segoe UI", 9), padx=10, pady=4, width=None):
        kw = dict(text=text, command=cmd, bg=bg, fg=fg, bd=0, font=font,
                  padx=padx, pady=pady, activebackground=BTN_H,
                  activeforeground=TEXT, relief="flat", cursor="hand2")
        if width:
            kw["width"] = width
        return tk.Button(parent, **kw)

    _TkBase = tk.Tk
else:
    def flat_btn(*a, **kw):  # type: ignore[misc]
        raise RuntimeError("tkinter is not available")

    _TkBase = object  # type: ignore[assignment, misc]


class ScannerV2App(_TkBase):  # type: ignore[misc]
    """Standalone debug GUI for MapScannerV2.

    Steps viewable via the View dropdown:
      original  – raw loaded/captured image
      bbox      – detected map bbox overlaid on full image
      edges     – edge magnitude image of the map crop
      microgrid – map crop with micro-grid lines
      grid      – map crop with module tile boundaries
      matches   – map crop with per-tile match result
    """

    _STEPS = [
        ("original",  "Original"),
        ("bbox",      "Map BBox"),
        ("edges",     "Edge Image"),
        ("microgrid", "Micro-Grid"),
        ("grid",      "Module Grid"),
        ("matches",   "Tile Matches"),
    ]

    def __init__(self, preload_path: Optional[Path] = None):
        super().__init__()
        self.title("D&D Map Scanner V2  –  Debug Tool")
        self.configure(bg=BG)
        self.geometry("1360x820")
        self.minsize(900, 600)

        self._img_orig:   Optional[Image.Image] = None
        self._result:     Optional[dict]        = None
        self._step_imgs:  dict[str, Image.Image] = {}
        self._tkimgs:     list = []
        self._step_var    = tk.StringVar(value="original")
        self._map_var     = tk.StringVar(value="")
        self._sel_tile:   Optional[tuple] = None  # (row, col)
        self._cv_scale    = 1.0
        self._cv_off      = (0, 0)   # (dx, dy) of image top-left in canvas

        # Map/view scale info (used for tile-click hit-testing)
        self._disp_img_size: tuple = (1, 1)   # (w, h) of the displayed image

        self._manifest: dict = {}
        self._load_manifest()
        self._build()

        if preload_path and preload_path.exists():
            self.after(100, lambda: self._load_file(str(preload_path)))

    # ── Manifest ──────────────────────────────────────────

    def _load_manifest(self):
        if MANIF.exists():
            try:
                with open(MANIF) as f:
                    self._manifest = json.load(f)
            except Exception:
                pass
        # Pick first map with local template data
        for name in self._manifest:
            if (MODULES / name).is_dir():
                self._map_var.set(name)
                break

    # ── Build UI ──────────────────────────────────────────

    def _build(self):
        # Toolbar
        tb = tk.Frame(self, bg=PANEL2, pady=6)
        tb.pack(fill="x")

        flat_btn(tb, "📂  Load Image",    self._load_image).pack(side="left", padx=6)
        flat_btn(tb, "📷  Screenshot",    self._do_screenshot).pack(side="left", padx=2)
        tk.Frame(tb, bg=BDR2, width=1).pack(side="left", fill="y", padx=8)
        flat_btn(tb, "🚀  Run Pipeline",  self._run_pipeline,
                 fg="#111", bg=ACCENT).pack(side="left", padx=2)
        tk.Frame(tb, bg=BDR2, width=1).pack(side="left", fill="y", padx=8)
        flat_btn(tb, "💾  Save Debug",    self._save_debug,
                 fg=DIM, bg=BTN_BG).pack(side="left", padx=2)

        # Map selector
        tk.Frame(tb, bg=BDR2, width=1).pack(side="left", fill="y", padx=8)
        tk.Label(tb, text="Map:", bg=PANEL2, fg=DIM,
                 font=("Segoe UI", 9)).pack(side="left", padx=(4, 2))
        map_names = [n for n in self._manifest
                     if (MODULES / n).is_dir()]
        cb = ttk.Combobox(tb, textvariable=self._map_var,
                          values=map_names, state="readonly",
                          font=("Segoe UI", 9), width=20)
        cb.pack(side="left")

        # Step selector
        tk.Frame(tb, bg=BDR2, width=1).pack(side="left", fill="y", padx=8)
        tk.Label(tb, text="View:", bg=PANEL2, fg=DIM,
                 font=("Segoe UI", 9)).pack(side="left", padx=(4, 2))
        step_cb = ttk.Combobox(tb, textvariable=self._step_var,
                               values=[s[0] for s in self._STEPS],
                               state="readonly", font=("Segoe UI", 9), width=12)
        step_cb.pack(side="left")
        step_cb.bind("<<ComboboxSelected>>", lambda _: self._show_step())

        # Status
        self._status_var = tk.StringVar(value="Load an image or take a screenshot to begin.")
        tk.Label(tb, textvariable=self._status_var, bg=PANEL2, fg=DIM,
                 font=("Segoe UI", 9), padx=12, anchor="w",
                 wraplength=400).pack(side="left", fill="x", expand=True)

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        # Main content (canvas | info panel)
        main = tk.PanedWindow(self, orient="horizontal", bg=BG,
                              sashwidth=4, sashrelief="flat")
        main.pack(fill="both", expand=True)

        cv_frame = tk.Frame(main, bg=BG)
        main.add(cv_frame, minsize=600)
        self._cv = tk.Canvas(cv_frame, bg=BG, bd=0, highlightthickness=0,
                             cursor="crosshair")
        self._cv.pack(fill="both", expand=True)
        self._cv.bind("<Configure>", lambda _: self._show_step())
        self._cv.bind("<Button-1>",  self._on_canvas_click)

        # Info / log panel
        info_frame = tk.Frame(main, bg=PANEL, width=340)
        info_frame.pack_propagate(False)
        main.add(info_frame, minsize=220)

        tk.Label(info_frame, text="Pipeline Info", bg=PANEL, fg=ACCENT,
                 font=("Segoe UI", 10, "bold"), padx=10, pady=8).pack(anchor="w")
        tk.Frame(info_frame, bg=BORDER, height=1).pack(fill="x")

        # Selected tile details
        self._tile_lbl = tk.Label(info_frame, text="Click a tile on the grid/matches view",
                                  bg=PANEL, fg=DIM, font=("Segoe UI", 9),
                                  padx=8, pady=4, anchor="w", wraplength=320)
        self._tile_lbl.pack(fill="x")
        tk.Frame(info_frame, bg=BORDER, height=1).pack(fill="x")

        # Log area
        self._log = tk.Text(info_frame, bg=PANEL2, fg=TEXT, bd=0,
                            font=("Consolas", 8), wrap="word",
                            state="disabled", highlightthickness=0)
        sb = tk.Scrollbar(info_frame, command=self._log.yview)
        self._log["yscrollcommand"] = sb.set
        self._log.pack(fill="both", expand=True, padx=4, pady=4)

        flat_btn(info_frame, "Clear Log", self._clear_log,
                 fg=DIM, bg=PANEL, padx=8, pady=3,
                 font=("Segoe UI", 8)).pack(anchor="e", padx=4, pady=4)

    # ── Actions ───────────────────────────────────────────

    def _load_image(self):
        path = filedialog.askopenfilename(
            title="Open image",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.tiff"),
                       ("All", "*.*")])
        if path:
            self._load_file(path)

    def _load_file(self, path: str):
        try:
            self._img_orig = Image.open(path).convert("RGB")
        except Exception as e:
            self._status("Error loading file: " + str(e))
            return
        self._result = None
        self._step_imgs.clear()
        self._step_imgs["original"] = self._img_orig.copy()
        self._step_var.set("original")
        self._show_step()
        self._log_msg(f"Loaded: {Path(path).name}  {self._img_orig.size}")
        self._status(f"Image loaded: {Path(path).name}")

    def _do_screenshot(self):
        if not (_HAVE_MSS or _HAVE_IMAGEGRAB):
            self._status("Install mss or Pillow[ImageGrab] for screenshots.")
            return
        self._status("Taking screenshot…")
        self.update_idletasks()
        img = _grab_screen()
        if img is None:
            self._status("Screenshot failed.")
            return
        self._img_orig = img
        self._result = None
        self._step_imgs.clear()
        self._step_imgs["original"] = img.copy()
        self._step_var.set("original")
        self._show_step()
        self._log_msg(f"Screenshot taken: {img.size}")
        self._status(f"Screenshot: {img.size[0]}×{img.size[1]}")

    def _run_pipeline(self):
        if self._img_orig is None:
            self._status("Load an image or take a screenshot first.")
            return
        map_name = self._map_var.get()
        self._log_msg(f"\n=== Pipeline run  map={map_name or '(none)'} ===")
        self._status("Running pipeline…")
        self.update_idletasks()

        scanner = MapScannerV2(map_name=map_name)
        result  = scanner.scan_image(self._img_orig)
        self._result = result

        self._build_step_images(result)
        self._log_result(result)
        self._step_var.set("matches" if "matches" in self._step_imgs else "grid")
        self._show_step()
        ok = result.get("ok", False)
        self._status("Pipeline complete." if ok else
                     "Pipeline failed: " + result.get("error", "unknown error"))

    def _build_step_images(self, result: dict):
        img = self._img_orig
        if img is None:
            return
        self._step_imgs["original"] = img.copy()

        bbox      = result.get("map_bbox")
        bbox_cands = result.get("bbox_candidates", [])
        self._step_imgs["bbox"] = draw_bbox_overlay(img, bbox, bbox_cands)

        map_img = result.get("map_image")
        if map_img is None:
            return

        micro = result.get("microgrid", {})
        grid  = result.get("grid", {})

        edges_data = micro.get("_edges")
        if edges_data is not None:
            self._step_imgs["edges"] = draw_edge_overlay(edges_data, map_img.size)
        else:
            self._step_imgs["edges"] = draw_edge_overlay(None, map_img.size)

        self._step_imgs["microgrid"] = draw_microgrid_overlay(map_img, micro)
        self._step_imgs["grid"]      = draw_grid_overlay(map_img, grid)

        layout = result.get("layout", {})
        self._step_imgs["matches"] = draw_matches_overlay(map_img, grid, layout)

    def _log_result(self, result: dict):
        if not result.get("ok"):
            self._log_msg("  ✗  " + result.get("error", "Pipeline failed"))
            return

        bbox = result.get("map_bbox")
        if bbox:
            self._log_msg(f"  ✓  Map bbox: x={bbox[0]} y={bbox[1]} "
                          f"w={bbox[2]} h={bbox[3]}")

        micro = result.get("microgrid", {})
        self._log_msg(
            f"  Micro-grid: step_x={micro.get('micro_step_x')}  "
            f"step_y={micro.get('micro_step_y')}")
        self._log_msg(
            f"  Module step: x={micro.get('module_step_x')}  "
            f"y={micro.get('module_step_y')}  "
            f"offset=({micro.get('offset_x')}, {micro.get('offset_y')})")

        grid = result.get("grid", {})
        self._log_msg(
            f"  Grid: {grid.get('n_cols')}×{grid.get('n_rows')} tiles  "
            f"(cell {grid.get('cell_w', 0):.1f}×{grid.get('cell_h', 0):.1f} px)")

        layout = result.get("layout", {})
        if layout:
            self._log_msg(f"  Matched {len(layout)} tiles:")
            for (r, c), info in sorted(layout.items()):
                self._log_msg(
                    f"    ({r},{c}) → {info['module']}  "
                    f"rot={info['rot']}°  score={info['score']:.4f}")
        elif result.get("warning"):
            self._log_msg("  ⚠  " + result["warning"])
        else:
            self._log_msg("  (no tiles matched – check templates and threshold)")

    # ── Canvas & tile interaction ──────────────────────────

    def _show_step(self):
        step = self._step_var.get()
        img  = self._step_imgs.get(step) or self._step_imgs.get("original")
        if img is None:
            self._cv.delete("all")
            self._cv.create_text(
                max(1, self._cv.winfo_width())  // 2,
                max(1, self._cv.winfo_height()) // 2,
                text="No image loaded.\nUse the toolbar to load an image.",
                fill=DIM, font=("Segoe UI", 11), justify="center")
            return

        cw = max(1, self._cv.winfo_width())
        ch = max(1, self._cv.winfo_height())
        iw, ih = img.size
        scale  = min(cw / iw, ch / ih, 1.0)
        nw     = max(1, int(iw * scale))
        nh     = max(1, int(ih * scale))
        ox     = (cw - nw) // 2
        oy     = (ch - nh) // 2

        disp = img.resize((nw, nh), Image.LANCZOS).convert("RGBA")
        ti   = _ImageTk.PhotoImage(disp)
        self._tkimgs = [ti]
        self._cv.delete("all")
        self._cv.create_image(ox, oy, anchor="nw", image=ti)

        # Store mapping info for click hit-testing
        self._cv_scale = scale
        self._cv_off   = (ox, oy)
        self._disp_img_size = (nw, nh)

    def _on_canvas_click(self, event):
        """Select a tile when the user clicks in grid/matches view."""
        step = self._step_var.get()
        if step not in ("grid", "matches") or self._result is None:
            return
        grid    = self._result.get("grid", {})
        map_img = self._result.get("map_image")
        if not grid or map_img is None:
            return

        ox, oy = self._cv_off
        scale  = self._cv_scale
        # Convert canvas coords to image coords
        img_x = (event.x - ox) / scale
        img_y = (event.y - oy) / scale

        # Find which tile was clicked
        for tile in grid.get("tiles", []):
            if (tile["x"] <= img_x < tile["x"] + tile["w"] and
                    tile["y"] <= img_y < tile["y"] + tile["h"]):
                self._sel_tile = (tile["row"], tile["col"])
                self._show_tile_info(tile["row"], tile["col"])
                break

    def _show_tile_info(self, row: int, col: int):
        result = self._result
        if result is None:
            return
        tile_matches = result.get("tile_matches", {})
        layout       = result.get("layout", {})
        key          = (row, col)

        lines = [f"Tile ({row}, {col})"]
        if key in layout:
            info = layout[key]
            lines.append(
                f"  Assigned: {info['module']}  rot={info['rot']}°  "
                f"score={info['score']:.4f}")
        else:
            lines.append("  Unassigned (no match below threshold)")

        cands = tile_matches.get(key, [])
        if cands:
            lines.append(f"\nTop {len(cands)} candidates:")
            for mk, rot, score in cands:
                lines.append(f"  {mk:<30} rot={rot:3}° score={score:.4f}")

        self._tile_lbl.config(text="\n".join(lines))

    # ── Save debug images ─────────────────────────────────

    def _save_debug(self):
        if not self._step_imgs:
            self._status("Nothing to save – run the pipeline first.")
            return
        try:
            DEBUG.mkdir(parents=True, exist_ok=True)
            for step, img in self._step_imgs.items():
                path = DEBUG / f"v2_{step}.png"
                img.convert("RGBA").save(path)
            self._log_msg(f"  Saved {len(self._step_imgs)} debug images → {DEBUG}")
            self._status(f"Saved to {DEBUG}")
        except Exception as e:
            self._status(f"Save failed: {e}")

    # ── Log helpers ───────────────────────────────────────

    def _log_msg(self, msg: str):
        self._log.configure(state="normal")
        self._log.insert("end", msg + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")

    def _clear_log(self):
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")

    def _status(self, msg: str):
        self._status_var.set(msg)


# ══════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════

if __name__ == "__main__":
    if not _HAVE_PIL:
        print("ERROR: Pillow is required.  Run:  pip install Pillow")
        raise SystemExit(1)
    if not _HAVE_TK:
        print("ERROR: tkinter is required for the debug GUI.")
        raise SystemExit(1)

    preload: Optional[Path] = None
    if len(sys.argv) > 1:
        preload = Path(sys.argv[1])

    app = ScannerV2App(preload_path=preload)
    app.mainloop()
