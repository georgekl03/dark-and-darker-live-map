"""detection/bbox.py – Map bounding-box detection.

Two complementary strategies are provided:

1. **Dark-box method** (``_find_by_dark_box``) – the original V2 approach:
   slide a centred square window at decreasing sizes; accept the first whose
   mean/median/trimmed-mean brightness is below ``dark_thresh``.

2. **Edge-contour method** (``_find_by_edge_contour``) – NEW: detect the
   *bright* outer border of the map overlay by computing Canny edges (or PIL
   FIND_EDGES), finding contours, and scoring each contour by centre-distance,
   area, aspect ratio, solidity, and brightness contrast.

``find_map_bbox`` tries the edge-contour method first (when
``config.use_edge_contour`` is True) and falls back to the dark-box method if
it fails.

All public functions are pure (no UI, no global state beyond optional
module-level import caches).
"""

from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path
from typing import List, Optional

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

try:
    import cv2 as _cv2
    _HAVE_CV2 = True
except ImportError:
    _cv2 = None  # type: ignore[assignment]
    _HAVE_CV2 = False

from .preprocess import sobel_edges


# ---------------------------------------------------------------------------
# Configuration dataclass
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class BboxConfig:
    """Tuning parameters for map bounding-box detection.

    Dark-box method (V1/V2 compatible)
    ------------------------------------
    dark_thresh : int
        Maximum acceptable mean pixel brightness inside the candidate
        window; windows darker than this are considered map candidates.
    bbox_method : str
        Brightness metric used to score candidate windows.  One of
        ``"mean"``, ``"median"``, ``"trimmed_mean"``, or ``"edge"``.
    search_margin : float
        Fraction of screen width/height to ignore at each edge when
        building candidate windows (avoids UI chrome).
    min_frac, max_frac, frac_step : float
        Range and step for the sliding-scale window-size search, expressed
        as a fraction of the search-region dimension.
    prefer_darkest : bool
        When *True*, skip threshold gating and simply pick the darkest
        (or highest-edge) candidate.

    Edge/contour method (new)
    --------------------------
    use_edge_contour : bool
        When *True*, attempt the edge+contour method before dark-box.
    canny_low, canny_high : int
        Thresholds for ``cv2.Canny`` (only used when cv2 is available).
    min_border_brightness : int
        The outer border region must have mean brightness at least this
        value to be considered a valid map border.
    contour_min_area_frac, contour_max_area_frac : float
        Contour area must lie in this range (as a fraction of the full
        image area) to be considered a map candidate.
    contour_min_solidity : float
        Minimum solidity (area / convex-hull-area) a contour must have.

    Refinement
    -----------
    bbox_refine : bool
        When *True*, snap the seed bbox outward using edge projections.
    bbox_refine_band_pct : float
        Width of the edge-projection band, as a fraction of the crop dimension.
    bbox_refine_max_expand_pct : float
        Maximum outward expansion per side, as a fraction of the crop dimension.
    bbox_refine_edge_quantile : float
        Quantile of the edge projection used to compute the threshold.

    Bypass
    ------
    bypass_bbox : bool
        Skip all detection; return a centred crop of ``bypass_crop_pct``
        of the image.
    bypass_crop_pct : float
        Fraction of the image to use for the bypass central crop.

    Preset save/load
    ----------------
    preset_name : str
        Logical name used when saving/loading presets.
    """

    # Dark-box method
    dark_thresh: int = 65
    bbox_method: str = "mean"       # "mean" | "median" | "trimmed_mean" | "edge"
    search_margin: float = 0.05
    min_frac: float = 0.20
    max_frac: float = 0.90
    frac_step: float = 0.05
    prefer_darkest: bool = False

    # Edge/contour method
    use_edge_contour: bool = True
    canny_low: int = 30
    canny_high: int = 100
    min_border_brightness: int = 80
    contour_min_area_frac: float = 0.05
    contour_max_area_frac: float = 0.85
    contour_min_solidity: float = 0.7

    # Refinement
    bbox_refine: bool = True
    bbox_refine_band_pct: float = 0.12
    bbox_refine_max_expand_pct: float = 0.10
    bbox_refine_edge_quantile: float = 0.85

    # Bypass
    bypass_bbox: bool = False
    bypass_crop_pct: float = 0.80

    # Preset
    preset_name: str = "default"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class BboxResult:
    """Outcome of :func:`find_map_bbox`.

    Attributes
    ----------
    bbox : tuple or None
        Final active ``(x, y, w, h)`` bounding box (seed or refined).
    seed_bbox : tuple or None
        The raw detected bbox before edge-snap refinement.
    refined_bbox : tuple or None
        The bbox after edge-snap refinement (may equal *seed_bbox* if
        refinement was not applied or made no change).
    candidates : list
        All candidate ``(x, y, w, h, metric)`` tuples from the dark-box
        method, or ``(x, y, w, h, score)`` from the edge-contour method.
    method_used : str
        Which method produced the final bbox: ``"edge_contour"``,
        ``"dark_box"``, or ``"bypass"``.
    log : list
        Human-readable debug log lines.
    ok : bool
        *True* when a bbox was found successfully.
    error : str
        Non-empty error description when *ok* is *False*.
    """

    bbox: Optional[tuple]
    seed_bbox: Optional[tuple]
    refined_bbox: Optional[tuple]
    candidates: list
    method_used: str
    log: list
    ok: bool
    error: str = ""


