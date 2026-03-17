#!/usr/bin/env python3
"""
cursor_detect.py  -  Shared cursor detection logic for Dark & Darker minimap tracker
=====================================================================================
Provides:
  * Settings management (load/save data/minimap_settings.json)
  * find_green_dot()         - robust HSV-based green pivot detection
  * build_outline_mask()     - dark-pixel cursor outline extraction
  * find_direction_circles() - circle-intersection direction estimation
  * raycast_tip()            - farthest cursor pixel and stable tracking point
  * smooth_angle() / smooth_pos() - EMA temporal smoothing

All detection functions accept a "params" sub-dict whose keys match the
corresponding section in DEFAULT_MINIMAP_SETTINGS, so callers can pass
settings["green_dot"], settings["outline"], etc. directly.

Dependencies: Pillow  (numpy optional but strongly recommended for speed)
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Optional fast-path: numpy
# ---------------------------------------------------------------------------
try:
    import numpy as np
    _HAVE_NUMPY = True
except ImportError:
    _HAVE_NUMPY = False

try:
    from PIL import Image
    _HAVE_PIL = True
except ImportError:
    _HAVE_PIL = False

# ---------------------------------------------------------------------------
# Settings paths
# ---------------------------------------------------------------------------
_HERE                 = Path(__file__).parent
MINIMAP_SETTINGS_PATH = _HERE / "data" / "minimap_settings.json"

DEFAULT_MINIMAP_SETTINGS: dict = {
    "version": 2,
    "roi": {
        "left":   1700,
        "top":    860,
        "width":  220,
        "height": 220,
    },
    "green_dot": {
        # OpenCV-style HSV: H 0-179, S 0-255, V 0-255
        "h_min":        40,
        "h_max":        90,
        "s_min":        80,
        "s_max":        255,
        "v_min":        60,
        "v_max":        255,
        "morph_kernel": 2,   # morphological close kernel half-size
        "min_area":     4,   # px2 - discard smaller blobs
        "max_area":     400, # px2 - discard larger false blobs
    },
    "outline": {
        "dark_thresh":  60,  # pixels with grey < this = black outline
        "local_radius": 30,  # half-size of window around pivot (px)
        "morph_kernel": 2,   # morphological close kernel half-size
    },
    "direction": {
        "r1":          10.0,  # inner sampling circle radius (px)
        "r2":          18.0,  # outer sampling circle radius (px)
        "samples":     90,    # sample points per circle
        "cluster_gap": 20.0,  # angular gap (deg) that separates clusters
        "min_hits":    2,     # minimum hits to form a valid cluster
    },
    "tip": {
        "enabled":     True,
        "raycast_max": 35,   # max raycast distance from pivot (px)
        "track_dist":  3.0,  # stable tracking point dist from pivot (px)
    },
    "smoothing": {
        "heading_alpha":     0.4,   # EMA weight for newest heading sample
        "pivot_alpha":       0.5,   # EMA weight for newest pivot position
        "max_heading_delta": 60.0,  # max per-frame heading change (deg)
    },
    "overlays": {
        "show_green_mask":   True,
        "show_outline_mask": True,
        "show_pivot":        True,
        "show_circles":      True,
        "show_hits":         True,
        "show_bisector":     True,
        "show_heading":      True,
        "show_tip":          True,
        "show_debug_text":   True,
    },
}


# ---------------------------------------------------------------------------
# Settings management
# ---------------------------------------------------------------------------
def _deep_copy(d):
    """Simple recursive dict/list deep copy (avoids importing copy)."""
    if isinstance(d, dict):
        return {k: _deep_copy(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_deep_copy(v) for v in d]
    return d


def load_minimap_settings() -> dict:
    """Load settings from MINIMAP_SETTINGS_PATH, merging with defaults."""
    s = _deep_copy(DEFAULT_MINIMAP_SETTINGS)
    try:
        with open(MINIMAP_SETTINGS_PATH, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        # Deep-merge: saved values override defaults at all levels
        def _merge(base, override):
            for k, v in override.items():
                if isinstance(v, dict) and isinstance(base.get(k), dict):
                    _merge(base[k], v)
                else:
                    base[k] = v
        _merge(s, saved)
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return s


def save_minimap_settings(s: dict) -> None:
    """Save settings dict to MINIMAP_SETTINGS_PATH (creates directory if needed)."""
    MINIMAP_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MINIMAP_SETTINGS_PATH, "w", encoding="utf-8") as fh:
        json.dump(s, fh, indent=2)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _rgb_to_hsv_pixel(r, g, b):
    """Convert a single RGB triple (0-255) to OpenCV-style HSV (H 0-179, S/V 0-255)."""
    r_, g_, b_ = r / 255.0, g / 255.0, b / 255.0
    cmax = max(r_, g_, b_)
    cmin = min(r_, g_, b_)
    delta = cmax - cmin

    v = cmax
    s = (delta / cmax) if cmax > 0 else 0.0

    if delta == 0:
        h_norm = 0.0
    elif cmax == r_:
        h_norm = ((g_ - b_) / delta) % 6
    elif cmax == g_:
        h_norm = (b_ - r_) / delta + 2
    else:
        h_norm = (r_ - g_) / delta + 4

    h_cv = (h_norm / 6.0) * 179.0  # OpenCV uses 0-179

    return int(round(h_cv)), int(round(s * 255)), int(round(v * 255))


def _mean_angle(angles_deg):
    """Circular mean of a list of angles in degrees."""
    if not angles_deg:
        return 0.0
    sin_s = sum(math.sin(math.radians(a)) for a in angles_deg)
    cos_s = sum(math.cos(math.radians(a)) for a in angles_deg)
    return math.degrees(math.atan2(sin_s, cos_s)) % 360.0


def _angle_diff(a, b):
    """Signed difference a-b wrapped to [-180, +180]."""
    d = (a - b) % 360.0
    if d > 180.0:
        d -= 360.0
    return d


# ---------------------------------------------------------------------------
# Green dot detection
# ---------------------------------------------------------------------------
def find_green_dot(img, params=None):
    """Detect the green pivot dot in a minimap image.

    The dot has a bright green centre fading to darker edges and is not a
    perfect circle.  We use HSV thresholding then pick the largest qualifying
    blob.

    Parameters
    ----------
    img    : PIL RGB Image (the minimap crop).
    params : dict with keys from DEFAULT_MINIMAP_SETTINGS["green_dot"].
             If None, uses defaults.

    Returns
    -------
    dict with:
      "center"     : (cx, cy) float pixel coords, or None
      "confidence" : 0-1 float (normalised blob area)
      "mask_img"   : PIL "L" image of the detected green mask (full image size)
    or None on failure.
    """
    if img is None:
        return None
    if params is None:
        params = DEFAULT_MINIMAP_SETTINGS["green_dot"]

    h_min = int(params.get("h_min", 40))
    h_max = int(params.get("h_max", 90))
    s_min = int(params.get("s_min", 80))
    s_max = int(params.get("s_max", 255))
    v_min = int(params.get("v_min", 60))
    v_max = int(params.get("v_max", 255))
    k     = max(0, int(params.get("morph_kernel", 2)))
    min_a = max(1, int(params.get("min_area", 4)))
    max_a = max(min_a + 1, int(params.get("max_area", 400)))

    w, h = img.size

    if _HAVE_NUMPY:
        arr = np.array(img, dtype=np.uint8)  # (H, W, 3) RGB
        # Convert to HSV via numpy
        r_f = arr[:, :, 0].astype(np.float32) / 255.0
        g_f = arr[:, :, 1].astype(np.float32) / 255.0
        b_f = arr[:, :, 2].astype(np.float32) / 255.0
        cmax = np.maximum(np.maximum(r_f, g_f), b_f)
        cmin = np.minimum(np.minimum(r_f, g_f), b_f)
        delta = cmax - cmin

        v_arr = cmax
        s_arr = np.where(cmax > 0, delta / np.maximum(cmax, 1e-6), 0.0)

        # Hue calculation (vectorised)
        h_arr = np.zeros_like(cmax)
        # cmax == r
        mask_r = (cmax == r_f) & (delta > 0)
        h_arr[mask_r] = ((g_f[mask_r] - b_f[mask_r]) / delta[mask_r]) % 6
        # cmax == g
        mask_g = (cmax == g_f) & (delta > 0) & ~mask_r
        h_arr[mask_g] = (b_f[mask_g] - r_f[mask_g]) / delta[mask_g] + 2
        # cmax == b
        mask_b = ~mask_r & ~mask_g & (delta > 0)
        h_arr[mask_b] = (r_f[mask_b] - g_f[mask_b]) / delta[mask_b] + 4
        # Normalize to 0-179
        h_cv = (h_arr / 6.0 * 179.0).astype(np.int16)
        s_cv = (s_arr * 255).astype(np.uint8)
        v_cv = (v_arr * 255).astype(np.uint8)

        # Threshold
        green_mask = (
            (h_cv >= h_min) & (h_cv <= h_max) &
            (s_cv >= s_min) & (s_cv <= s_max) &
            (v_cv >= v_min) & (v_cv <= v_max)
        ).astype(np.uint8) * 255

        # Morphological close (simple dilation then erosion)
        if k > 0:
            try:
                # scipy provides an optimised binary_closing; ImportError is caught
                # so the manual fallback below is used when scipy is absent.
                from scipy.ndimage import binary_closing
                closed = binary_closing(green_mask > 0, iterations=k).astype(np.uint8) * 255
                green_mask = closed
            except (ImportError, Exception):
                # Manual box-morphology fallback (no scipy required)
                for _ in range(k):
                    padded = np.pad(green_mask, 1, mode="constant")
                    dilated = np.zeros_like(green_mask)
                    for di in range(-1, 2):
                        for dj in range(-1, 2):
                            dilated = np.maximum(
                                dilated, padded[1+di:1+di+h, 1+dj:1+dj+w])
                    green_mask = dilated
                for _ in range(k):
                    padded = np.pad(green_mask, 1, mode="constant")
                    eroded = np.full_like(green_mask, 255)
                    for di in range(-1, 2):
                        for dj in range(-1, 2):
                            eroded = np.minimum(
                                eroded, padded[1+di:1+di+h, 1+dj:1+dj+w])
                    green_mask = eroded

        # Find connected blobs via labelling
        mask_img = Image.fromarray(green_mask, mode="L")
        # Find best blob using connected component approach
        labeled = _label_blobs(green_mask)
        if labeled is None or labeled.max() == 0:
            return {"center": None, "confidence": 0.0, "mask_img": mask_img}

        best_label, best_area = 0, 0
        for lbl in range(1, labeled.max() + 1):
            area = int((labeled == lbl).sum())
            if min_a <= area <= max_a and area > best_area:
                best_area = area
                best_label = lbl

        if best_label == 0:
            return {"center": None, "confidence": 0.0, "mask_img": mask_img}

        blob = (labeled == best_label)
        ys, xs = np.where(blob)
        cx = float(xs.mean())
        cy = float(ys.mean())
        conf = min(1.0, best_area / max(1.0, max_a * 0.5))
        return {"center": (cx, cy), "confidence": conf, "mask_img": mask_img}

    else:
        # Pure-Python fallback (slow)
        pixels = list(img.getdata())
        mask_data = []
        green_pixels = []
        for idx, (r_p, g_p, b_p) in enumerate(pixels):
            hh, ss, vv = _rgb_to_hsv_pixel(r_p, g_p, b_p)
            hit = (h_min <= hh <= h_max and
                   s_min <= ss <= s_max and
                   v_min <= vv <= v_max)
            mask_data.append(255 if hit else 0)
            if hit:
                green_pixels.append((idx % w, idx // w))

        mask_img = Image.new("L", (w, h))
        mask_img.putdata(mask_data)

        if len(green_pixels) < min_a:
            return {"center": None, "confidence": 0.0, "mask_img": mask_img}

        cx = sum(x for x, _ in green_pixels) / len(green_pixels)
        cy = sum(y for _, y in green_pixels) / len(green_pixels)
        area = len(green_pixels)
        conf = min(1.0, area / max(1.0, max_a * 0.5))
        return {"center": (cx, cy), "confidence": conf, "mask_img": mask_img}


def _label_blobs(binary_mask):
    """Simple connected-component labelling using scipy or fallback."""
    if not _HAVE_NUMPY:
        return None
    try:
        from scipy.ndimage import label
        labeled, _ = label(binary_mask > 0)
        return labeled
    except ImportError:
        pass
    # Minimal BFS fallback
    h, w = binary_mask.shape
    labeled = np.zeros_like(binary_mask, dtype=np.int32)
    current = 0
    for y in range(h):
        for x in range(w):
            if binary_mask[y, x] > 0 and labeled[y, x] == 0:
                current += 1
                queue = [(y, x)]
                labeled[y, x] = current
                while queue:
                    cy2, cx2 = queue.pop()
                    for dy, dx in [(-1,0),(1,0),(0,-1),(0,1)]:
                        ny, nx = cy2+dy, cx2+dx
                        if (0 <= ny < h and 0 <= nx < w and
                                binary_mask[ny, nx] > 0 and
                                labeled[ny, nx] == 0):
                            labeled[ny, nx] = current
                            queue.append((ny, nx))
    return labeled


# ---------------------------------------------------------------------------
# Cursor outline mask
# ---------------------------------------------------------------------------
def build_outline_mask(img, pivot, params=None):
    """Build a binary mask of dark (outline) pixels in a local window around pivot.

    Parameters
    ----------
    img    : PIL RGB Image (minimap crop).
    pivot  : (px, py) float pixel coords of the green dot centroid.
    params : dict with keys from DEFAULT_MINIMAP_SETTINGS["outline"].

    Returns
    -------
    dict with:
      "mask"     : 2-D list[list[int]] (or np.ndarray) of 0/255 values;
                   indexed [row][col] relative to origin.
      "mask_img" : PIL "L" image of the crop-size mask.
      "origin"   : (ox, oy) top-left corner of the local window in image coords.
      "size"     : (sw, sh) size of the local window.
    or None on failure.
    """
    if img is None or pivot is None:
        return None
    if params is None:
        params = DEFAULT_MINIMAP_SETTINGS["outline"]

    dark_thresh  = int(params.get("dark_thresh",  60))
    local_radius = int(params.get("local_radius", 30))
    k            = max(0, int(params.get("morph_kernel", 2)))

    iw, ih = img.size
    cx, cy = pivot
    ox = max(0, int(round(cx)) - local_radius)
    oy = max(0, int(round(cy)) - local_radius)
    ex = min(iw, int(round(cx)) + local_radius + 1)
    ey = min(ih, int(round(cy)) + local_radius + 1)
    sw = ex - ox
    sh = ey - oy
    if sw < 4 or sh < 4:
        return None

    crop = img.crop((ox, oy, ex, ey)).convert("L")

    if _HAVE_NUMPY:
        gray = np.array(crop, dtype=np.uint8)
        dark = (gray < dark_thresh).astype(np.uint8) * 255

        if k > 0:
            try:
                from scipy.ndimage import binary_closing
                dark = binary_closing(dark > 0, iterations=k).astype(np.uint8) * 255
            except (ImportError, Exception):
                for _ in range(k):
                    padded = np.pad(dark, 1, mode="constant")
                    dilated = np.zeros_like(dark)
                    for di in range(-1, 2):
                        for dj in range(-1, 2):
                            dilated = np.maximum(
                                dilated,
                                padded[1+di:1+di+sh, 1+dj:1+dj+sw])
                    dark = dilated
                for _ in range(k):
                    padded = np.pad(dark, 1, mode="constant")
                    eroded = np.full_like(dark, 255)
                    for di in range(-1, 2):
                        for dj in range(-1, 2):
                            eroded = np.minimum(
                                eroded,
                                padded[1+di:1+di+sh, 1+dj:1+dj+sw])
                    dark = eroded

        mask_img = Image.fromarray(dark, mode="L")
        return {
            "mask":     dark,
            "mask_img": mask_img,
            "origin":   (ox, oy),
            "size":     (sw, sh),
        }
    else:
        # Pure-Python fallback
        gray_data = list(crop.getdata())
        mask_data = [255 if v < dark_thresh else 0 for v in gray_data]
        mask_img = Image.new("L", (sw, sh))
        mask_img.putdata(mask_data)
        # Build 2D list
        mask_2d = []
        for row in range(sh):
            mask_2d.append(mask_data[row * sw:(row + 1) * sw])
        return {
            "mask":     mask_2d,
            "mask_img": mask_img,
            "origin":   (ox, oy),
            "size":     (sw, sh),
        }


# ---------------------------------------------------------------------------
# Circle-based direction detection
# ---------------------------------------------------------------------------
def _sample_mask_at(mask_info, pivot, radius, n_samples):
    """Sample the outline mask on a circle of given radius around pivot.

    Returns list of (angle_deg, hit_bool) where angle 0=north, 90=east.
    """
    ox, oy = mask_info["origin"]
    sw, sh = mask_info["size"]
    mask   = mask_info["mask"]
    px, py = pivot

    # Local coords of pivot inside the mask crop
    lpx = px - ox
    lpy = py - oy

    samples = []
    for i in range(n_samples):
        angle_deg = (360.0 / n_samples) * i
        # 0=north means: x += sin, y -= cos (image y downward)
        ang_rad = math.radians(angle_deg)
        lx = lpx + radius * math.sin(ang_rad)
        ly = lpy - radius * math.cos(ang_rad)
        xi = int(round(lx))
        yi = int(round(ly))
        if 0 <= xi < sw and 0 <= yi < sh:
            if _HAVE_NUMPY:
                hit = bool(mask[yi, xi] > 0)
            else:
                hit = bool(mask[yi][xi] > 0)
        else:
            hit = False
        samples.append((angle_deg, hit))
    return samples


def _cluster_hits(samples, gap_deg=20.0, min_hits=2):
    """Group consecutive hit angles into clusters.

    Parameters
    ----------
    samples  : list of (angle_deg, hit_bool)
    gap_deg  : angular gap that separates clusters
    min_hits : minimum hits to keep a cluster

    Returns
    -------
    list of dicts: {"angles": [...], "mid": float, "size": int}
    sorted by size descending.
    """
    n = len(samples)
    if n == 0:
        return []
    hits = [a for a, h in samples if h]
    if not hits:
        return []

    step = 360.0 / n
    # Sort hits
    hits_sorted = sorted(hits)
    clusters = []
    current = [hits_sorted[0]]
    for a in hits_sorted[1:]:
        if a - current[-1] <= gap_deg:
            current.append(a)
        else:
            clusters.append(current)
            current = [a]
    # Check wrap-around: last cluster with first cluster
    if clusters and (360.0 - hits_sorted[-1] + hits_sorted[0]) <= gap_deg:
        # Merge the last cluster (current) with the first cluster
        clusters[0] = current + clusters[0]
    else:
        clusters.append(current)

    result = []
    for cl in clusters:
        if len(cl) < min_hits:
            continue
        mid = _mean_angle(cl)
        result.append({"angles": cl, "mid": mid, "size": len(cl)})

    result.sort(key=lambda x: x["size"], reverse=True)
    return result


def _count_bright_along_ray(img, pivot, angle_deg, length, bright_thresh=140):
    """Count bright pixels along a ray from pivot.  Used for disambiguation."""
    iw, ih = img.size
    px, py = pivot
    ang_rad = math.radians(angle_deg)
    data = img.convert("L").load()
    count = 0
    for d in range(1, int(length) + 1):
        x = int(round(px + d * math.sin(ang_rad)))
        y = int(round(py - d * math.cos(ang_rad)))
        if 0 <= x < iw and 0 <= y < ih:
            if data[x, y] >= bright_thresh:
                count += 1
    return count


def find_direction_circles(img, pivot, params_outline=None, params_dir=None):
    """Estimate cursor heading using circle-based outline intersection.

    Draws two circles (R1 and R2) around the pivot, samples outline mask hits,
    clusters them into left/right edge groups, computes the bisector, and
    disambiguates forward vs backward with a brightness raycast.

    Parameters
    ----------
    img           : PIL RGB Image (minimap crop).
    pivot         : (px, py) float coords of the green dot centroid.
    params_outline: dict from DEFAULT_MINIMAP_SETTINGS["outline"].
    params_dir    : dict from DEFAULT_MINIMAP_SETTINGS["direction"].

    Returns
    -------
    dict with:
      "heading"    : float degrees (0=north, 90=east), or None
      "confidence" : 0-1 float
      "bisector"   : raw bisector angle (before disambiguation) or None
      "clusters"   : list of cluster dicts (see _cluster_hits)
      "r1_samples" : list of (angle, hit) for R1
      "r2_samples" : list of (angle, hit) for R2
    or None on failure.
    """
    if img is None or pivot is None:
        return None
    if params_outline is None:
        params_outline = DEFAULT_MINIMAP_SETTINGS["outline"]
    if params_dir is None:
        params_dir = DEFAULT_MINIMAP_SETTINGS["direction"]

    r1          = float(params_dir.get("r1", 10.0))
    r2          = float(params_dir.get("r2", 18.0))
    samples_n   = max(8, int(params_dir.get("samples", 90)))
    cluster_gap = float(params_dir.get("cluster_gap", 20.0))
    min_hits    = max(1, int(params_dir.get("min_hits", 2)))

    # Build outline mask
    mask_info = build_outline_mask(img, pivot, params_outline)
    if mask_info is None:
        return None

    local_radius = int(params_outline.get("local_radius", 30))
    r1 = min(r1, local_radius - 1)
    r2 = min(r2, local_radius - 1)
    if r1 < 1:
        r1 = 1.0
    if r2 < r1 + 1:
        r2 = r1 + 1

    # Sample both circles
    r1_samples = _sample_mask_at(mask_info, pivot, r1, samples_n)
    r2_samples = _sample_mask_at(mask_info, pivot, r2, samples_n)

    # Combine hits for clustering
    combined = r1_samples + r2_samples
    clusters = _cluster_hits(combined, gap_deg=cluster_gap, min_hits=min_hits)

    if len(clusters) < 2:
        # Fallback: try just one circle each separately
        clusters_r1 = _cluster_hits(r1_samples, gap_deg=cluster_gap, min_hits=min_hits)
        clusters_r2 = _cluster_hits(r2_samples, gap_deg=cluster_gap, min_hits=min_hits)
        clusters = (clusters_r1 + clusters_r2)
        clusters.sort(key=lambda x: x["size"], reverse=True)

    if len(clusters) < 2:
        return {
            "heading":    None,
            "confidence": 0.0,
            "bisector":   None,
            "clusters":   clusters,
            "r1_samples": r1_samples,
            "r2_samples": r2_samples,
        }

    # Take the two largest clusters as left/right edges
    cl_a = clusters[0]["mid"]
    cl_b = clusters[1]["mid"]

    # Bisector: mean angle of the two cluster midpoints
    bisector = _mean_angle([cl_a, cl_b])

    # Two candidates: bisector and bisector+180
    cand_a = bisector % 360.0
    cand_b = (bisector + 180.0) % 360.0

    # Disambiguate: count bright pixels along each candidate ray
    ray_len = float(params_dir.get("r2", 18.0)) * 1.5
    score_a = _count_bright_along_ray(img, pivot, cand_a, ray_len)
    score_b = _count_bright_along_ray(img, pivot, cand_b, ray_len)

    heading = cand_a if score_a >= score_b else cand_b

    # Confidence: based on number of clusters and their size balance
    total_hits = clusters[0]["size"] + clusters[1]["size"]
    max_possible = samples_n * 2
    conf = min(1.0, total_hits / max(1.0, max_possible * 0.3))
    # Bonus if score difference is large
    if score_a + score_b > 0:
        score_ratio = abs(score_a - score_b) / (score_a + score_b + 1e-6)
        conf = min(1.0, conf * (0.5 + 0.5 * score_ratio))

    return {
        "heading":    heading,
        "confidence": conf,
        "bisector":   bisector,
        "clusters":   clusters,
        "r1_samples": r1_samples,
        "r2_samples": r2_samples,
    }


# ---------------------------------------------------------------------------
# Cursor tip detection
# ---------------------------------------------------------------------------
def raycast_tip(img, pivot, heading_deg, mask_info=None,
                params_tip=None, params_outline=None):
    """Raycast from pivot along heading to find the cursor tip.

    Finds the farthest pixel that belongs to the cursor outline/body and
    computes a stable tracking point at a configurable distance from pivot.

    Parameters
    ----------
    img          : PIL RGB Image.
    pivot        : (px, py).
    heading_deg  : float direction (0=north, 90=east).
    mask_info    : pre-built outline mask dict from build_outline_mask().
                   If None, the function will build it internally.
    params_tip   : dict from DEFAULT_MINIMAP_SETTINGS["tip"].
    params_outline: dict from DEFAULT_MINIMAP_SETTINGS["outline"].

    Returns
    -------
    dict with:
      "tip"      : (tx, ty) or None
      "track_pt" : (trx, try) or None
      "dist"     : float distance to tip, or None
    """
    if img is None or pivot is None or heading_deg is None:
        return None
    if params_tip is None:
        params_tip = DEFAULT_MINIMAP_SETTINGS["tip"]
    if params_outline is None:
        params_outline = DEFAULT_MINIMAP_SETTINGS["outline"]

    raycast_max = int(params_tip.get("raycast_max", 35))
    track_dist  = float(params_tip.get("track_dist", 3.0))
    dark_thresh = int(params_outline.get("dark_thresh", 60))

    if mask_info is None:
        mask_info = build_outline_mask(img, pivot, params_outline)
    if mask_info is None:
        return None

    ox, oy = mask_info["origin"]
    sw, sh = mask_info["size"]
    mask   = mask_info["mask"]
    px, py = pivot
    lpx = px - ox
    lpy = py - oy

    ang_rad = math.radians(heading_deg)
    farthest = None
    farthest_d = 0.0

    iw, ih = img.size
    gray = img.convert("L").load()

    for d in range(2, raycast_max + 1):
        lx = lpx + d * math.sin(ang_rad)
        ly = lpy - d * math.cos(ang_rad)
        xi = int(round(lx))
        yi = int(round(ly))

        # Check in image coords
        img_x = xi + ox
        img_y = yi + oy
        if not (0 <= img_x < iw and 0 <= img_y < ih):
            break

        # A cursor pixel is either part of the dark outline or bright interior
        gray_val = gray[img_x, img_y]
        in_outline = False
        if 0 <= xi < sw and 0 <= yi < sh:
            if _HAVE_NUMPY:
                in_outline = bool(mask[yi, xi] > 0)
            else:
                in_outline = bool(mask[yi][xi] > 0)

        in_body = (gray_val >= 100)  # broad threshold for cursor interior

        if in_outline or in_body:
            farthest = (float(img_x), float(img_y))
            farthest_d = float(d)

    if farthest is None:
        return {"tip": None, "track_pt": None, "dist": None}

    # Stable tracking point
    track_pt = None
    if track_dist > 0:
        tx = px + track_dist * math.sin(ang_rad)
        ty = py - track_dist * math.cos(ang_rad)
        track_pt = (float(tx), float(ty))

    return {
        "tip":      farthest,
        "track_pt": track_pt,
        "dist":     farthest_d,
    }


# ---------------------------------------------------------------------------
# Temporal smoothing
# ---------------------------------------------------------------------------
def smooth_angle(new_angle, prev_angle, alpha=0.4, max_delta=60.0):
    """Exponential moving average for heading angle with wrap-around.

    Parameters
    ----------
    new_angle  : newest heading in degrees (or None).
    prev_angle : previous smoothed heading (or None).
    alpha      : EMA weight for new_angle (1.0 = no smoothing).
    max_delta  : maximum allowed change per frame in degrees.

    Returns
    -------
    Smoothed angle in degrees, or None.
    """
    if new_angle is None:
        return prev_angle
    if prev_angle is None:
        return float(new_angle)

    diff = _angle_diff(float(new_angle), float(prev_angle))
    diff = max(-max_delta, min(max_delta, diff))
    smoothed = (float(prev_angle) + alpha * diff) % 360.0
    return smoothed


def smooth_pos(new_pos, prev_pos, alpha=0.5):
    """EMA smoothing for 2-D position tuple.

    Parameters
    ----------
    new_pos  : (x, y) or None.
    prev_pos : previous (x, y) or None.
    alpha    : EMA weight for new_pos.

    Returns
    -------
    Smoothed (x, y) tuple or None.
    """
    if new_pos is None:
        return prev_pos
    if prev_pos is None:
        return (float(new_pos[0]), float(new_pos[1]))
    x = prev_pos[0] + alpha * (float(new_pos[0]) - prev_pos[0])
    y = prev_pos[1] + alpha * (float(new_pos[1]) - prev_pos[1])
    return (x, y)
