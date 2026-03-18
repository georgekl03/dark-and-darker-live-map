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

import dataclasses
import json
import math
import sys
import threading
import time
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
    from PIL import Image, ImageDraw, ImageFilter, ImageOps
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
#  ScannerConfig dataclass
# ══════════════════════════════════════════════════════════

@dataclasses.dataclass
class ScannerConfig:
    # BBox
    dark_thresh: int = 65
    bbox_method: str = "mean"       # "mean", "median", "trimmed_mean", "edge"
    search_margin: float = 0.05
    min_frac: float = 0.20
    max_frac: float = 0.90
    frac_step: float = 0.05
    prefer_darkest: bool = False
    # Crop preprocessing
    border_crop_pct: float = 0.0    # 0–0.20: strip this % of map edges before microgrid
    contrast_boost: bool = False    # ImageOps.autocontrast before microgrid
    unsharp_mask: bool = False      # apply unsharp mask before microgrid
    # Microgrid
    min_micro: int = 2
    max_micro: int = 30
    min_module: int = 15
    max_module: int = 500
    micro_cells: int = 10
    force_micro_period: int = 0     # 0 = disabled
    force_module_period: int = 0    # 0 = disabled
    min_grid_size: int = 2
    max_grid_size: int = 10
    # Grid inference
    override_n_rows: int = 0        # 0 = auto
    override_n_cols: int = 0        # 0 = auto
    # Template matching
    match_thr: float = 0.40
    tmpl_size: int = 64
    top_k: int = 5
    unique_assignment: bool = True
    # Bypass bbox
    bypass_bbox: bool = False       # skip bbox stage, use central crop
    bypass_crop_pct: float = 0.80   # how much of screen center to use
    # BBox refinement (edge-snap after seed selection)
    bbox_refine: bool = True
    bbox_refine_band_pct: float = 0.12
    bbox_refine_min_expand_px: int = 0
    bbox_refine_max_expand_pct: float = 0.10
    bbox_refine_edge_quantile: float = 0.85
    # Grid inference scoring weights
    grid_score_step_weight: float = 0.5
    grid_score_line_weight: float = 0.5


DEFAULT_CONFIG = ScannerConfig()


def _config_to_dict(cfg: ScannerConfig) -> dict:
    return dataclasses.asdict(cfg)


def _config_from_dict(d: dict) -> ScannerConfig:
    fields = {f.name for f in dataclasses.fields(ScannerConfig)}
    return ScannerConfig(**{k: v for k, v in d.items() if k in fields})


# ── Settings persistence ──────────────────────────────────

SETTINGS_PATH = DEBUG / "v2_settings.json"


def _save_settings(cfg: ScannerConfig, path: Path = SETTINGS_PATH) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(_config_to_dict(cfg), f, indent=2)
        return True
    except Exception:
        return False


def _load_settings(path: Path = SETTINGS_PATH) -> Optional[ScannerConfig]:
    try:
        with open(path) as f:
            d = json.load(f)
        return _config_from_dict(d)
    except Exception:
        return None


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