# ---------------------------------------------------------------------------
# Internal brightness helpers  (mirrors map_scanner_v2.py helpers)
# ---------------------------------------------------------------------------

def _mean_brightness(img: "Image.Image") -> float:
    if _HAVE_NUMPY:
        return float(np.asarray(img.convert("L"), dtype=np.float32).mean())
    data = list(img.convert("L").getdata())
    return sum(data) / max(len(data), 1)


def _median_brightness(img: "Image.Image") -> float:
    data = sorted(img.convert("L").getdata())
    n = len(data)
    if n == 0:
        return 0.0
    return (data[n // 2 - 1] + data[n // 2]) / 2 if n % 2 == 0 else float(data[n // 2])


def _trimmed_mean_brightness(img: "Image.Image", trim: float = 0.10) -> float:
    data = sorted(img.convert("L").getdata())
    n = len(data)
    cut = max(1, int(n * trim))
    trimmed = data[cut: n - cut]
    return sum(trimmed) / max(len(trimmed), 1)


def _edge_density(img: "Image.Image") -> float:
    if _HAVE_NUMPY:
        arr = np.asarray(img.convert("L"), dtype=np.float32)
        edges = sobel_edges(arr)
        return float(np.asarray(edges).mean()) / 255.0
    edged = img.convert("L").filter(ImageFilter.FIND_EDGES)
    data = list(edged.getdata())
    return sum(data) / max(len(data), 1) / 255.0


def _brightness_metric(patch: "Image.Image", method: str) -> float:
    if method == "median":
        return _median_brightness(patch)
    if method == "trimmed_mean":
        return _trimmed_mean_brightness(patch)
    if method == "edge":
        return _edge_density(patch)
    return _mean_brightness(patch)


# ---------------------------------------------------------------------------
# Dark-box method
# ---------------------------------------------------------------------------

def _find_by_dark_box(
    gray: "Image.Image",
    config: BboxConfig,
    log: list,
) -> "tuple[Optional[tuple], list]":
    """Locate the map area using the centred sliding-scale dark-window heuristic.

    Slides a square window at decreasing fractions of the search region,
    computes a brightness metric for each, and returns the first window
    whose metric falls below ``config.dark_thresh`` (or the darkest/
    highest-edge window when ``config.prefer_darkest`` is True).

    Parameters
    ----------
    gray:
        Full-screen greyscale PIL Image.
    config:
        Detection configuration.
    log:
        List to append debug log lines to (mutated in place).

    Returns
    -------
    (seed_bbox, candidates)
        *seed_bbox* is ``(x, y, w, h)`` or *None*.
        *candidates* is a list of ``(x, y, w, h, metric)`` tuples for all
        windows that were evaluated.
    """
    sw, sh = gray.size
    margin = max(0.0, min(0.25, config.search_margin))
    sx0 = int(sw * margin)
    sy0 = int(sh * margin)
    sx1 = int(sw * (1 - margin))
    sy1 = int(sh * (1 - margin))
    sw2, sh2 = sx1 - sx0, sy1 - sy0

    step = max(0.01, config.frac_step)
    frac = config.max_frac
    fracs: list = []
    while frac >= config.min_frac - 1e-9:
        fracs.append(round(frac, 6))
        frac -= step

    method = config.bbox_method
    thresh = config.dark_thresh
    candidates: list = []

    for frac in fracs:
        side = int(min(sw2, sh2) * frac)
        if side < 32:
            continue
        cx = sx0 + (sw2 - side) // 2
        cy = sy0 + (sh2 - side) // 2
        patch = gray.crop((cx, cy, cx + side, cy + side))
        metric = _brightness_metric(patch, method)
        candidates.append((cx, cy, side, side, metric))
        log.append(f"dark_box frac={frac:.2f} side={side} {method}={metric:.1f}")

    if not candidates:
        log.append("dark_box: no candidates (all patches too small)")
        return None, candidates

    seed_bbox: Optional[tuple] = None

    if config.prefer_darkest:
        if method == "edge":
            best = max(candidates, key=lambda c: c[4])
        else:
            best = min(candidates, key=lambda c: c[4])
        cx, cy, cw, ch, metric = best
        log.append(f"dark_box prefer_darkest: metric={metric:.1f} at ({cx},{cy})")
        seed_bbox = (cx, cy, cw, ch)

    elif method == "edge":
        edge_thresh = thresh / 255.0 * 0.5
        for cand in candidates:
            cx, cy, cw, ch, metric = cand
            if metric > edge_thresh:
                log.append(f"dark_box edge accepted: density={metric:.4f} > {edge_thresh:.4f}")
                seed_bbox = (cx, cy, cw, ch)
                break
        if seed_bbox is None:
            best = max(candidates, key=lambda c: c[4])
            cx, cy, cw, ch, metric = best
            log.append(f"dark_box edge fallback: best density={metric:.4f}")
            seed_bbox = (cx, cy, cw, ch)

    else:
        for cand in candidates:
            cx, cy, cw, ch, metric = cand
            if metric < thresh:
                log.append(f"dark_box accepted: {method}={metric:.1f} < {thresh}")
                seed_bbox = (cx, cy, cw, ch)
                break
        if seed_bbox is None:
            log.append(
                f"dark_box FAILED: all {len(candidates)} candidates above threshold {thresh}"
            )

    return seed_bbox, candidates


# ---------------------------------------------------------------------------
# Edge-contour method (new)
# ---------------------------------------------------------------------------

def _find_by_edge_contour(
    image: "Image.Image",
    config: BboxConfig,
    log: list,
) -> "tuple[Optional[tuple], list]":
    """Locate the map area by finding the bright outer border via edge+contour.

    The map overlay has a distinctly bright square border against a dark
    background.  This function:

    1. Converts the image to greyscale.
    2. Computes Canny edges (cv2) or PIL FIND_EDGES.
    3. (cv2 path) Finds contours, filters them by area, aspect ratio, and
       solidity, scores each by closeness to image centre and brightness
       contrast, and returns the bounding rect of the best candidate.
    4. (no-cv2 fallback) Thresholds the image at ``config.min_border_brightness``
       to isolate bright regions, finds the largest connected region near the
       centre, and returns its bounding box.

    Parameters
    ----------
    image:
        Full-screen RGB (or any mode) PIL Image.
    config:
        Detection configuration.
    log:
        List to append debug log lines to (mutated in place).

    Returns
    -------
    (seed_bbox, candidates)
        *seed_bbox* is ``(x, y, w, h)`` or *None*.
        *candidates* is a list of ``(x, y, w, h, score)`` tuples for every
        contour that passed the area filter.
    """
    iw, ih = image.size
    image_area = iw * ih
    cx_img = iw / 2.0
    cy_img = ih / 2.0
    candidates: list = []

    gray_pil = image.convert("L")

    # ------------------------------------------------------------------
    # cv2 path
    # ------------------------------------------------------------------
    if _HAVE_CV2 and _HAVE_NUMPY:
        gray_arr = np.asarray(gray_pil, dtype=np.uint8)
        edges = _cv2.Canny(gray_arr, config.canny_low, config.canny_high)

        contours, _ = _cv2.findContours(edges, _cv2.RETR_EXTERNAL, _cv2.CHAIN_APPROX_SIMPLE)
        log.append(f"edge_contour: found {len(contours)} raw contours (cv2)")

        for cnt in contours:
            area = float(_cv2.contourArea(cnt))
            area_frac = area / max(image_area, 1)
            if area_frac < config.contour_min_area_frac:
                continue
            if area_frac > config.contour_max_area_frac:
                continue

            # Aspect ratio check
            rx, ry, rw, rh = _cv2.boundingRect(cnt)
            aspect = rw / max(rh, 1)
            if not (0.6 <= aspect <= 1.4):
                continue

            # Solidity check
            hull = _cv2.convexHull(cnt)
            hull_area = float(_cv2.contourArea(hull))
            solidity = area / max(hull_area, 1.0)
            if solidity < config.contour_min_solidity:
                continue

            # Centre-distance score (0=centre, higher=off-centre)
            rect_cx = rx + rw / 2.0
            rect_cy = ry + rh / 2.0
            max_dist = math.sqrt(cx_img ** 2 + cy_img ** 2)
            dist = math.sqrt((rect_cx - cx_img) ** 2 + (rect_cy - cy_img) ** 2)
            centre_score = 1.0 - dist / max(max_dist, 1.0)

            # Brightness contrast: mean inside vs mean outside
            mask = np.zeros(gray_arr.shape, dtype=np.uint8)
            _cv2.drawContours(mask, [cnt], -1, 255, thickness=_cv2.FILLED)
            inner_mean = float(gray_arr[mask == 255].mean()) if mask.any() else 0.0
            outer_mean = float(gray_arr[mask == 0].mean())
            contrast_score = (inner_mean - outer_mean) / 255.0

            # Area score: larger is better (up to a point)
            area_score = min(area_frac / config.contour_max_area_frac, 1.0)

            # Combined score
            score = centre_score * 0.4 + contrast_score * 0.4 + area_score * 0.2

            candidates.append((rx, ry, rw, rh, score))
            log.append(
                f"  contour bbox=({rx},{ry},{rw},{rh}) area_frac={area_frac:.3f} "
                f"aspect={aspect:.2f} solidity={solidity:.2f} "
                f"centre={centre_score:.2f} contrast={contrast_score:.2f} "
                f"score={score:.3f}"
            )

        if candidates:
            best = max(candidates, key=lambda c: c[4])
            rx, ry, rw, rh, score = best
            log.append(
                f"edge_contour best: ({rx},{ry},{rw},{rh}) score={score:.3f}"
            )
            return (rx, ry, rw, rh), candidates

        log.append("edge_contour (cv2): no contours passed all filters")
        return None, candidates

    # ------------------------------------------------------------------
    # No-cv2 fallback: threshold bright regions, find largest near centre
    # ------------------------------------------------------------------
    log.append("edge_contour: cv2 not available, using brightness-threshold fallback")

    if _HAVE_NUMPY:
        gray_arr = np.asarray(gray_pil, dtype=np.uint8)
        bright_mask = (gray_arr >= config.min_border_brightness).astype(np.uint8)

        # Label connected components via a simple flood-fill free scan:
        # We use a row-major labelling using scipy if available, else a
        # crude bounding-box scan of contiguous bright horizontal runs.
        try:
            from scipy import ndimage as _ndi
            labelled, num_features = _ndi.label(bright_mask)
            best_score = -1.0
            best_box: Optional[tuple] = None
            for label_id in range(1, num_features + 1):
                rows, cols = np.where(labelled == label_id)
                if len(rows) == 0:
                    continue
                ry0, ry1 = int(rows.min()), int(rows.max())
                rx0, rx1 = int(cols.min()), int(cols.max())
                rw = rx1 - rx0 + 1
                rh = ry1 - ry0 + 1
                area_frac = (rw * rh) / max(image_area, 1)
                if area_frac < config.contour_min_area_frac:
                    continue
                if area_frac > config.contour_max_area_frac:
                    continue
                aspect = rw / max(rh, 1)
                if not (0.6 <= aspect <= 1.4):
                    continue
                rect_cx = rx0 + rw / 2.0
                rect_cy = ry0 + rh / 2.0
                dist = math.sqrt((rect_cx - cx_img) ** 2 + (rect_cy - cy_img) ** 2)
                max_dist = math.sqrt(cx_img ** 2 + cy_img ** 2)
                centre_score = 1.0 - dist / max(max_dist, 1.0)
                score = centre_score * 0.5 + area_frac * 0.5
                candidates.append((rx0, ry0, rw, rh, score))
                if score > best_score:
                    best_score = score
                    best_box = (rx0, ry0, rw, rh)
            if best_box:
                log.append(
                    f"edge_contour (scipy fallback) best: {best_box} score={best_score:.3f}"
                )
                return best_box, candidates
        except ImportError:
            pass

        # Last resort: single bounding box of all bright pixels
        bright_rows, bright_cols = np.where(bright_mask > 0)
        if len(bright_rows) > 0:
            ry0, ry1 = int(bright_rows.min()), int(bright_rows.max())
            cx0, cx1 = int(bright_cols.min()), int(bright_cols.max())
            rw = cx1 - cx0 + 1
            rh = ry1 - ry0 + 1
            area_frac = (rw * rh) / max(image_area, 1)
            if (
                config.contour_min_area_frac <= area_frac <= config.contour_max_area_frac
                and 0.6 <= rw / max(rh, 1) <= 1.4
            ):
                log.append(f"edge_contour (numpy threshold) bbox: ({cx0},{ry0},{rw},{rh})")
                candidates.append((cx0, ry0, rw, rh, 0.5))
                return (cx0, ry0, rw, rh), candidates

    # Pure-PIL fallback: threshold via point, find bounding box
    binary = gray_pil.point(lambda v: 255 if v >= config.min_border_brightness else 0, "L")
    bbox_pil = binary.getbbox()
    if bbox_pil is not None:
        bx0, by0, bx1, by1 = bbox_pil
        bw = bx1 - bx0
        bh = by1 - by0
        area_frac = (bw * bh) / max(image_area, 1)
        aspect = bw / max(bh, 1)
        if (
            config.contour_min_area_frac <= area_frac <= config.contour_max_area_frac
            and 0.6 <= aspect <= 1.4
        ):
            log.append(f"edge_contour (PIL threshold) bbox: ({bx0},{by0},{bw},{bh})")
            candidates.append((bx0, by0, bw, bh, 0.5))
            return (bx0, by0, bw, bh), candidates

    log.append("edge_contour (fallback): no suitable bright region found")
    return None, candidates


# ---------------------------------------------------------------------------
# Edge-snap refinement
# ---------------------------------------------------------------------------

def _refine_bbox_by_edges(
    gray: "Image.Image",
    seed: tuple,
    config: BboxConfig,
    log: list,
) -> Optional[tuple]:
    """Snap each side of *seed* outward to strong edge projections.

    Crops the region defined by *seed*, computes a Sobel edge image, builds
    1-D projections near each side, then walks each boundary outward until
    the projection drops below a threshold.

    Parameters
    ----------
    gray:
        Full-screen greyscale PIL Image.
    seed:
        ``(x, y, w, h)`` seed bounding box.
    config:
        Detection configuration.
    log:
        Log list (mutated in place).

    Returns
    -------
    Refined ``(x, y, w, h)`` tuple, or *None* if refinement fails.
    """
    x0, y0, w0, h0 = seed
    sw, sh = gray.size
    band_pct = max(0.02, min(0.49, config.bbox_refine_band_pct))
    max_exp_pct = max(0.0, min(0.40, config.bbox_refine_max_expand_pct))
    quantile = max(0.50, min(0.99, config.bbox_refine_edge_quantile))

    log.append(f"  refine seed: ({x0},{y0},{w0}×{h0})")

    try:
        crop = gray.crop((x0, y0, x0 + w0, y0 + h0))

        if _HAVE_NUMPY:
            arr = np.asarray(crop, dtype=np.float32)
            edges = sobel_edges(arr)
            H, W = edges.shape
            band_x = max(2, int(W * band_pct))
            band_y = max(2, int(H * band_pct))
            max_dx = max(0, int(W * max_exp_pct))
            max_dy = max(0, int(H * max_exp_pct))

            left_proj   = edges[:, :band_x].sum(axis=0)
            right_proj  = edges[:, W - band_x:].sum(axis=0)
            top_proj    = edges[:band_y, :].sum(axis=1)
            bottom_proj = edges[H - band_y:, :].sum(axis=1)

            def _q(arr_1d: "np.ndarray") -> float:
                flat = arr_1d.flatten().copy()
                flat.sort()
                idx = min(len(flat) - 1, int(len(flat) * quantile))
                return float(flat[idx])

            def _find_boundary(proj: "np.ndarray", inward: bool) -> int:
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

            dl = min(max_dx, _find_boundary(left_proj,   False))
            dr = min(max_dx, _find_boundary(right_proj,  True))
            dt = min(max_dy, _find_boundary(top_proj,    False))
            db = min(max_dy, _find_boundary(bottom_proj, True))

            nx0 = max(0,      x0 - dl)
            ny0 = max(0,      y0 - dt)
            nx1 = min(sw - 1, x0 + w0 + dr)
            ny1 = min(sh - 1, y0 + h0 + db)
            nw  = max(1, nx1 - nx0)
            nh  = max(1, ny1 - ny0)
            log.append(
                f"  refine deltas: left={dl} right={dr} top={dt} bot={db}"
                f"  → ({nx0},{ny0},{nw}×{nh})"
            )
            return (nx0, ny0, nw, nh)

        # PIL fallback
        epil = crop.filter(ImageFilter.FIND_EDGES)
        W2, H2 = epil.size
        pix = list(epil.getdata())

        band_x = max(2, int(W2 * band_pct))
        band_y = max(2, int(H2 * band_pct))
        max_dx = max(0, int(W2 * max_exp_pct))
        max_dy = max(0, int(H2 * max_exp_pct))

        left_proj2  = [sum(pix[r * W2 + c] for r in range(H2)) for c in range(band_x)]
        right_proj2 = [sum(pix[r * W2 + (W2 - band_x + c)] for r in range(H2)) for c in range(band_x)]
        top_proj2   = [sum(pix[r * W2 + c] for c in range(W2)) for r in range(band_y)]
        bot_proj2   = [sum(pix[(H2 - band_y + r) * W2 + c] for c in range(W2)) for r in range(band_y)]

        def _q2(arr_1d: list) -> float:
            s = sorted(arr_1d)
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

        nx0 = max(0,      x0 - dl)
        ny0 = max(0,      y0 - dt)
        nx1 = min(sw - 1, x0 + w0 + dr)
        ny1 = min(sh - 1, y0 + h0 + db)
        nw  = max(1, nx1 - nx0)
        nh  = max(1, ny1 - ny0)
        log.append(
            f"  refine (PIL) deltas: left={dl} right={dr} top={dt} bot={db}"
            f"  → ({nx0},{ny0},{nw}×{nh})"
        )
        return (nx0, ny0, nw, nh)

    except Exception as exc:
        log.append(f"  refine FAILED ({exc}); keeping seed")
        return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def find_map_bbox(
    image: "Image.Image",
    config: Optional[BboxConfig] = None,
) -> BboxResult:
    """Locate the dungeon-map overlay in *image* and return a :class:`BboxResult`.

    Detection order (when ``config.use_edge_contour`` is *True*):

    1. **Edge-contour method** – find the bright square border via Canny
       edges + contour filtering.  Fast and robust when the map is clearly
       visible against a dark background.
    2. **Dark-box method** – centred sliding-scale window heuristic.  Used
       as fallback when the edge-contour method fails.

    After a seed bbox is found, edge-snap refinement is applied when
    ``config.bbox_refine`` is *True*.

    Parameters
    ----------
    image:
        Full-screen PIL Image (RGB, RGBA, or L).
    config:
        :class:`BboxConfig` instance.  Defaults to ``BboxConfig()`` when
        *None*.

    Returns
    -------
    :class:`BboxResult`
    """
    if config is None:
        config = BboxConfig()

    log: list = []
    gray = image.convert("L")
    sw, sh = image.size

    # ------------------------------------------------------------------
    # Bypass mode
    # ------------------------------------------------------------------
    if config.bypass_bbox:
        frac = max(0.1, min(1.0, config.bypass_crop_pct))
        cw   = int(sw * frac)
        ch   = int(sh * frac)
        cx   = (sw - cw) // 2
        cy   = (sh - ch) // 2
        bbox = (cx, cy, cw, ch)
        log.append(
            f"bypass_bbox=True: central {frac * 100:.0f}% crop ({cx},{cy},{cw}×{ch})"
        )
        return BboxResult(
            bbox=bbox,
            seed_bbox=bbox,
            refined_bbox=None,
            candidates=[],
            method_used="bypass",
            log=log,
            ok=True,
        )

    # ------------------------------------------------------------------
    # Edge-contour method
    # ------------------------------------------------------------------
    seed_bbox: Optional[tuple] = None
    all_candidates: list = []
    method_used = "dark_box"

    if config.use_edge_contour:
        ec_seed, ec_cands = _find_by_edge_contour(image, config, log)
        all_candidates.extend(ec_cands)
        if ec_seed is not None:
            seed_bbox = ec_seed
            method_used = "edge_contour"

    # ------------------------------------------------------------------
    # Dark-box fallback
    # ------------------------------------------------------------------
    if seed_bbox is None:
        db_seed, db_cands = _find_by_dark_box(gray, config, log)
        all_candidates.extend(db_cands)
        if db_seed is not None:
            seed_bbox = db_seed
            method_used = "dark_box"

    if seed_bbox is None:
        return BboxResult(
            bbox=None,
            seed_bbox=None,
            refined_bbox=None,
            candidates=all_candidates,
            method_used=method_used,
            log=log,
            ok=False,
            error=(
                "Could not detect the dungeon map area. "
                "Ensure the in-game map overlay is open and fully visible."
            ),
        )

    # ------------------------------------------------------------------
    # Edge-snap refinement
    # ------------------------------------------------------------------
    refined_bbox: Optional[tuple] = None
    if config.bbox_refine:
        refined_bbox = _refine_bbox_by_edges(gray, seed_bbox, config, log)

    final_bbox = refined_bbox if refined_bbox is not None else seed_bbox

    return BboxResult(
        bbox=final_bbox,
        seed_bbox=seed_bbox,
        refined_bbox=refined_bbox,
        candidates=all_candidates,
        method_used=method_used,
        log=log,
        ok=True,
    )


# ---------------------------------------------------------------------------
# Preset save/load
# ---------------------------------------------------------------------------

def save_preset(config: BboxConfig, name: str, path: "Path | str") -> None:
    """Persist a :class:`BboxConfig` as a JSON preset.

    Parameters
    ----------
    config:
        Configuration to save.
    name:
        Logical preset name (stored in ``preset_name`` field and used as
        the key inside the JSON file).
    path:
        File system path to the JSON presets file.
    """
    p = Path(path)
    try:
        with open(p) as f:
            data = json.load(f)
    except Exception:
        data = {}

    d = dataclasses.asdict(config)
    d["preset_name"] = name
    data[name] = d

    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        json.dump(data, f, indent=2)


def load_preset(name: str, path: "Path | str") -> Optional[BboxConfig]:
    """Load a :class:`BboxConfig` preset by name.

    Parameters
    ----------
    name:
        Preset name (key inside the JSON file).
    path:
        File system path to the JSON presets file.

    Returns
    -------
    :class:`BboxConfig` or *None* when the preset is not found.
    """
    try:
        with open(Path(path)) as f:
            data = json.load(f)
        d = data[name]
        fields = {fld.name for fld in dataclasses.fields(BboxConfig)}
        return BboxConfig(**{k: v for k, v in d.items() if k in fields})
    except Exception:
        return None


def list_presets(path: "Path | str") -> List[str]:
    """Return the names of all presets stored in *path*.

    Parameters
    ----------
    path:
        File system path to the JSON presets file.

    Returns
    -------
    Sorted list of preset name strings (empty when the file doesn't exist).
    """
    try:
        with open(Path(path)) as f:
            data = json.load(f)
        return sorted(data.keys())
    except Exception:
        return []
