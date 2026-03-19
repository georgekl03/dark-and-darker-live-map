"""detection/microgrid.py – Micro-grid detection for the dungeon-map overlay.

The dungeon map image has a faint 10×10 sub-cell (micro-cell) grid inside
each module tile.  This module detects the micro-cell period and the module
period by:

1. Computing Sobel edge magnitude of the map crop.
2. Summing edge values along columns (proj_x) and rows (proj_y) to get 1-D
   profiles.
3. Applying an FFT to each profile to find the dominant periodic spacing in
   both the micro-cell range and the module range.
4. Resolving which period interpretation best divides the image into an
   integer grid count in [min_grid_size, max_grid_size].
5. Finding the best phase offset for alignment.

An optional **microgrid-first** search (``use_microgrid_first=True``) scans a
coarse grid of candidate windows around the image centre, scores each by FFT
periodicity, and can return the window with the strongest micro-grid signal as
an alternative bbox estimate.

All public functions are pure (no UI, no global state).
"""

from __future__ import annotations

import dataclasses
import math
from typing import Any, Optional

try:
    from PIL import Image, ImageFilter
    _HAVE_PIL = True
except ImportError:
    _HAVE_PIL = False

try:
    import numpy as np
    _HAVE_NUMPY = True
except ImportError:
    np = None  # type: ignore[assignment]
    _HAVE_NUMPY = False

from .preprocess import sobel_edges


# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class MicrogridConfig:
    """Tuning parameters for micro-grid detection.

    Period search ranges
    --------------------
    min_micro, max_micro : int
        Search range for micro-cell period (pixels per sub-cell).
    min_module, max_module : int
        Search range for module period (pixels per module tile).
    micro_cells : int
        Number of micro-cells per module (always 10 in Dark & Darker).

    Forced periods
    --------------
    force_micro_period : int
        When > 0, override the detected micro-cell period with this value.
    force_module_period : int
        When > 0, override the detected module period with this value.

    Grid size constraints
    ---------------------
    min_grid_size, max_grid_size : int
        Acceptable range for the integer grid count (e.g. a 4×4 to 8×8 map).

    Microgrid-first search
    -----------------------
    use_microgrid_first : bool
        When *True*, scan a coarse grid of candidate windows and return the
        window with the strongest combined micro-grid periodicity signal via
        :func:`search_microgrid_first`.
    mgf_window_frac : float
        Candidate window size as a fraction of the shorter image dimension.
    mgf_search_steps : int
        Number of coarse grid steps along each axis (total windows =
        ``mgf_search_steps ** 2``).
    """

    min_micro: int = 2
    max_micro: int = 30
    min_module: int = 15
    max_module: int = 500
    micro_cells: int = 10
    force_micro_period: int = 0
    force_module_period: int = 0
    min_grid_size: int = 2
    max_grid_size: int = 10
    # Microgrid-first search
    use_microgrid_first: bool = False
    mgf_window_frac: float = 0.5
    mgf_search_steps: int = 7


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class MicrogridResult:
    """Outcome of :func:`detect_microgrid`.

    Attributes
    ----------
    module_step_x, module_step_y : int
        Module tile size in pixels along each axis.
    micro_step_x, micro_step_y : int
        Micro-cell size in pixels along each axis.
    offset_x, offset_y : int
        Phase offset (in pixels from the crop origin) that best aligns
        the micro-grid.
    periodicity_score_x, periodicity_score_y : float
        FFT peak magnitude for the dominant micro-cell period.
    mgf_bbox : tuple or None
        ``(x, y, w, h)`` of the best-scoring window from the
        microgrid-first search (only populated when
        ``config.use_microgrid_first`` is *True*).
    mgf_score : float
        Combined periodicity score of *mgf_bbox*.
    log : list
        Human-readable debug log lines.
    _proj_x, _proj_y : array-like
        1-D edge projection profiles (NumPy arrays or plain lists).
    _edges : array-like or None
        Full edge-magnitude image (NumPy array) or *None* in PIL-only mode.
    """

    module_step_x: int
    module_step_y: int
    micro_step_x: int
    micro_step_y: int
    offset_x: int
    offset_y: int
    periodicity_score_x: float
    periodicity_score_y: float
    mgf_bbox: Optional[tuple]
    mgf_score: float
    log: list
    _proj_x: Any
    _proj_y: Any
    _edges: Any