def _median_brightness(img: Image.Image) -> float:
    """Return median pixel value of a greyscale PIL image."""
    data = sorted(img.convert("L").getdata())
    n = len(data)
    if n == 0:
        return 0.0
    return (data[n // 2 - 1] + data[n // 2]) / 2 if n % 2 == 0 else float(data[n // 2])


def _trimmed_mean_brightness(img: Image.Image, trim: float = 0.10) -> float:
    """Return trimmed-mean pixel value (trim top/bottom trim fraction)."""
    data = sorted(img.convert("L").getdata())
    n = len(data)
    cut = max(1, int(n * trim))
    trimmed = data[cut: n - cut]
    return sum(trimmed) / max(len(trimmed), 1)


def _edge_density(img: Image.Image) -> float:
    """Return mean edge magnitude (normalized 0-1) as a darkness-proxy."""
    if _HAVE_NUMPY:
        arr = np.array(img.convert("L"), dtype=np.float32)
        edges = _sobel_numpy(arr)
        return float(edges.mean()) / 255.0
    edged = img.convert("L").filter(ImageFilter.FIND_EDGES)
    data = list(edged.getdata())
    return sum(data) / max(len(data), 1) / 255.0


def _brightness_metric(patch_gray: Image.Image, method: str) -> float:
    """Compute a brightness/feature metric for a patch using the given method."""
    if method == "median":
        return _median_brightness(patch_gray)
    if method == "trimmed_mean":
        return _trimmed_mean_brightness(patch_gray)
    if method == "edge":
        return _edge_density(patch_gray)
    # Default: mean
    return _mean_brightness(patch_gray)


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


# ── Grid-line scoring helpers ────────────────────────────

def _grid_line_strength(edges, positions: list, axis: int, band: int = 1) -> float:
    """Sum edge magnitude along candidate grid lines.

    Parameters
    ----------
    edges : np.ndarray or PIL Image
        Edge magnitude image (H×W).
    positions : list of int
        Pixel positions of candidate grid lines along *axis*.
    axis : int
        0 = vertical lines (vary x), 1 = horizontal lines (vary y).
    band : int
        Half-width of the band sampled either side of each line.

    Returns
    -------
    float : total summed edge strength (higher = better alignment).
    """
    if not positions:
        return 0.0

    if _HAVE_NUMPY and isinstance(edges, np.ndarray):
        H, W = edges.shape
        total = 0.0
        for p in positions:
            p = int(round(p))
            lo = max(0, p - band)
            hi = min((W if axis == 0 else H) - 1, p + band) + 1
            if axis == 0:   # vertical line at column p
                total += float(edges[:, lo:hi].sum())
            else:            # horizontal line at row p
                total += float(edges[lo:hi, :].sum())
        return total

    # PIL fallback: convert to pixel list
    if edges is None:
        return 0.0
    try:
        arr = list(edges.convert("L").getdata())
        W2, H2 = edges.size
    except Exception:
        return 0.0
    total = 0.0
    for p in positions:
        p = int(round(p))
        lo = max(0, p - band)
        hi = min((W2 if axis == 0 else H2) - 1, p + band) + 1
        for pp in range(lo, hi):
            if axis == 0:
                for row in range(H2):
                    total += arr[row * W2 + pp]
            else:
                for col in range(W2):
                    total += arr[pp * W2 + col]
    return total


def _best_phase_for_N(edges, step: float, N: int, axis: int,
                       search_radius_px: int = 0) -> "tuple[float, float]":
    """Find the phase offset that maximises grid-line strength.

    Parameters
    ----------
    edges : np.ndarray or PIL Image
    step : float
        Candidate module step size in pixels.
    N : int
        Number of modules along *axis*.
    axis : int
        0 = vertical lines (columns), 1 = horizontal lines (rows).
    search_radius_px : int
        Search ±search_radius_px around phase 0.  When 0, searches the full
        [0, step) range.

    Returns
    -------
    (best_phase, best_score)
    """
    if step <= 0:
        return 0.0, 0.0

    if _HAVE_NUMPY and isinstance(edges, np.ndarray):
        H, W = edges.shape
        dim = W if axis == 0 else H
    elif edges is not None:
        try:
            W2, H2 = edges.size
            dim = W2 if axis == 0 else H2
        except Exception:
            return 0.0, 0.0
    else:
        return 0.0, 0.0

    radius = max(1, int(search_radius_px) if search_radius_px > 0 else int(step))
    best_phase, best_score = 0.0, -1.0

    for offset in range(radius):
        positions = [offset + k * step for k in range(N + 1)
                     if 0 <= offset + k * step <= dim]
        score = _grid_line_strength(edges, positions, axis)
        if score > best_score:
            best_score = score
            best_phase = float(offset)

    return best_phase, best_score


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


def _detect_period_numpy_top10(profile: "np.ndarray", min_p: int, max_p: int
                                ) -> "tuple[int, float, dict]":
    """Like _detect_period_numpy but also returns top-10 period->score dict."""
    n = len(profile)
    if n < min_p * 2:
        return min_p, 0.0, {}
    centered = profile.astype(float) - profile.mean()
    n2 = 1
    while n2 < n:
        n2 <<= 1
    fft_mag = np.abs(np.fft.rfft(centered, n=n2))
    freqs   = np.fft.rfftfreq(n2)
    with np.errstate(divide="ignore", invalid="ignore"):
        periods_arr = np.where(freqs > 0, 1.0 / freqs, np.inf)
    mask = (periods_arr >= min_p) & (periods_arr <= max_p)
    if not mask.any():
        return min_p, 0.0, {}
    masked_mag  = fft_mag[mask]
    masked_per  = periods_arr[mask]
    masked_freq = freqs[mask]
    best_i      = int(np.argmax(masked_mag))
    peak_freq   = float(masked_freq[best_i])
    period      = int(round(1.0 / peak_freq)) if peak_freq > 0 else min_p
    period      = max(min_p, min(max_p, period))
    # Build top-10 dict
    top_idx = np.argsort(masked_mag)[::-1][:10]
    scores: dict = {}
    for i in top_idx:
        pf = float(masked_freq[i])
        p  = int(round(1.0 / pf)) if pf > 0 else min_p
        p  = max(min_p, min(max_p, p))
        scores[p] = float(masked_mag[i])
    return period, float(masked_mag[best_i]), scores


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
    config : ScannerConfig, optional
        Tuning configuration. Defaults to DEFAULT_CONFIG when not provided.
    """

    def __init__(self, map_name: str = "", config: ScannerConfig = None):
        self.map_name     = map_name
        self.config       = config or ScannerConfig()
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
        cfg    = self.config
        result: dict = {
            "ok": False,
            "image_size": image.size,
            "timings": {},
            "bbox_log": [],
            "microgrid_log": [],
            "grid_log": [],
        }

        # Stage 1: locate map area
        t0 = time.perf_counter()

        if cfg.bypass_bbox:
            # Skip bbox detection – use central crop
            sw, sh = image.size
            frac   = max(0.1, min(1.0, cfg.bypass_crop_pct))
            cw     = int(sw * frac)
            ch     = int(sh * frac)
            cx     = (sw - cw) // 2
            cy     = (sh - ch) // 2
            bbox   = (cx, cy, cw, ch)
            bbox_cands: list = []
            bbox_refined: Optional[tuple] = None
            result["bbox_log"].append(
                f"bypass_bbox=True: using central {frac*100:.0f}% crop "
                f"({cx},{cy},{cw}×{ch})")
        else:
            bbox, bbox_cands, bbox_refined = self._find_map_bbox(image, result["bbox_log"])

        result["map_bbox"]          = bbox           # seed (before refinement)
        result["map_bbox_seed"]     = bbox           # explicit seed alias
        result["map_bbox_refined"]  = bbox_refined if not cfg.bypass_bbox else None
        result["bbox_candidates"]   = bbox_cands
        result["timings"]["bbox"]   = time.perf_counter() - t0

        if bbox is None:
            result["error"] = (
                "Could not detect the dungeon map area.\n"
                "Make sure the in-game map overlay is open and fully visible."
            )
            return result

        # Use refined bbox as the active region when available
        active_bbox = bbox_refined if bbox_refined is not None else bbox
        result["map_bbox"] = active_bbox   # final used bbox

        x, y, w, h = active_bbox
        map_img = image.crop((x, y, x + w, y + h)).convert("L")

        # Optional border crop
        if cfg.border_crop_pct > 0:
            bcp = max(0.0, min(0.20, cfg.border_crop_pct))
            mw, mh = map_img.size
            dx = int(mw * bcp)
            dy = int(mh * bcp)
            if dx > 0 or dy > 0:
                map_img = map_img.crop((dx, dy, mw - dx, mh - dy))
                result["bbox_log"].append(
                    f"border_crop_pct={bcp:.2f}: cropped to {map_img.size}")

        # Optional contrast boost
        if cfg.contrast_boost:
            map_img = ImageOps.autocontrast(map_img)

        # Optional unsharp mask
        if cfg.unsharp_mask:
            map_img = map_img.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))

        result["map_image"] = map_img

        # Stage 2: micro-grid
        t1 = time.perf_counter()
        micro = self._detect_microgrid(map_img, result["microgrid_log"])
        result["microgrid"] = micro
        result["timings"]["microgrid"] = time.perf_counter() - t1

        # Stage 3: module grid
        t2 = time.perf_counter()
        grid = self._infer_grid(map_img, micro, result["grid_log"])
        result["grid"] = grid
        result["timings"]["grid"] = time.perf_counter() - t2

        # Stage 4: classify tiles (only when a map name is set)
        t3 = time.perf_counter()
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
        result["timings"]["classify"] = time.perf_counter() - t3
        result["timings"]["total"]    = time.perf_counter() - t0
        result["ok"]           = True
        return result

    # ── Stage 1: Map bbox ──────────────────────────────────

    def _find_map_bbox(self, image: Image.Image,
                       log: list) -> "tuple[Optional[tuple], list, Optional[tuple]]":
        """Return (seed_bbox, candidates_list, refined_bbox) for the dungeon-map region.

        ``seed_bbox`` and ``refined_bbox`` are both ``(x, y, w, h)`` tuples.
        ``refined_bbox`` may differ from ``seed_bbox`` if bbox_refine is enabled.
        """
        cfg    = self.config
        sw, sh = image.size
        gray   = image.convert("L")

        margin = max(0.0, min(0.25, cfg.search_margin))
        sx0, sy0 = int(sw * margin), int(sh * margin)
        sx1, sy1 = int(sw * (1 - margin)), int(sh * (1 - margin))
        sw2, sh2 = sx1 - sx0, sy1 - sy0

        candidates: list = []  # each entry: (x, y, w, h, metric_value)

        # Build frac list descending from max_frac to min_frac
        step  = max(0.01, cfg.frac_step)
        frac  = cfg.max_frac
        fracs: list = []
        while frac >= cfg.min_frac - 1e-9:
            fracs.append(round(frac, 6))
            frac -= step

        method = cfg.bbox_method
        thresh = cfg.dark_thresh

        for frac in fracs:
            side = int(min(sw2, sh2) * frac)
            if side < 32:
                continue
            cx = sx0 + (sw2 - side) // 2
            cy = sy0 + (sh2 - side) // 2
            patch = gray.crop((cx, cy, cx + side, cy + side))
            metric = _brightness_metric(patch, method)
            candidates.append((cx, cy, side, side, metric))
            log.append(f"frac={frac:.2f} side={side} {method}={metric:.1f}")

        if not candidates:
            log.append("No candidates found (all patches too small)")
            return None, candidates, None

        seed_bbox: Optional[tuple] = None

        if cfg.prefer_darkest:
            # Pick candidate with lowest brightness (or highest edge density for "edge")
            if method == "edge":
                # Higher edge density = more map-like, so pick highest
                best = max(candidates, key=lambda c: c[4])
            else:
                best = min(candidates, key=lambda c: c[4])
            cx, cy, cw, ch, metric = best
            log.append(f"prefer_darkest=True: best metric={metric:.1f} at ({cx},{cy})")
            seed_bbox = (cx, cy, cw, ch)

        elif method == "edge":
            # Higher edge density = more map-like
            edge_thresh = thresh / 255.0 * 0.5
            for cand in candidates:
                cx, cy, cw, ch, metric = cand
                if metric > edge_thresh:
                    log.append(f"edge accepted: density={metric:.4f} > {edge_thresh:.4f}")
                    seed_bbox = (cx, cy, cw, ch)
                    break
            if seed_bbox is None:
                # Fallback: just pick highest edge density
                best = max(candidates, key=lambda c: c[4])
                cx, cy, cw, ch, metric = best
                log.append(f"edge fallback: best density={metric:.4f}")
                seed_bbox = (cx, cy, cw, ch)

        else:
            # mean / median / trimmed_mean: first below threshold wins
            for cand in candidates:
                cx, cy, cw, ch, metric = cand
                if metric < thresh:
                    log.append(f"accepted: {method}={metric:.1f} < {thresh}")
                    seed_bbox = (cx, cy, cw, ch)
                    break
            if seed_bbox is None:
                log.append(f"FAILED: all {len(candidates)} candidates above threshold {thresh}")
                return None, candidates, None

        # ── Refinement (edge-snap) ────────────────────────
        refined_bbox: Optional[tuple] = None
        if cfg.bbox_refine and seed_bbox is not None:
            refined_bbox = self._refine_bbox_by_edges(gray, seed_bbox, log)

        return seed_bbox, candidates, refined_bbox

    def _refine_bbox_by_edges(self, gray: Image.Image, seed: tuple,
                               log: list) -> Optional[tuple]:
        """Refine a seed bbox by walking each side to strong edge projections.

        Converts the seed crop to edges, builds 1-D projections near each
        side, finds the strong-edge threshold, then adjusts each boundary
        outward/inward until the projection drops below threshold.

        Returns a (potentially rectangular) refined (x, y, w, h), or the
        original seed if refinement fails.
        """
        cfg = self.config
        x0, y0, w0, h0 = seed
        band_pct    = max(0.02, min(0.49, cfg.bbox_refine_band_pct))
        max_exp_pct = max(0.0,  min(0.40, cfg.bbox_refine_max_expand_pct))
        quantile    = max(0.50, min(0.99, cfg.bbox_refine_edge_quantile))
        min_exp_px  = max(0, cfg.bbox_refine_min_expand_px)

        sw, sh = gray.size
        log.append(f"  refine seed: ({x0},{y0},{w0}×{h0})")

        try:
            crop = gray.crop((x0, y0, x0 + w0, y0 + h0))
            edges = _compute_edges(crop)

            if _HAVE_NUMPY and isinstance(edges, np.ndarray):
                H, W = edges.shape
                band_x = max(2, int(W * band_pct))
                band_y = max(2, int(H * band_pct))
                max_dx = max(min_exp_px, int(W * max_exp_pct))
                max_dy = max(min_exp_px, int(H * max_exp_pct))

                # Projections in the outer bands of each side
                left_proj   = edges[:, :band_x].sum(axis=0)      # shape (band_x,)
                right_proj  = edges[:, W-band_x:].sum(axis=0)    # shape (band_x,)
                top_proj    = edges[:band_y, :].sum(axis=1)       # shape (band_y,)
                bottom_proj = edges[H-band_y:, :].sum(axis=1)    # shape (band_y,)

                def _q(arr: "np.ndarray") -> float:
                    flat = arr.flatten()
                    flat.sort()
                    idx = min(len(flat) - 1, int(len(flat) * quantile))
                    return float(flat[idx])

                # For each side find how far inward (from the outside edge) the
                # strong-edge threshold is exceeded; use that to compute the delta.
                def _find_edge_boundary(proj: "np.ndarray", inward: bool) -> int:
                    """Return the index along proj where we exceed threshold."""
                    thr = _q(proj) * 0.5
                    if not inward:
                        for i in range(len(proj)):
                            if proj[i] >= thr:
                                return i
                    else:
                        for i in range(len(proj) - 1, -1, -1):
                            if proj[i] >= thr:
                                return len(proj) - 1 - i
                    return 0

                dl = min(max_dx, _find_edge_boundary(left_proj,   False))
                dr = min(max_dx, _find_edge_boundary(right_proj,  True))
                dt = min(max_dy, _find_edge_boundary(top_proj,    False))
                db = min(max_dy, _find_edge_boundary(bottom_proj, True))

                # Expand outward (subtract from left/top, add to right/bottom)
                nx0 = max(0,       x0 - dl)
                ny0 = max(0,       y0 - dt)
                nx1 = min(sw - 1,  x0 + w0 + dr)
                ny1 = min(sh - 1,  y0 + h0 + db)
                nw  = max(1, nx1 - nx0)
                nh  = max(1, ny1 - ny0)

                log.append(
                    f"  refine deltas: left={dl} right={dr} top={dt} bot={db}  "
                    f"→ ({nx0},{ny0},{nw}×{nh})")
                return (nx0, ny0, nw, nh)

            # PIL fallback: simpler projection using PIL FIND_EDGES
            epil = crop.filter(ImageFilter.FIND_EDGES)
            W2, H2 = epil.size
            pix = list(epil.getdata())

            band_x = max(2, int(W2 * band_pct))
            band_y = max(2, int(H2 * band_pct))
            max_dx = max(min_exp_px, int(W2 * max_exp_pct))
            max_dy = max(min_exp_px, int(H2 * max_exp_pct))

            left_proj2  = [sum(pix[r * W2 + c] for r in range(H2)) for c in range(band_x)]
            right_proj2 = [sum(pix[r * W2 + (W2 - band_x + c)] for r in range(H2))
                           for c in range(band_x)]
            top_proj2   = [sum(pix[r * W2 + c] for c in range(W2)) for r in range(band_y)]
            bot_proj2   = [sum(pix[(H2 - band_y + r) * W2 + c] for c in range(W2))
                           for r in range(band_y)]

            def _q2(arr: list) -> float:
                s = sorted(arr)
                idx = min(len(s) - 1, int(len(s) * quantile))
                return float(s[idx])

            def _fe(proj: list, inward: bool) -> int:
                thr = _q2(proj) * 0.5
                if not inward:
                    for i, v in enumerate(proj):
                        if v >= thr:
                            return i
                else:
                    for i in range(len(proj) - 1, -1, -1):
                        if proj[i] >= thr:
                            return len(proj) - 1 - i
                return 0

            dl = min(max_dx, _fe(left_proj2,  False))
            dr = min(max_dx, _fe(right_proj2, True))
            dt = min(max_dy, _fe(top_proj2,   False))
            db = min(max_dy, _fe(bot_proj2,   True))

            nx0 = max(0,       x0 - dl)
            ny0 = max(0,       y0 - dt)
            nx1 = min(sw - 1,  x0 + w0 + dr)
            ny1 = min(sh - 1,  y0 + h0 + db)
            nw  = max(1, nx1 - nx0)
            nh  = max(1, ny1 - ny0)
            log.append(
                f"  refine (PIL) deltas: left={dl} right={dr} top={dt} bot={db}  "
                f"→ ({nx0},{ny0},{nw}×{nh})")
            return (nx0, ny0, nw, nh)

        except Exception as exc:
            log.append(f"  refine FAILED ({exc}); keeping seed bbox")
            return None

    # ── Stage 2: Micro-grid detection ─────────────────────

    def _detect_microgrid(self, map_img: Image.Image, log: list) -> dict:
        """Detect micro-grid spacing and alignment from the cropped map image."""
        cfg    = self.config
        W, H   = map_img.size

        min_micro  = cfg.min_micro
        max_micro  = cfg.max_micro
        min_module = cfg.min_module
        max_module = cfg.max_module

        if _HAVE_NUMPY:
            arr    = np.array(map_img, dtype=np.float32)
            edges  = _sobel_numpy(arr)
            proj_x = edges.sum(axis=0)    # shape (W,)
            proj_y = edges.sum(axis=1)    # shape (H,)

            # Detect module period (dominant large period)
            mod_px, mod_sx, mod_scores_x = _detect_period_numpy_top10(
                proj_x, min_module, min(max_module, W // 2))
            mod_py, mod_sy, mod_scores_y = _detect_period_numpy_top10(
                proj_y, min_module, min(max_module, H // 2))
            # Detect micro-cell period (dominant small period)
            mic_px, mic_sx, mic_scores_x = _detect_period_numpy_top10(
                proj_x, min_micro, max_micro)
            mic_py, mic_sy, mic_scores_y = _detect_period_numpy_top10(
                proj_y, min_micro, max_micro)

            # Apply forced periods if set
            if cfg.force_module_period > 0:
                mod_px = mod_py = cfg.force_module_period
                log.append(f"force_module_period={cfg.force_module_period}")
            if cfg.force_micro_period > 0:
                mic_px = mic_py = cfg.force_micro_period
                log.append(f"force_micro_period={cfg.force_micro_period}")

            log.append(f"module_period x={mod_px}(score={mod_sx:.1f}) y={mod_py}(score={mod_sy:.1f})")
            log.append(f"micro_period  x={mic_px}(score={mic_sx:.1f}) y={mic_py}(score={mic_sy:.1f})")

            step_x, micro_step_x = self._resolve_period(
                W, mod_px, mod_sx, mic_px, mic_sx)
            step_y, micro_step_y = self._resolve_period(
                H, mod_py, mod_sy, mic_py, mic_sy)

            offset_x = _find_best_offset_numpy(proj_x, max(1, micro_step_x))
            offset_y = _find_best_offset_numpy(proj_y, max(1, micro_step_y))

            log.append(f"resolved step x={step_x} micro_x={micro_step_x} offset_x={offset_x}")
            log.append(f"resolved step y={step_y} micro_y={micro_step_y} offset_y={offset_y}")

            return {
                "module_step_x": step_x,
                "module_step_y": step_y,
                "micro_step_x":  micro_step_x,
                "micro_step_y":  micro_step_y,
                "offset_x":      offset_x,
                "offset_y":      offset_y,
                # Debug data
                "_proj_x":        proj_x,
                "_proj_y":        proj_y,
                "_edges":         edges,
                "_mod_scores_x":  mod_scores_x,
                "_mod_scores_y":  mod_scores_y,
                "_mic_scores_x":  mic_scores_x,
                "_mic_scores_y":  mic_scores_y,
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

        mod_px, mod_sx = _detect_period_pil(proj_x2, min_module,
                                             min(max_module, W2 // 2))
        mod_py, mod_sy = _detect_period_pil(proj_y2, min_module,
                                             min(max_module, H2 // 2))
        mic_px, mic_sx = _detect_period_pil(proj_x2, min_micro, max_micro)
        mic_py, _      = _detect_period_pil(proj_y2, min_micro, max_micro)

        if cfg.force_module_period > 0:
            mod_px = mod_py = cfg.force_module_period
            log.append(f"force_module_period={cfg.force_module_period}")
        if cfg.force_micro_period > 0:
            mic_px = mic_py = cfg.force_micro_period
            log.append(f"force_micro_period={cfg.force_micro_period}")

        log.append(f"module_period x={mod_px}(score={mod_sx:.1f}) y={mod_py}")
        log.append(f"micro_period  x={mic_px}(score={mic_sx:.1f}) y={mic_py}")

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
            "_proj_x":       proj_x2,
            "_proj_y":       proj_y2,
            "_edges":        None,
            "_mod_scores_x": {},
            "_mod_scores_y": {},
            "_mic_scores_x": {},
            "_mic_scores_y": {},
        }

    def _resolve_period(self, size: int,
                        mod_p: int, mod_score: float,
                        mic_p: int, mic_score: float
                        ) -> "tuple[int, int]":
        """Choose between module-period and micro-period interpretations.

        Returns (module_step, micro_step) as integers in pixels.
        """
        cfg = self.config
        min_gs = cfg.min_grid_size
        max_gs = cfg.max_grid_size
        micro_cells = cfg.micro_cells

        def grid_quality(p: int) -> float:
            if p <= 0:
                return 0.0
            gs = size / p
            if gs < min_gs or gs > max_gs:
                return 0.0
            return 1.0 - abs(gs - round(gs))

        q_mod = grid_quality(mod_p)
        q_mic = grid_quality(mic_p * micro_cells)

        if q_mod >= q_mic:
            return mod_p, max(1, round(mod_p / micro_cells))
        else:
            return mic_p * micro_cells, mic_p

    # ── Stage 3: Module grid ───────────────────────────────

    def _infer_grid(self, map_img: Image.Image, micro: dict,
                    log: list) -> dict:
        """Divide the map image into a regular grid of module tiles.

        Uses search-and-score over candidate grid sizes N in
        [min_grid_size..max_grid_size] rather than clamping a rounded
        period estimate.  For the chosen N, gridlines are generated at
        phase-optimized offsets so tile boundaries align with actual map
        content.
        """
        cfg    = self.config
        W, H   = map_img.size
        step_x = max(1, micro.get("module_step_x") or W)
        step_y = max(1, micro.get("module_step_y") or H)
        micro_step_x = max(1, micro.get("micro_step_x") or 1)
        micro_step_y = max(1, micro.get("micro_step_y") or 1)
        offset_x = micro.get("offset_x", 0)
        offset_y = micro.get("offset_y", 0)

        min_gs = cfg.min_grid_size
        max_gs = cfg.max_grid_size
        sw     = cfg.grid_score_step_weight
        lw     = cfg.grid_score_line_weight

        edges = micro.get("_edges")   # may be None in PIL mode

        # ── Override shortcut ─────────────────────────────────────────
        micro_cells = cfg.micro_cells
        if cfg.override_n_cols > 0 and cfg.override_n_rows > 0:
            n_cols = cfg.override_n_cols
            n_rows = cfg.override_n_rows
            log.append(f"override_n_cols={n_cols}  override_n_rows={n_rows}")
        elif cfg.override_n_cols > 0:
            n_cols = cfg.override_n_cols
            log.append(f"override_n_cols={n_cols}")
            n_rows = self._score_best_n(
                H, step_y, micro_step_y, micro_cells, min_gs, max_gs, sw, lw,
                edges, axis=1, log=log, label="rows")
        elif cfg.override_n_rows > 0:
            n_rows = cfg.override_n_rows
            log.append(f"override_n_rows={n_rows}")
            n_cols = self._score_best_n(
                W, step_x, micro_step_x, micro_cells, min_gs, max_gs, sw, lw,
                edges, axis=0, log=log, label="cols")
        else:
            n_cols = self._score_best_n(
                W, step_x, micro_step_x, micro_cells, min_gs, max_gs, sw, lw,
                edges, axis=0, log=log, label="cols")
            n_rows = self._score_best_n(
                H, step_y, micro_step_y, micro_cells, min_gs, max_gs, sw, lw,
                edges, axis=1, log=log, label="rows")

        # ── Grid-line generation with phase optimisation ─────────────
        implied_step_x = W / n_cols if n_cols > 0 else W
        implied_step_y = H / n_rows if n_rows > 0 else H

        # Search radius: ±one micro-step so we don't wander far from
        # the FFT-detected phase.
        radius_x = max(1, micro_step_x)
        radius_y = max(1, micro_step_y)

        if edges is not None:
            phase_x, _ = _best_phase_for_N(edges, implied_step_x, n_cols, 0, radius_x)
            phase_y, _ = _best_phase_for_N(edges, implied_step_y, n_rows, 1, radius_y)
        else:
            # PIL fallback: use micro offset as initial phase, no search
            phase_x = float(offset_x % max(1, implied_step_x))
            phase_y = float(offset_y % max(1, implied_step_y))

        # Build explicit grid lines (N+1 boundaries, including 0 and dim)
        x_lines = [max(0, min(W, int(round(phase_x + k * implied_step_x))))
                   for k in range(n_cols + 1)]
        y_lines = [max(0, min(H, int(round(phase_y + k * implied_step_y))))
                   for k in range(n_rows + 1)]

        # Ensure endpoints are exactly 0 and W/H so we don't leave gaps
        x_lines[0]  = 0
        x_lines[-1] = W
        y_lines[0]  = 0
        y_lines[-1] = H

        # Self-check
        if len(x_lines) != n_cols + 1:
            log.append(f"WARN: len(x_lines)={len(x_lines)} expected {n_cols + 1}")
        if len(y_lines) != n_rows + 1:
            log.append(f"WARN: len(y_lines)={len(y_lines)} expected {n_rows + 1}")

        log.append(
            f"grid: {n_cols}×{n_rows} tiles  "
            f"implied_step_x={implied_step_x:.1f} implied_step_y={implied_step_y:.1f}  "
            f"phase_x={phase_x:.1f} phase_y={phase_y:.1f}")
        log.append(
            f"  x_lines[0..{n_cols}]={x_lines}  "
            f"y_lines[0..{n_rows}]={y_lines}")

        tiles = []
        for r in range(n_rows):
            for c in range(n_cols):
                tx = x_lines[c]
                ty = y_lines[r]
                tw = x_lines[c + 1] - tx
                th = y_lines[r + 1] - ty
                if tw > 0 and th > 0:
                    tiles.append({
                        "row": r, "col": c,
                        "x": tx, "y": ty,
                        "w": tw, "h": th,
                    })

        return {
            "n_rows":  n_rows,  "n_cols":  n_cols,
            "cell_w":  implied_step_x, "cell_h": implied_step_y,
            "x_lines": x_lines, "y_lines": y_lines,
            "tiles":   tiles,
        }

    def _score_best_n(self, dim: int, detected_step: float,
                      micro_step: float, micro_cells: int,
                      min_gs: int, max_gs: int,
                      sw: float, lw: float,
                      edges, axis: int,
                      log: list, label: str) -> int:
        """Evaluate candidate grid sizes and return the best N.

        Parameters
        ----------
        dim : int
            Image dimension in pixels (width for cols, height for rows).
        detected_step : float
            Module step detected by microgrid FFT (pixels per module).
        micro_step : float
            Micro-cell step (pixels per sub-cell).
        micro_cells : int
            Number of micro-cells per module.
        min_gs, max_gs : int
            Range of allowed N.
        sw, lw : float
            Weights for step-closeness and line-strength scoring.
        edges : array-like or None
            Edge magnitude image.
        axis : int
            0 = cols (vertical lines), 1 = rows (horizontal lines).
        log : list
            Log target.
        label : str
            Human label for log output.
        """
        micro_derived_step = micro_step * micro_cells

        def _step_score(implied: float, ref: float) -> float:
            if ref <= 0:
                return 0.0
            return 1.0 / (1.0 + abs(implied - ref) / max(ref, 1.0))

        rows_log = [f"  N-scoring ({label}, dim={dim}):"]

        best_n = min_gs
        best_total = 0.0

        candidates_scores: list = []

        for N in range(min_gs, max_gs + 1):
            if N <= 0:
                continue
            implied = dim / N

            # Step-closeness score (average over two period references)
            sc1 = _step_score(implied, detected_step)
            sc2 = _step_score(implied, micro_derived_step)
            step_sc = (sc1 + sc2) / 2.0

            # Line-strength score using best phase for this N
            if edges is not None:
                phase, line_sc = _best_phase_for_N(edges, implied, N, axis)
            else:
                phase, line_sc = 0.0, 0.0

            candidates_scores.append((N, implied, step_sc, line_sc, phase))

        # Normalise line scores to [0,1]
        if candidates_scores:
            max_line = max(c[3] for c in candidates_scores) or 1.0
            candidates_scores = [
                (N, imp, ssc, lsc / max_line, ph)
                for N, imp, ssc, lsc, ph in candidates_scores
            ]

        for N, implied, step_sc, line_sc_norm, phase in candidates_scores:
            total = sw * step_sc + lw * line_sc_norm
            rows_log.append(
                f"    N={N:2d} step={implied:6.1f}  "
                f"step_sc={step_sc:.3f}  line_sc={line_sc_norm:.3f}  "
                f"total={total:.3f}  phase={phase:.1f}")
            if total > best_total:
                best_total = total
                best_n = N

        rows_log.append(f"  → best {label} N={best_n} (total={best_total:.3f})")
        for line in rows_log:
            log.append(line)

        return max(1, best_n)



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

        sz        = self.config.tmpl_size
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
        cfg   = self.config
        sz    = cfg.tmpl_size
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
            tile_matches[key] = candidates[: cfg.top_k]

        if cfg.unique_assignment:
            layout = self._assign_unique(tile_matches)
        else:
            # Best match per tile, no uniqueness constraint
            layout = {}
            for key, cands in tile_matches.items():
                if cands and cands[0][2] <= cfg.match_thr:
                    mk, rot, score = cands[0]
                    layout[key] = {"module": mk, "rot": rot, "score": score}
        return tile_matches, layout

    def _assign_unique(self, tile_matches: dict) -> dict:
        """Greedy unique module assignment across all tiles."""
        cfg  = self.config
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
            if score > cfg.match_thr:
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
                      candidates: list,
                      seed_bbox: Optional[tuple] = None,
                      refined_bbox: Optional[tuple] = None) -> Image.Image:
    """Highlight the detected map bbox (and all candidates) on the full image.

    Parameters
    ----------
    bbox : tuple or None
        The final active bbox (``(x,y,w,h)``).
    candidates : list
        All scored candidates from the seed-selection stage.
    seed_bbox : tuple or None
        The original seed bbox before refinement.  Drawn in orange when
        different from *bbox*.
    refined_bbox : tuple or None
        The refined bbox after edge-snap.  Drawn in green when present.
    """
    out  = _rgba(image)
    draw = ImageDraw.Draw(out)

    # Draw all candidates in dim yellow
    for cand in candidates:
        cx, cy, cw, ch, avg = cand
        draw.rectangle([cx, cy, cx + cw, cy + ch],
                       outline=(200, 180, 60, 120), width=2)

    # Draw seed bbox in orange (if it differs from the final active bbox)
    if seed_bbox and seed_bbox != bbox:
        sx, sy, sw2, sh2 = seed_bbox
        draw.rectangle([sx, sy, sx + sw2, sy + sh2],
                       outline=(240, 140, 30, 200), width=3)
        draw.text((sx + 6, sy + 6), "Seed bbox", fill=(240, 140, 30, 200))

    # Draw refined bbox in cyan
    if refined_bbox:
        rx, ry, rw, rh = refined_bbox
        draw.rectangle([rx, ry, rx + rw, ry + rh],
                       outline=(60, 220, 220, 230), width=3)
        draw.text((rx + 6, ry + 6 + 16), "Refined bbox", fill=(60, 220, 220, 230))

    # Highlight the final active bbox in bright green
    if bbox:
        x, y, w, h = bbox
        draw.rectangle([x, y, x + w, y + h],
                       outline=(80, 230, 80, 240), width=4)
        draw.text((x + 6, y + 6), "Active map area", fill=(80, 230, 80, 240))

    del draw
    return out


def draw_bbox_heatmap(image: Image.Image, candidates: list,
                      bbox: Optional[tuple]) -> Image.Image:
    """Color each candidate by brightness (dark=green, bright=red)."""
    out  = _rgba(image)
    draw = ImageDraw.Draw(out)
    if candidates:
        brightnesses = [c[4] for c in candidates]
        lo, hi = min(brightnesses), max(brightnesses)
        span = max(hi - lo, 1.0)
        for cand in candidates:
            cx, cy, cw, ch, avg = cand
            t = (avg - lo) / span  # 0=darkest, 1=brightest
            r = int(t * 220 + 20)
            g = int((1 - t) * 200 + 20)
            b = 40
            draw.rectangle([cx, cy, cx + cw, cy + ch],
                           outline=(r, g, b, 200), width=2)
            draw.text((cx + 2, cy + 2), f"{avg:.0f}", fill=(r, g, b, 220))
    if bbox:
        x, y, w, h = bbox
        draw.rectangle([x, y, x + w, y + h], outline=(80, 230, 80, 240), width=4)
        draw.text((x + 6, y + 6), "Accepted", fill=(80, 230, 80, 240))
    del draw
    return out


def draw_profiles_image(micro: dict, size: tuple = (512, 256)) -> Image.Image:
    """Render proj_x / proj_y as a simple line chart."""
    W, H = size
    out  = Image.new("RGBA", (W, H), (20, 20, 20, 255))
    draw = ImageDraw.Draw(out)

    def _draw_profile(profile, color, y_offset, chart_h):
        if profile is None:
            return
        if _HAVE_NUMPY:
            arr = np.asarray(profile, dtype=float)
        else:
            arr = list(profile)
            if not arr:
                return
        n = len(arr)
        if n < 2:
            return
        x_scale = W / n
        if _HAVE_NUMPY:
            mx = float(arr.max()) or 1.0
            pts = [(int(i * x_scale), y_offset + chart_h - int(float(arr[i]) / mx * (chart_h - 4)))
                   for i in range(n)]
        else:
            mx = max(arr) or 1.0
            pts = [(int(i * x_scale), y_offset + chart_h - int(arr[i] / mx * (chart_h - 4)))
                   for i in range(n)]
        for i in range(len(pts) - 1):
            draw.line([pts[i], pts[i + 1]], fill=color, width=1)

    half = H // 2
    draw.text((4, 4),        "proj_x (columns)", fill=(180, 220, 180, 255))
    draw.text((4, half + 4), "proj_y (rows)",    fill=(180, 180, 220, 255))
    _draw_profile(micro.get("_proj_x"), (120, 220, 120, 200), 20,        half - 24)
    _draw_profile(micro.get("_proj_y"), (120, 120, 220, 200), half + 20, half - 24)

    # Draw vertical lines for module steps (from FFT offset)
    msx   = micro.get("module_step_x", 0)
    proj_x = micro.get("_proj_x")
    if msx and proj_x is not None:
        n = len(proj_x) if hasattr(proj_x, "__len__") else 1
        if n > 0:
            x_per_px = W / n
            x = micro.get("offset_x", 0) * x_per_px
            while x < W:
                draw.line([(int(x), 20), (int(x), half - 4)],
                          fill=(200, 200, 60, 150), width=1)
                x += msx * x_per_px

    # Draw final snapped grid lines on top (from grid x_lines/y_lines)
    grid = micro.get("_grid")   # optionally injected by _build_step_images
    if grid:
        proj_xd = micro.get("_proj_x")
        dim_x = len(proj_xd) if proj_xd is not None and hasattr(proj_xd, "__len__") else 1
        x_per_px = W / max(dim_x, 1)
        for xp in grid.get("x_lines", []):
            draw.line([(int(xp * x_per_px), 20), (int(xp * x_per_px), half - 4)],
                      fill=(60, 220, 220, 200), width=2)

        proj_yd = micro.get("_proj_y")
        dim_y = len(proj_yd) if proj_yd is not None and hasattr(proj_yd, "__len__") else 1
        y_per_px = (H // 2 - 24) / max(dim_y, 1)
        for yp in grid.get("y_lines", []):
            screen_y = half + 20 + (H // 2 - 24) - int(yp * y_per_px)
            draw.line([(0, screen_y), (W, screen_y)],
                      fill=(220, 60, 220, 200), width=2)

    del draw
    return out


def draw_edge_overlay(edges, map_size: tuple) -> Image.Image:
    """Convert edge magnitude array to a visible greyscale image."""
    if _HAVE_NUMPY and isinstance(edges, np.ndarray):
        mx     = float(edges.max()) or 1.0
        arr_u8 = (edges / mx * 255).clip(0, 255).astype(np.uint8)
        return Image.fromarray(arr_u8, mode="L").convert("RGBA")
    if edges is not None:
        return edges.convert("RGBA")
    return Image.new("RGBA", map_size, (0, 0, 0, 255))


def draw_microgrid_overlay(map_img: Image.Image, micro: dict) -> Image.Image:
    """Draw micro-grid lines (both axes) on the map crop."""
    out  = _rgba(map_img)
    draw = ImageDraw.Draw(out)
    W, H = map_img.size

    micro_step_x  = micro.get("micro_step_x", 0)
    micro_step_y  = micro.get("micro_step_y", 0)
    offset_x      = micro.get("offset_x", 0)
    offset_y      = micro.get("offset_y", 0)
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
    """Draw module tile boundaries on the map crop.

    When ``grid`` contains ``x_lines`` / ``y_lines`` (from the phase-snapped
    grid-inference stage), those explicit boundary lines are drawn in addition
    to the tile rectangles to make alignment clearly visible.
    """
    out  = _rgba(map_img)
    draw = ImageDraw.Draw(out)
    W, H = map_img.size

    x_lines = grid.get("x_lines", [])
    y_lines = grid.get("y_lines", [])

    # Draw explicit snapped grid lines (vertical)
    for i, xp in enumerate(x_lines):
        color = (60, 200, 240, 220) if i in (0, len(x_lines) - 1) else (60, 180, 220, 160)
        draw.line([(xp, 0), (xp, H)], fill=color, width=2)
        if 0 < i < len(x_lines) - 1:
            draw.text((xp + 2, 2), str(i), fill=(60, 180, 220, 220))

    # Draw explicit snapped grid lines (horizontal)
    for i, yp in enumerate(y_lines):
        color = (60, 200, 240, 220) if i in (0, len(y_lines) - 1) else (60, 220, 180, 160)
        draw.line([(0, yp), (W, yp)], fill=color, width=2)
        if 0 < i < len(y_lines) - 1:
            draw.text((2, yp + 2), str(i), fill=(60, 220, 180, 220))

    # Draw tile rectangles and indices on top
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
      original     – raw loaded/captured image
      bbox         – detected map bbox overlaid on full image
      bbox_heatmap – candidate heatmap colored by brightness
      edges        – edge magnitude image of the map crop
      microgrid    – map crop with micro-grid lines
      profiles     – 1-D projection profiles chart
      grid         – map crop with module tile boundaries
      matches      – map crop with per-tile match result
    """

    _STEPS = [
        ("original",     "Original"),
        ("bbox",         "Map BBox"),
        ("bbox_heatmap", "BBox Heatmap"),
        ("edges",        "Edge Image"),
        ("microgrid",    "Micro-Grid"),
        ("profiles",     "Profiles"),
        ("grid",         "Module Grid"),
        ("matches",      "Tile Matches"),
    ]

    def __init__(self, preload_path: Optional[Path] = None):
        super().__init__()
        self.title("D&D Map Scanner V2  –  Debug Tool")
        self.configure(bg=BG)
        self.geometry("1500x860")
        self.minsize(900, 600)

        self._img_orig:   Optional[Image.Image] = None
        self._result:     Optional[dict]        = None
        self._step_imgs:  dict = {}
        self._tkimgs:     list = []
        self._step_var    = tk.StringVar(value="original")
        self._map_var     = tk.StringVar(value="")
        self._sel_tile:   Optional[tuple] = None  # (row, col)
        self._cv_scale    = 1.0
        self._cv_off      = (0, 0)   # (dx, dy) of image top-left in canvas

        self._disp_img_size: tuple = (1, 1)   # (w, h) of the displayed image

        self._manifest: dict = {}
        self._load_manifest()

        # Load or initialise config
        loaded = _load_settings()
        self._config: ScannerConfig = loaded if loaded is not None else ScannerConfig()

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
        for name in self._manifest:
            if (MODULES / name).is_dir():
                self._map_var.set(name)
                break

    # ── Build UI ──────────────────────────────────────────

    def _build(self):
        # Toolbar
        tb = tk.Frame(self, bg=PANEL2, pady=6)
        tb.pack(fill="x")

        flat_btn(tb, "📂  Load Image",   self._load_image).pack(side="left", padx=6)
        flat_btn(tb, "📷  Screenshot",   self._do_screenshot).pack(side="left", padx=2)
        tk.Frame(tb, bg=BDR2, width=1).pack(side="left", fill="y", padx=8)
        flat_btn(tb, "🚀  Run Pipeline", self._run_pipeline,
                 fg="#111", bg=ACCENT).pack(side="left", padx=2)
        tk.Frame(tb, bg=BDR2, width=1).pack(side="left", fill="y", padx=8)
        flat_btn(tb, "💾  Save Debug",   self._save_debug,
                 fg=DIM, bg=BTN_BG).pack(side="left", padx=2)

        # Map selector
        tk.Frame(tb, bg=BDR2, width=1).pack(side="left", fill="y", padx=8)
        tk.Label(tb, text="Map:", bg=PANEL2, fg=DIM,
                 font=("Segoe UI", 9)).pack(side="left", padx=(4, 2))
        map_names = [n for n in self._manifest if (MODULES / n).is_dir()]
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
                               state="readonly", font=("Segoe UI", 9), width=14)
        step_cb.pack(side="left")
        step_cb.bind("<<ComboboxSelected>>", lambda _: self._show_step())

        # Status label
        self._status_var = tk.StringVar(value="Load an image or take a screenshot to begin.")
        tk.Label(tb, textvariable=self._status_var, bg=PANEL2, fg=DIM,
                 font=("Segoe UI", 9), padx=12, anchor="w",
                 wraplength=400).pack(side="left", fill="x", expand=True)

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        # Main content (canvas | info+settings panel)
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

        # Right panel: tabbed notebook
        info_frame = tk.Frame(main, bg=PANEL, width=380)
        info_frame.pack_propagate(False)
        main.add(info_frame, minsize=260)

        tk.Label(info_frame, text="Pipeline Info", bg=PANEL, fg=ACCENT,
                 font=("Segoe UI", 10, "bold"), padx=10, pady=8).pack(anchor="w")
        tk.Frame(info_frame, bg=BORDER, height=1).pack(fill="x")

        nb = ttk.Notebook(info_frame)
        nb.pack(fill="both", expand=True, padx=2, pady=4)

        # ── Tab 1: Log ────────────────────────────────────
        log_tab = tk.Frame(nb, bg=PANEL)
        nb.add(log_tab, text="Log")

        # Selected tile details
        self._tile_lbl = tk.Label(log_tab, text="Click a tile on the grid/matches view",
                                  bg=PANEL, fg=DIM, font=("Segoe UI", 9),
                                  padx=8, pady=4, anchor="w", wraplength=340)
        self._tile_lbl.pack(fill="x")
        tk.Frame(log_tab, bg=BORDER, height=1).pack(fill="x")

        self._log = tk.Text(log_tab, bg=PANEL2, fg=TEXT, bd=0,
                            font=("Consolas", 8), wrap="word",
                            state="disabled", highlightthickness=0)
        sb = tk.Scrollbar(log_tab, command=self._log.yview)
        self._log["yscrollcommand"] = sb.set
        sb.pack(side="right", fill="y")
        self._log.pack(fill="both", expand=True, padx=4, pady=4)

        flat_btn(log_tab, "Clear Log", self._clear_log,
                 fg=DIM, bg=PANEL, padx=8, pady=3,
                 font=("Segoe UI", 8)).pack(anchor="e", padx=4, pady=4)

        # ── Tab 2: Settings ───────────────────────────────
        settings_outer = tk.Frame(nb, bg=PANEL)
        nb.add(settings_outer, text="Settings")

        # Scrollable container
        settings_canvas = tk.Canvas(settings_outer, bg=PANEL, bd=0,
                                    highlightthickness=0)
        settings_sb = tk.Scrollbar(settings_outer, orient="vertical",
                                   command=settings_canvas.yview)
        settings_canvas.configure(yscrollcommand=settings_sb.set)
        settings_sb.pack(side="right", fill="y")
        settings_canvas.pack(side="left", fill="both", expand=True)

        settings_frame = tk.Frame(settings_canvas, bg=PANEL)
        settings_win   = settings_canvas.create_window(
            (0, 0), window=settings_frame, anchor="nw")

        def _on_settings_configure(event):
            settings_canvas.configure(
                scrollregion=settings_canvas.bbox("all"))
        settings_frame.bind("<Configure>", _on_settings_configure)

        def _on_canvas_configure(event):
            settings_canvas.itemconfig(settings_win, width=event.width)
        settings_canvas.bind("<Configure>", _on_canvas_configure)

        # Mouse-wheel scrolling (only when hovering over the settings canvas)
        def _on_mousewheel(event):
            settings_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        settings_canvas.bind("<Enter>", lambda _: settings_canvas.bind_all("<MouseWheel>", _on_mousewheel))
        settings_canvas.bind("<Leave>", lambda _: settings_canvas.unbind_all("<MouseWheel>"))

        self._sv: dict = {}   # StringVar / IntVar / BooleanVar keyed by field name

        def _lf(parent, title):
            """Labeled frame helper."""
            f = tk.LabelFrame(parent, text=title, bg=PANEL, fg=ACCENT,
                              font=("Segoe UI", 8, "bold"), padx=6, pady=4)
            f.pack(fill="x", padx=6, pady=3)
            return f

        def _row(parent, label, widget_factory):
            r = tk.Frame(parent, bg=PANEL)
            r.pack(fill="x", pady=1)
            tk.Label(r, text=label, bg=PANEL, fg=TEXT,
                     font=("Segoe UI", 8), width=22, anchor="w").pack(side="left")
            w = widget_factory(r)
            w.pack(side="left")
            return w

        def _spin(parent, key, from_, to, inc=1, width=6, is_float=False):
            if is_float:
                v = tk.StringVar(value=str(getattr(self._config, key)))
            else:
                v = tk.StringVar(value=str(getattr(self._config, key)))
            self._sv[key] = v
            w = ttk.Spinbox(parent, from_=from_, to=to, increment=inc,
                            textvariable=v, width=width)
            return w

        def _combo(parent, key, values):
            v = tk.StringVar(value=str(getattr(self._config, key)))
            self._sv[key] = v
            w = ttk.Combobox(parent, textvariable=v, values=values,
                             state="readonly", width=14)
            return w

        def _check(parent, key):
            v = tk.BooleanVar(value=bool(getattr(self._config, key)))
            self._sv[key] = v
            w = tk.Checkbutton(parent, variable=v, bg=PANEL,
                               activebackground=PANEL, fg=TEXT,
                               selectcolor=PANEL2)
            return w

        # ── BBox Detection ────────────────────────────────
        f = _lf(settings_frame, "BBox Detection")
        _row(f, "dark_thresh (0-255):",
             lambda p: _spin(p, "dark_thresh", 0, 255, 1, 5))
        _row(f, "bbox_method:",
             lambda p: _combo(p, "bbox_method",
                              ["mean", "median", "trimmed_mean", "edge"]))
        _row(f, "search_margin % (0-25):",
             lambda p: _spin(p, "search_margin", 0, 25, 0.5, 5, is_float=True))
        _row(f, "min_frac (0.05-0.90):",
             lambda p: _spin(p, "min_frac", 0.05, 0.90, 0.05, 6, is_float=True))
        _row(f, "max_frac (0.10-0.99):",
             lambda p: _spin(p, "max_frac", 0.10, 0.99, 0.05, 6, is_float=True))
        _row(f, "frac_step (0.01-0.20):",
             lambda p: _spin(p, "frac_step", 0.01, 0.20, 0.01, 6, is_float=True))
        _row(f, "prefer_darkest:",
             lambda p: _check(p, "prefer_darkest"))

        # ── BBox Refinement ───────────────────────────────
        f = _lf(settings_frame, "BBox Refinement")
        _row(f, "bbox_refine:",
             lambda p: _check(p, "bbox_refine"))
        _row(f, "refine_band_pct (0-49):",
             lambda p: _spin(p, "bbox_refine_band_pct", 0, 49, 1, 5, is_float=True))
        _row(f, "refine_min_expand_px:",
             lambda p: _spin(p, "bbox_refine_min_expand_px", 0, 100, 1, 5))
        _row(f, "refine_max_expand% (0-40):",
             lambda p: _spin(p, "bbox_refine_max_expand_pct", 0, 40, 1, 5, is_float=True))
        _row(f, "refine_edge_quantile:",
             lambda p: _spin(p, "bbox_refine_edge_quantile", 0.50, 0.99, 0.01, 6, is_float=True))


        f = _lf(settings_frame, "Crop Preprocessing")
        _row(f, "border_crop_pct % (0-20):",
             lambda p: _spin(p, "border_crop_pct", 0, 20, 1, 5, is_float=True))
        _row(f, "contrast_boost:",
             lambda p: _check(p, "contrast_boost"))
        _row(f, "unsharp_mask:",
             lambda p: _check(p, "unsharp_mask"))

        # ── Microgrid ─────────────────────────────────────
        f = _lf(settings_frame, "Microgrid")
        _row(f, "min_micro (1-20):",
             lambda p: _spin(p, "min_micro", 1, 20, 1, 5))
        _row(f, "max_micro (5-100):",
             lambda p: _spin(p, "max_micro", 5, 100, 1, 5))
        _row(f, "min_module (5-200):",
             lambda p: _spin(p, "min_module", 5, 200, 1, 5))
        _row(f, "max_module (50-1000):",
             lambda p: _spin(p, "max_module", 50, 1000, 10, 6))
        _row(f, "micro_cells (5-20):",
             lambda p: _spin(p, "micro_cells", 5, 20, 1, 5))
        _row(f, "force_micro_period:",
             lambda p: _spin(p, "force_micro_period", 0, 100, 1, 5))
        _row(f, "force_module_period:",
             lambda p: _spin(p, "force_module_period", 0, 1000, 10, 6))
        _row(f, "min_grid_size (1-5):",
             lambda p: _spin(p, "min_grid_size", 1, 5, 1, 5))
        _row(f, "max_grid_size (2-20):",
             lambda p: _spin(p, "max_grid_size", 2, 20, 1, 5))

        # ── Grid Inference ────────────────────────────────
        f = _lf(settings_frame, "Grid Inference")
        _row(f, "override_n_rows (0=auto):",
             lambda p: _spin(p, "override_n_rows", 0, 20, 1, 5))
        _row(f, "override_n_cols (0=auto):",
             lambda p: _spin(p, "override_n_cols", 0, 20, 1, 5))
        _row(f, "step_weight (0-1):",
             lambda p: _spin(p, "grid_score_step_weight", 0.0, 1.0, 0.05, 6, is_float=True))
        _row(f, "line_weight (0-1):",
             lambda p: _spin(p, "grid_score_line_weight", 0.0, 1.0, 0.05, 6, is_float=True))

        # ── Template Matching ─────────────────────────────
        f = _lf(settings_frame, "Template Matching")
        _row(f, "match_thr (0.00-1.00):",
             lambda p: _spin(p, "match_thr", 0.00, 1.00, 0.01, 6, is_float=True))
        _row(f, "tmpl_size (32-256):",
             lambda p: _spin(p, "tmpl_size", 32, 256, 8, 5))
        _row(f, "top_k (1-20):",
             lambda p: _spin(p, "top_k", 1, 20, 1, 5))
        _row(f, "unique_assignment:",
             lambda p: _check(p, "unique_assignment"))

        # ── Bypass BBox ───────────────────────────────────
        f = _lf(settings_frame, "Bypass BBox")
        _row(f, "bypass_bbox:",
             lambda p: _check(p, "bypass_bbox"))
        _row(f, "bypass_crop_pct % (10-100):",
             lambda p: _spin(p, "bypass_crop_pct", 10, 100, 5, 5, is_float=True))

        # ── Settings management buttons ───────────────────
        btn_frame = tk.Frame(settings_frame, bg=PANEL)
        btn_frame.pack(fill="x", padx=6, pady=6)
        flat_btn(btn_frame, "Reset Defaults", self._reset_settings,
                 fg=DIM, bg=BTN_BG, padx=6, pady=3,
                 font=("Segoe UI", 8)).pack(side="left", padx=2)
        flat_btn(btn_frame, "Save…", self._save_settings_dialog,
                 fg=DIM, bg=BTN_BG, padx=6, pady=3,
                 font=("Segoe UI", 8)).pack(side="left", padx=2)
        flat_btn(btn_frame, "Load…", self._load_settings_dialog,
                 fg=DIM, bg=BTN_BG, padx=6, pady=3,
                 font=("Segoe UI", 8)).pack(side="left", padx=2)

    # ── Config ↔ UI ──────────────────────────────────────

    def _get_config_from_ui(self) -> ScannerConfig:
        """Read all settings widgets and return a ScannerConfig."""
        sv = self._sv

        def _f(key, default=0.0):
            try:
                return float(sv[key].get())
            except Exception:
                return default

        def _i(key, default=0):
            try:
                return int(float(sv[key].get()))
            except Exception:
                return default

        def _b(key, default=False):
            try:
                return bool(sv[key].get())
            except Exception:
                return default

        def _s(key, default=""):
            try:
                return str(sv[key].get())
            except Exception:
                return default

        return ScannerConfig(
            dark_thresh                 = _i("dark_thresh", 65),
            bbox_method                 = _s("bbox_method", "mean"),
            search_margin               = _f("search_margin", 5.0) / 100.0,
            min_frac                    = _f("min_frac", 0.20),
            max_frac                    = _f("max_frac", 0.90),
            frac_step                   = _f("frac_step", 0.05),
            prefer_darkest              = _b("prefer_darkest"),
            bbox_refine                 = _b("bbox_refine", True),
            bbox_refine_band_pct        = _f("bbox_refine_band_pct", 12.0) / 100.0,
            bbox_refine_min_expand_px   = _i("bbox_refine_min_expand_px", 0),
            bbox_refine_max_expand_pct  = _f("bbox_refine_max_expand_pct", 10.0) / 100.0,
            bbox_refine_edge_quantile   = _f("bbox_refine_edge_quantile", 0.85),
            border_crop_pct             = _f("border_crop_pct", 0.0) / 100.0,
            contrast_boost              = _b("contrast_boost"),
            unsharp_mask                = _b("unsharp_mask"),
            min_micro                   = _i("min_micro", 2),
            max_micro                   = _i("max_micro", 30),
            min_module                  = _i("min_module", 15),
            max_module                  = _i("max_module", 500),
            micro_cells                 = _i("micro_cells", 10),
            force_micro_period          = _i("force_micro_period", 0),
            force_module_period         = _i("force_module_period", 0),
            min_grid_size               = _i("min_grid_size", 2),
            max_grid_size               = _i("max_grid_size", 10),
            override_n_rows             = _i("override_n_rows", 0),
            override_n_cols             = _i("override_n_cols", 0),
            grid_score_step_weight      = _f("grid_score_step_weight", 0.5),
            grid_score_line_weight      = _f("grid_score_line_weight", 0.5),
            match_thr                   = _f("match_thr", 0.40),
            tmpl_size                   = _i("tmpl_size", 64),
            top_k                       = _i("top_k", 5),
            unique_assignment           = _b("unique_assignment", True),
            bypass_bbox                 = _b("bypass_bbox"),
            bypass_crop_pct             = _f("bypass_crop_pct", 80.0) / 100.0,
        )

    def _apply_config_to_ui(self, cfg: ScannerConfig):
        """Push a ScannerConfig into all settings widgets."""
        sv = self._sv
        mapping = {
            "dark_thresh":                  str(cfg.dark_thresh),
            "bbox_method":                  cfg.bbox_method,
            "search_margin":                str(round(cfg.search_margin * 100, 4)),
            "min_frac":                     str(cfg.min_frac),
            "max_frac":                     str(cfg.max_frac),
            "frac_step":                    str(cfg.frac_step),
            "prefer_darkest":               cfg.prefer_darkest,
            "bbox_refine":                  cfg.bbox_refine,
            "bbox_refine_band_pct":         str(round(cfg.bbox_refine_band_pct * 100, 2)),
            "bbox_refine_min_expand_px":    str(cfg.bbox_refine_min_expand_px),
            "bbox_refine_max_expand_pct":   str(round(cfg.bbox_refine_max_expand_pct * 100, 2)),
            "bbox_refine_edge_quantile":    str(cfg.bbox_refine_edge_quantile),
            "border_crop_pct":              str(round(cfg.border_crop_pct * 100, 4)),
            "contrast_boost":               cfg.contrast_boost,
            "unsharp_mask":                 cfg.unsharp_mask,
            "min_micro":                    str(cfg.min_micro),
            "max_micro":                    str(cfg.max_micro),
            "min_module":                   str(cfg.min_module),
            "max_module":                   str(cfg.max_module),
            "micro_cells":                  str(cfg.micro_cells),
            "force_micro_period":           str(cfg.force_micro_period),
            "force_module_period":          str(cfg.force_module_period),
            "min_grid_size":                str(cfg.min_grid_size),
            "max_grid_size":                str(cfg.max_grid_size),
            "override_n_rows":              str(cfg.override_n_rows),
            "override_n_cols":              str(cfg.override_n_cols),
            "grid_score_step_weight":       str(cfg.grid_score_step_weight),
            "grid_score_line_weight":       str(cfg.grid_score_line_weight),
            "match_thr":                    str(cfg.match_thr),
            "tmpl_size":                    str(cfg.tmpl_size),
            "top_k":                        str(cfg.top_k),
            "unique_assignment":            cfg.unique_assignment,
            "bypass_bbox":                  cfg.bypass_bbox,
            "bypass_crop_pct":              str(round(cfg.bypass_crop_pct * 100, 4)),
        }
        for key, val in mapping.items():
            if key not in sv:
                continue
            v = sv[key]
            if isinstance(v, tk.BooleanVar):
                v.set(bool(val))
            else:
                v.set(str(val))

    def _reset_settings(self):
        self._apply_config_to_ui(ScannerConfig())
        self._log_msg("Settings reset to defaults.")

    def _save_settings_dialog(self):
        if not _HAVE_TK:
            return
        path = filedialog.asksaveasfilename(
            title="Save Settings",
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All", "*.*")],
            initialfile="v2_settings.json",
        )
        if path:
            cfg = self._get_config_from_ui()
            if _save_settings(cfg, Path(path)):
                self._log_msg(f"Settings saved to {path}")
                self._status(f"Settings saved: {Path(path).name}")
            else:
                self._status("Failed to save settings.")

    def _load_settings_dialog(self):
        if not _HAVE_TK:
            return
        path = filedialog.askopenfilename(
            title="Load Settings",
            filetypes=[("JSON", "*.json"), ("All", "*.*")],
        )
        if path:
            cfg = _load_settings(Path(path))
            if cfg is not None:
                self._apply_config_to_ui(cfg)
                self._log_msg(f"Settings loaded from {path}")
                self._status(f"Settings loaded: {Path(path).name}")
            else:
                self._status("Failed to load settings (invalid file).")

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
        cfg      = self._get_config_from_ui()
        self._config = cfg
        _save_settings(cfg)   # auto-save

        # Log a config snapshot
        self._log_msg(f"\n=== Pipeline run  map={map_name or '(none)'} ===")
        d = _config_to_dict(cfg)
        non_default = {k: v for k, v in d.items()
                       if v != getattr(DEFAULT_CONFIG, k)}
        if non_default:
            self._log_msg("  Config overrides: " +
                          "  ".join(f"{k}={v}" for k, v in non_default.items()))
        else:
            self._log_msg("  Config: all defaults")

        self._status("Running pipeline…")
        self.update_idletasks()

        img = self._img_orig

        def _worker():
            scanner = MapScannerV2(map_name=map_name, config=cfg)
            result  = scanner.scan_image(img)
            self.after(0, lambda: self._pipeline_done(result))

        threading.Thread(target=_worker, daemon=True).start()

    def _pipeline_done(self, result: dict):
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

        bbox         = result.get("map_bbox")
        seed_bbox    = result.get("map_bbox_seed")     # kept for reference
        refined_bbox = result.get("map_bbox_refined")
        bbox_cands   = result.get("bbox_candidates", [])
        self._step_imgs["bbox"] = draw_bbox_overlay(
            img, bbox, bbox_cands,
            seed_bbox=seed_bbox,
            refined_bbox=refined_bbox)
        self._step_imgs["bbox_heatmap"] = draw_bbox_heatmap(img, bbox_cands, bbox)

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

        # Inject grid reference so profiles can draw final grid lines
        micro_with_grid = dict(micro)
        micro_with_grid["_grid"] = grid
        self._step_imgs["profiles"]  = draw_profiles_image(micro_with_grid, size=(512, 256))
        self._step_imgs["grid"]      = draw_grid_overlay(map_img, grid)

        layout = result.get("layout", {})
        self._step_imgs["matches"] = draw_matches_overlay(map_img, grid, layout)

    def _log_result(self, result: dict):
        if not result.get("ok"):
            self._log_msg("  ✗  " + result.get("error", "Pipeline failed"))
            # Log bbox candidates if any
            for line in result.get("bbox_log", []):
                self._log_msg("  " + line)
            return

        # Timings
        timings = result.get("timings", {})
        if timings:
            parts = [f"{k}={v*1000:.1f}ms" for k, v in timings.items()]
            self._log_msg("  ⏱  " + "  ".join(parts))

        bbox         = result.get("map_bbox")
        refined_bbox = result.get("map_bbox_refined")
        if bbox:
            self._log_msg(f"  ✓  Active map bbox: x={bbox[0]} y={bbox[1]} "
                          f"w={bbox[2]} h={bbox[3]}")
        if refined_bbox:
            rb = refined_bbox
            self._log_msg(f"  ✓  Refined bbox:    x={rb[0]} y={rb[1]} "
                          f"w={rb[2]} h={rb[3]}")

        # BBox log
        for line in result.get("bbox_log", []):
            self._log_msg("  bbox: " + line)

        micro = result.get("microgrid", {})
        self._log_msg(
            f"  Micro-grid: step_x={micro.get('micro_step_x')}  "
            f"step_y={micro.get('micro_step_y')}")
        self._log_msg(
            f"  Module step: x={micro.get('module_step_x')}  "
            f"y={micro.get('module_step_y')}  "
            f"offset=({micro.get('offset_x')}, {micro.get('offset_y')})")

        # Microgrid log
        for line in result.get("microgrid_log", []):
            self._log_msg("  μgrid: " + line)

        # Top period scores
        msx = micro.get("_mod_scores_x", {})
        if msx:
            top3 = sorted(msx.items(), key=lambda t: -t[1])[:3]
            self._log_msg("  mod_scores_x: " +
                          "  ".join(f"p={p}:{s:.0f}" for p, s in top3))

        grid = result.get("grid", {})
        self._log_msg(
            f"  Grid: {grid.get('n_cols')}×{grid.get('n_rows')} tiles  "
            f"(cell {grid.get('cell_w', 0):.1f}×{grid.get('cell_h', 0):.1f} px)")

        x_lines = grid.get("x_lines", [])
        y_lines = grid.get("y_lines", [])
        if x_lines:
            self._log_msg(f"  x_lines ({len(x_lines)}): {x_lines}")
        if y_lines:
            self._log_msg(f"  y_lines ({len(y_lines)}): {y_lines}")

        for line in result.get("grid_log", []):
            self._log_msg("  grid: " + line)

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
        img_x  = (event.x - ox) / scale
        img_y  = (event.y - oy) / scale

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
