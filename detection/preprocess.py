"""detection/preprocess.py – Image preprocessing utilities.

All functions are pure (no side-effects, no global state) and accept a
PIL Image as their primary argument.  NumPy and OpenCV are used when
available; PIL-only fallbacks are provided for environments that have
only Pillow installed.
"""

from __future__ import annotations

from typing import Optional, Tuple

try:
    from PIL import Image, ImageFilter, ImageOps
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


# ---------------------------------------------------------------------------
# Individual preprocessing helpers
# ---------------------------------------------------------------------------

def to_gray(img: "Image.Image") -> "Image.Image":
    """Convert *img* to an ``'L'`` (greyscale) PIL Image.

    Parameters
    ----------
    img:
        Any PIL Image (RGB, RGBA, L, …).

    Returns
    -------
    PIL.Image.Image
        Greyscale (mode ``'L'``) copy of *img*.
    """
    return img.convert("L")


def apply_gamma(img: "Image.Image", gamma: float) -> "Image.Image":
    """Apply gamma correction to *img*.

    A gamma < 1 brightens the image; gamma > 1 darkens it.  Uses a
    NumPy-based lookup-table when NumPy is available, otherwise falls
    back to a pure-Python per-pixel transform via PIL ``point``.

    Parameters
    ----------
    img:
        Greyscale PIL Image (mode ``'L'``).
    gamma:
        Gamma exponent.  1.0 returns the image unchanged.

    Returns
    -------
    PIL.Image.Image
        Gamma-corrected image in mode ``'L'``.
    """
    if gamma == 1.0:
        return img.copy()

    gray = img.convert("L")

    if _HAVE_NUMPY:
        arr = np.asarray(gray, dtype=np.float32) / 255.0
        corrected = np.clip(np.power(arr, gamma) * 255.0, 0, 255).astype(np.uint8)
        return Image.fromarray(corrected, mode="L")

    # Pure-Python fallback via 256-entry LUT
    lut = [int(pow(i / 255.0, gamma) * 255.0 + 0.5) for i in range(256)]
    return gray.point(lut)


def apply_clahe(
    img: "Image.Image",
    clip_limit: float = 2.0,
    tile_grid: Tuple[int, int] = (8, 8),
) -> "Image.Image":
    """Apply CLAHE (Contrast Limited Adaptive Histogram Equalisation).

    Uses ``cv2.createCLAHE`` when OpenCV is available; otherwise falls
    back to ``PIL.ImageOps.autocontrast``.

    Parameters
    ----------
    img:
        Greyscale PIL Image.
    clip_limit:
        CLAHE clip limit (higher = more contrast; only used with cv2).
    tile_grid:
        ``(tile_w, tile_h)`` CLAHE tile size (only used with cv2).

    Returns
    -------
    PIL.Image.Image
        Contrast-enhanced greyscale image.
    """
    gray = img.convert("L")

    if _HAVE_CV2 and _HAVE_NUMPY:
        arr = np.asarray(gray, dtype=np.uint8)
        clahe_op = _cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=tile_grid)
        enhanced = clahe_op.apply(arr)
        return Image.fromarray(enhanced, mode="L")

    # PIL fallback
    return ImageOps.autocontrast(gray)


def apply_unsharp_mask(
    img: "Image.Image",
    radius: int = 2,
    percent: int = 150,
    threshold: int = 3,
) -> "Image.Image":
    """Sharpen *img* using an unsharp mask.

    Wraps ``PIL.ImageFilter.UnsharpMask``.

    Parameters
    ----------
    img:
        PIL Image (any mode; converted to ``'L'`` if needed).
    radius:
        Blur radius for the unsharp mask kernel.
    percent:
        Strength of the sharpening effect (100 = no change).
    threshold:
        Minimum brightness difference to apply sharpening.

    Returns
    -------
    PIL.Image.Image
        Sharpened image.
    """
    gray = img.convert("L")
    return gray.filter(ImageFilter.UnsharpMask(radius=radius, percent=percent, threshold=threshold))