# ---------------------------------------------------------------------------
# Period detection helpers
# ---------------------------------------------------------------------------

def _detect_period_numpy(
    profile: "np.ndarray",
    min_p: int,
    max_p: int,
) -> "tuple[int, float]":
    """Find the dominant period in *profile* using an FFT.

    Parameters
    ----------
    profile:
        1-D NumPy array of edge-projection values.
    min_p, max_p:
        Search range for the period (inclusive), in pixels.

    Returns
    -------
    (period, score)
        *period* is the best integer period in [min_p, max_p]; *score* is
        the FFT magnitude at that frequency.
    """
    n = len(profile)
    if n < min_p * 2:
        return min_p, 0.0
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
        return min_p, 0.0
    best_i    = int(np.argmax(fft_mag[mask]))
    peak_freq = float(freqs[mask][best_i])
    period    = int(round(1.0 / peak_freq)) if peak_freq > 0 else min_p
    period    = max(min_p, min(max_p, period))
    return period, float(fft_mag[mask][best_i])


def _detect_period_numpy_top10(
    profile: "np.ndarray",
    min_p: int,
    max_p: int,
) -> "tuple[int, float, dict]":
    """Like :func:`_detect_period_numpy` but also returns the top-10 scores.

    Parameters
    ----------
    profile:
        1-D NumPy array of edge-projection values.
    min_p, max_p:
        Search range for the period (inclusive), in pixels.

    Returns
    -------
    (best_period, best_score, scores_dict)
        *scores_dict* maps ``period → FFT_magnitude`` for the top-10
        candidate periods.
    """
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
    top_idx = np.argsort(masked_mag)[::-1][:10]
    scores: dict = {}
    for i in top_idx:
        pf = float(masked_freq[i])
        p  = int(round(1.0 / pf)) if pf > 0 else min_p
        p  = max(min_p, min(max_p, p))
        scores[p] = float(masked_mag[i])
    return period, float(masked_mag[best_i]), scores