def apply_autocontrast(img: "Image.Image") -> "Image.Image":
    """Stretch the pixel value range to [0, 255] using ``ImageOps.autocontrast``.

    Parameters
    ----------
    img:
        PIL Image.

    Returns
    -------
    PIL.Image.Image
        Autocontrasted image.
    """
    return ImageOps.autocontrast(img.convert("L"))


def sobel_edges(arr: "np.ndarray") -> "np.ndarray":
    """Compute Sobel edge magnitude from a 2-D float32 greyscale array.

    Uses a fast 3-tap finite-difference approximation.  Requires NumPy.
    Falls back to computing edges via PIL ``FIND_EDGES`` filter and
    returning a float32 array when NumPy is not available.

    Parameters
    ----------
    arr:
        2-D float32 NumPy array (H × W) with pixel values in [0, 255].

    Returns
    -------
    np.ndarray
        Float32 edge-magnitude array of the same shape as *arr*.
    """
    if _HAVE_NUMPY:
        f = arr.astype(np.float32)
        gx = f[1:-1, 2:] - f[1:-1, :-2]
        gy = f[2:, 1:-1] - f[:-2, 1:-1]
        mag = np.sqrt(gx * gx + gy * gy)
        out = np.zeros(arr.shape, dtype=np.float32)
        out[1:-1, 1:-1] = mag
        return out

    # NumPy not available: delegate to PIL and convert to a list-backed array
    pil_img = Image.fromarray(arr.astype("uint8") if hasattr(arr, "astype") else arr, mode="L")
    edges_pil = pil_img.filter(ImageFilter.FIND_EDGES)
    # Return as a plain 2-D list-of-lists so callers that check for ndarray
    # degrade gracefully; true ndarray returned only when numpy is present.
    w, h = edges_pil.size
    pixels = list(edges_pil.getdata())
    result = [[float(pixels[r * w + c]) for c in range(w)] for r in range(h)]
    return result  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Composite preprocessing pipeline
# ---------------------------------------------------------------------------

def preprocess_for_detection(
    img: "Image.Image",
    gamma: float = 1.0,
    clahe: bool = False,
    autocontrast: bool = False,
    unsharp: bool = False,
) -> dict:
    """Apply a configurable chain of preprocessing steps to *img*.

    Returns a dictionary with three keys:

    ``'gray'``
        Plain greyscale (mode ``'L'``) PIL Image – no enhancements.
    ``'enhanced'``
        Greyscale PIL Image after all requested enhancements (gamma,
        CLAHE / autocontrast, unsharp mask).  Equal to ``'gray'`` when no
        enhancements are requested.
    ``'edges'``
        Sobel edge-magnitude array (NumPy float32) computed from
        ``'enhanced'``, or a PIL ``FIND_EDGES`` image when NumPy is
        unavailable.

    Parameters
    ----------
    img:
        Source PIL Image (RGB, RGBA, L, …).
    gamma:
        Gamma exponent applied before contrast enhancement.  1.0 = off.
    clahe:
        When *True*, apply CLAHE (or autocontrast fallback) after gamma.
    autocontrast:
        When *True* (and *clahe* is *False*), apply plain autocontrast.
    unsharp:
        When *True*, apply an unsharp mask as the final enhancement step.

    Returns
    -------
    dict with keys ``'gray'``, ``'enhanced'``, ``'edges'``.
    """
    gray = to_gray(img)

    enhanced: "Image.Image" = gray

    if gamma != 1.0:
        enhanced = apply_gamma(enhanced, gamma)

    if clahe:
        enhanced = apply_clahe(enhanced)
    elif autocontrast:
        enhanced = apply_autocontrast(enhanced)

    if unsharp:
        enhanced = apply_unsharp_mask(enhanced)

    # Compute edge map
    if _HAVE_NUMPY:
        arr = np.asarray(enhanced, dtype=np.float32)
        edges = sobel_edges(arr)
    else:
        edges = enhanced.filter(ImageFilter.FIND_EDGES)

    return {
        "gray": gray,
        "enhanced": enhanced,
        "edges": edges,
    }