def _detect_period_pil(
    profile: list,
    min_p: int,
    max_p: int,
) -> "tuple[int, float]":
    """Pure-Python autocorrelation period detection (PIL / no-numpy fallback).

    Parameters
    ----------
    profile:
        1-D list of edge-projection values.
    min_p, max_p:
        Search range for the period (inclusive), in pixels.

    Returns
    -------
    (best_period, best_score)
    """
    n    = len(profile)
    mean = sum(profile) / max(n, 1)
    c    = [x - mean for x in profile]
    best_score, best_p = -1e18, min_p
    for p in range(min_p, min(max_p + 1, n // 2 + 1)):
        score = sum(c[i] * c[i + p] for i in range(n - p)) / max(n - p, 1)
        if score > best_score:
            best_score, best_p = score, p
    return best_p, best_score


# ---------------------------------------------------------------------------
# Phase (offset) helpers
# ---------------------------------------------------------------------------

def _find_best_offset(profile: Any, period: int) -> int:
    """Find the phase offset that maximises the comb-sum of *profile*.

    Tries all offsets in ``[0, period)`` and returns the one where the sum
    of ``profile[offset], profile[offset+period], profile[offset+2*period],
    …`` is highest.

    Parameters
    ----------
    profile:
        1-D NumPy array or plain Python list of edge-projection values.
    period:
        Detected period in pixels.

    Returns
    -------
    int
        Best phase offset in ``[0, period)``.
    """
    if period <= 0:
        return 0
    if _HAVE_NUMPY and hasattr(profile, "dtype"):
        best, best_o = -1e18, 0
        for o in range(period):
            idx = np.arange(o, len(profile), period)
            s   = float(profile[idx].sum())
            if s > best:
                best, best_o = s, o
        return best_o
    # Pure-Python fallback
    best, best_o = -1e18, 0
    for o in range(period):
        s = sum(profile[i] for i in range(o, len(profile), period))
        if s > best:
            best, best_o = s, o
    return best_o


# ---------------------------------------------------------------------------
# Period resolution
# ---------------------------------------------------------------------------

def _resolve_period(
    size: int,
    mod_p: int,
    mod_score: float,
    mic_p: int,
    mic_score: float,
    config: MicrogridConfig,
) -> "tuple[int, int]":
    """Choose between module-period and micro-period interpretations.

    Both ``mod_p`` (module period) and ``mic_p * micro_cells`` (inferred
    module period from micro-cell period) are evaluated by how evenly they
    divide *size* into an integer count in
    ``[config.min_grid_size, config.max_grid_size]``.  The interpretation
    with the better grid-quality score is returned.

    Parameters
    ----------
    size:
        Image dimension in pixels (width for cols, height for rows).
    mod_p:
        Detected module period (pixels per module tile).
    mod_score:
        FFT magnitude for *mod_p* (used as tiebreaker).
    mic_p:
        Detected micro-cell period (pixels per micro-cell).
    mic_score:
        FFT magnitude for *mic_p* (used as tiebreaker).
    config:
        Microgrid configuration.

    Returns
    -------
    (module_step, micro_step)
        Both values are integers in pixels.
    """
    min_gs      = config.min_grid_size
    max_gs      = config.max_grid_size
    micro_cells = config.micro_cells

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
    return mic_p * micro_cells, mic_p


# ---------------------------------------------------------------------------
# Microgrid-first window search
# ---------------------------------------------------------------------------

def search_microgrid_first(
    image: "Image.Image",
    config: Optional[MicrogridConfig] = None,
) -> "tuple[Optional[tuple], float]":
    """Scan a coarse grid of windows and return the one with the best micro-grid signal.

    This is an alternative way to locate the map bbox: rather than relying on
    brightness or edge-contour detection, we score each candidate window by
    how strongly it exhibits the micro-grid periodicity and return the window
    whose combined X+Y periodicity score is highest.

    Parameters
    ----------
    image:
        Full-screen PIL Image.
    config:
        :class:`MicrogridConfig` instance.  Uses defaults when *None*.

    Returns
    -------
    (bbox, score)
        *bbox* is ``(x, y, w, h)`` of the best-scoring window, or *None*
        when no window yields a positive score.  *score* is the combined
        periodicity score.
    """
    if config is None:
        config = MicrogridConfig()

    iw, ih = image.size
    win_size = int(min(iw, ih) * max(0.1, min(1.0, config.mgf_window_frac)))
    if win_size < 32:
        return None, 0.0

    steps = max(1, config.mgf_search_steps)
    # Centre the search grid
    x_start = (iw - win_size) // 2
    y_start = (ih - win_size) // 2
    x_range = max(1, (iw - win_size) // 2)
    y_range = max(1, (ih - win_size) // 2)

    best_score = -1.0
    best_bbox: Optional[tuple] = None

    gray = image.convert("L")

    for si in range(steps):
        for sj in range(steps):
            ox = x_start - x_range + (2 * x_range * si) // max(steps - 1, 1)
            oy = y_start - y_range + (2 * y_range * sj) // max(steps - 1, 1)
            ox = max(0, min(iw - win_size, ox))
            oy = max(0, min(ih - win_size, oy))

            crop = gray.crop((ox, oy, ox + win_size, oy + win_size))

            if _HAVE_NUMPY:
                arr   = np.asarray(crop, dtype=np.float32)
                edges = sobel_edges(arr)
                proj_x = np.asarray(edges).sum(axis=0)
                proj_y = np.asarray(edges).sum(axis=1)
                _, score_x, _ = _detect_period_numpy_top10(
                    proj_x, config.min_micro, config.max_micro
                )
                _, score_y, _ = _detect_period_numpy_top10(
                    proj_y, config.min_micro, config.max_micro
                )
            else:
                edges_pil = crop.filter(ImageFilter.FIND_EDGES)
                pw, ph = edges_pil.size
                pix = list(edges_pil.getdata())
                proj_x_list = [sum(pix[r * pw + c] for r in range(ph)) for c in range(pw)]
                proj_y_list = [sum(pix[r * pw + c] for c in range(pw)) for r in range(ph)]
                _, score_x = _detect_period_pil(proj_x_list, config.min_micro, config.max_micro)
                _, score_y = _detect_period_pil(proj_y_list, config.min_micro, config.max_micro)

            combined = score_x + score_y
            if combined > best_score:
                best_score = combined
                best_bbox = (ox, oy, win_size, win_size)

    if best_score <= 0:
        return None, 0.0
    return best_bbox, best_score


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def detect_microgrid(
    map_img: "Image.Image",
    config: Optional[MicrogridConfig] = None,
) -> MicrogridResult:
    """Detect micro-grid spacing and phase from a cropped map image.

    The function computes Sobel edge magnitude, sums it along each axis to
    produce 1-D profiles, runs FFT period detection in both the micro-cell
    and module period ranges, resolves which interpretation best fits an
    integer grid, and finds the best phase offset for alignment.

    Parameters
    ----------
    map_img:
        Greyscale (or any mode) PIL Image of just the map crop.
    config:
        :class:`MicrogridConfig` instance.  Uses defaults when *None*.

    Returns
    -------
    :class:`MicrogridResult`
    """
    if config is None:
        config = MicrogridConfig()

    log: list = []
    W, H = map_img.size

    min_micro  = config.min_micro
    max_micro  = config.max_micro
    min_module = config.min_module
    max_module = config.max_module

    gray = map_img.convert("L")

    if _HAVE_NUMPY:
        arr   = np.asarray(gray, dtype=np.float32)
        edges = sobel_edges(arr)
        edges_arr = np.asarray(edges)
        proj_x = edges_arr.sum(axis=0)   # shape (W,)
        proj_y = edges_arr.sum(axis=1)   # shape (H,)

        # Module period detection
        mod_px, mod_sx, mod_scores_x = _detect_period_numpy_top10(
            proj_x, min_module, min(max_module, W // 2)
        )
        mod_py, mod_sy, mod_scores_y = _detect_period_numpy_top10(
            proj_y, min_module, min(max_module, H // 2)
        )
        # Micro-cell period detection
        mic_px, mic_sx, mic_scores_x = _detect_period_numpy_top10(
            proj_x, min_micro, max_micro
        )
        mic_py, mic_sy, mic_scores_y = _detect_period_numpy_top10(
            proj_y, min_micro, max_micro
        )

        # Apply forced periods
        if config.force_module_period > 0:
            mod_px = mod_py = config.force_module_period
            log.append(f"force_module_period={config.force_module_period}")
        if config.force_micro_period > 0:
            mic_px = mic_py = config.force_micro_period
            log.append(f"force_micro_period={config.force_micro_period}")

        log.append(
            f"module_period x={mod_px}(score={mod_sx:.1f}) y={mod_py}(score={mod_sy:.1f})"
        )
        log.append(
            f"micro_period  x={mic_px}(score={mic_sx:.1f}) y={mic_py}(score={mic_sy:.1f})"
        )

        step_x, micro_step_x = _resolve_period(W, mod_px, mod_sx, mic_px, mic_sx, config)
        step_y, micro_step_y = _resolve_period(H, mod_py, mod_sy, mic_py, mic_sy, config)

        offset_x = _find_best_offset(proj_x, max(1, micro_step_x))
        offset_y = _find_best_offset(proj_y, max(1, micro_step_y))

        log.append(
            f"resolved step x={step_x} micro_x={micro_step_x} offset_x={offset_x}"
        )
        log.append(
            f"resolved step y={step_y} micro_y={micro_step_y} offset_y={offset_y}"
        )

        mgf_bbox: Optional[tuple] = None
        mgf_score = 0.0
        if config.use_microgrid_first:
            mgf_bbox, mgf_score = search_microgrid_first(map_img, config)
            log.append(f"microgrid_first bbox={mgf_bbox} score={mgf_score:.1f}")

        return MicrogridResult(
            module_step_x=step_x,
            module_step_y=step_y,
            micro_step_x=micro_step_x,
            micro_step_y=micro_step_y,
            offset_x=offset_x,
            offset_y=offset_y,
            periodicity_score_x=mic_sx,
            periodicity_score_y=mic_sy,
            mgf_bbox=mgf_bbox,
            mgf_score=mgf_score,
            log=log,
            _proj_x=proj_x,
            _proj_y=proj_y,
            _edges=edges_arr,
        )

    # ------------------------------------------------------------------
    # PIL-only fallback
    # ------------------------------------------------------------------
    edges_pil = gray.filter(ImageFilter.FIND_EDGES)
    W2, H2    = edges_pil.size
    pixels    = list(edges_pil.getdata())
    proj_x2: list = [0.0] * W2
    proj_y2: list = [0.0] * H2
    for r in range(H2):
        for c in range(W2):
            v = pixels[r * W2 + c]
            proj_x2[c] += v
            proj_y2[r] += v

    mod_px, mod_sx = _detect_period_pil(proj_x2, min_module, min(max_module, W2 // 2))
    mod_py, mod_sy = _detect_period_pil(proj_y2, min_module, min(max_module, H2 // 2))
    mic_px, mic_sx = _detect_period_pil(proj_x2, min_micro, max_micro)
    mic_py, mic_sy = _detect_period_pil(proj_y2, min_micro, max_micro)

    if config.force_module_period > 0:
        mod_px = mod_py = config.force_module_period
        log.append(f"force_module_period={config.force_module_period}")
    if config.force_micro_period > 0:
        mic_px = mic_py = config.force_micro_period
        log.append(f"force_micro_period={config.force_micro_period}")

    log.append(f"module_period x={mod_px}(score={mod_sx:.1f}) y={mod_py}(score={mod_sy:.1f})")
    log.append(f"micro_period  x={mic_px}(score={mic_sx:.1f}) y={mic_py}(score={mic_sy:.1f})")

    step_x, micro_step_x = _resolve_period(W2, mod_px, mod_sx, mic_px, mic_sx, config)
    step_y, micro_step_y = _resolve_period(H2, mod_py, mod_sy, mic_py, mic_sy, config)

    offset_x = _find_best_offset(proj_x2, max(1, micro_step_x))
    offset_y = _find_best_offset(proj_y2, max(1, micro_step_y))

    log.append(f"resolved step x={step_x} micro_x={micro_step_x} offset_x={offset_x}")
    log.append(f"resolved step y={step_y} micro_y={micro_step_y} offset_y={offset_y}")

    mgf_bbox = None
    mgf_score = 0.0
    if config.use_microgrid_first:
        mgf_bbox, mgf_score = search_microgrid_first(map_img, config)
        log.append(f"microgrid_first bbox={mgf_bbox} score={mgf_score:.1f}")

    return MicrogridResult(
        module_step_x=step_x,
        module_step_y=step_y,
        micro_step_x=micro_step_x,
        micro_step_y=micro_step_y,
        offset_x=offset_x,
        offset_y=offset_y,
        periodicity_score_x=mic_sx,
        periodicity_score_y=mic_sy,
        mgf_bbox=mgf_bbox,
        mgf_score=mgf_score,
        log=log,
        _proj_x=proj_x2,
        _proj_y=proj_y2,
        _edges=None,
    )
