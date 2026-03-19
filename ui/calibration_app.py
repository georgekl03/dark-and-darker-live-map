"""ui/calibration_app.py – Interactive calibration / debug app.

Standalone Tkinter application for interactively debugging the bbox and
microgrid detection pipeline.

Launch::

    python ui/calibration_app.py

Or import and instantiate::

    from ui.calibration_app import CalibrationApp
    app = CalibrationApp()
    app.mainloop()
"""
from __future__ import annotations

import json
import math
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Optional imports – handled gracefully
# ---------------------------------------------------------------------------

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk
    _HAVE_TK = True
    _TkFrameBase = tk.Frame
    _TkBase = tk.Tk
except ImportError:  # pragma: no cover
    _HAVE_TK = False
    _TkFrameBase = object  # type: ignore[assignment, misc]
    _TkBase = object  # type: ignore[assignment, misc]

try:
    from PIL import Image, ImageDraw, ImageTk
    _HAVE_PIL = True
except ImportError:  # pragma: no cover
    _HAVE_PIL = False

try:
    import numpy as np
    _HAVE_NUMPY = True
except ImportError:  # pragma: no cover
    np = None  # type: ignore[assignment]
    _HAVE_NUMPY = False

# Detection imports – resolve relative to repo root when run as a script
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from detection import (
        BboxConfig,
        BboxResult,
        MicrogridConfig,
        MicrogridResult,
        find_map_bbox,
        detect_microgrid,
        search_microgrid_first,
        preprocess_for_detection,
    )
    _HAVE_DETECTION = True
except ImportError as _det_err:  # pragma: no cover
    _HAVE_DETECTION = False
    _det_err_msg = str(_det_err)

# ---------------------------------------------------------------------------
# Colour palette (matches map_scanner_v2.py)
# ---------------------------------------------------------------------------

BG      = "#141414"
PANEL   = "#1c1c1c"
PANEL2  = "#242424"
BORDER  = "#303030"
BDR2    = "#424242"
ACCENT  = "#c8a84b"
TEXT    = "#e0ddd8"
DIM     = "#808080"
BTN_BG  = "#2a2a2a"
BTN_H   = "#3c3c3c"

# ---------------------------------------------------------------------------
# Default preset directory
# ---------------------------------------------------------------------------

PRESET_DIR = _REPO_ROOT / "data" / "debug" / "presets"

# ---------------------------------------------------------------------------
# Detection modes
# ---------------------------------------------------------------------------

MODES = [
    "Outer Square (Edge+Contour)",
    "Outer Square (Dark Box)",
    "Micro-Grid First",
    "Manual Crop",
]

# ---------------------------------------------------------------------------
# Stage definitions (id, display label)
# ---------------------------------------------------------------------------

STAGES = [
    ("original",     "Original"),
    ("preprocessed", "Preprocessed"),
    ("edges",        "Edge Map"),
    ("bbox",         "BBox Overlay"),
    ("crop",         "Map Crop"),
    ("microgrid",    "Micro-Grid"),
    ("grid",         "Module Grid"),
]

# ---------------------------------------------------------------------------
# Helper: flat button factory
# ---------------------------------------------------------------------------

def _flat_btn(
    parent: "tk.Widget",
    text: str,
    cmd: Any,
    fg: str = TEXT,
    bg: str = BTN_BG,
    font: tuple = ("Segoe UI", 9),
    padx: int = 10,
    pady: int = 4,
    width: Optional[int] = None,
) -> "tk.Button":
    kw: Dict[str, Any] = dict(
        text=text, command=cmd, bg=bg, fg=fg, bd=0, font=font,
        padx=padx, pady=pady, activebackground=BTN_H,
        activeforeground=TEXT, relief="flat", cursor="hand2",
    )
    if width is not None:
        kw["width"] = width
    return tk.Button(parent, **kw)


# ---------------------------------------------------------------------------
# Helper: collapsible section frame
# ---------------------------------------------------------------------------

class _CollapsibleSection(_TkFrameBase):  # type: ignore[misc]
    """A LabelFrame with a ▶/▼ toggle button that hides/shows its content."""

    def __init__(self, parent: "tk.Widget", title: str, **kw: Any) -> None:
        super().__init__(parent, bg=PANEL, **kw)
        self._expanded = True

        hdr = tk.Frame(self, bg=PANEL2)
        hdr.pack(fill="x", pady=(2, 0))

        self._toggle_btn = tk.Button(
            hdr, text="▼ " + title,
            bg=PANEL2, fg=ACCENT, bd=0, font=("Segoe UI", 8, "bold"),
            anchor="w", padx=6, pady=3, relief="flat", cursor="hand2",
            activebackground=BTN_H, activeforeground=ACCENT,
            command=self._toggle,
        )
        self._toggle_btn.pack(fill="x")

        self._title = title
        self.body = tk.Frame(self, bg=PANEL)
        self.body.pack(fill="x", padx=6, pady=4)

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        if self._expanded:
            self.body.pack(fill="x", padx=6, pady=4)
            self._toggle_btn.config(text="▼ " + self._title)
        else:
            self.body.forget()
            self._toggle_btn.config(text="▶ " + self._title)


# ---------------------------------------------------------------------------
# Helper: setting row (label + widget + help text)
# ---------------------------------------------------------------------------

def _setting_row(
    parent: "tk.Widget",
    label: str,
    widget_factory: Any,
    help_text: str,
) -> "tk.Widget":
    """Build a (label, widget, help) trio inside *parent* and return the widget."""
    row = tk.Frame(parent, bg=PANEL)
    row.pack(fill="x", pady=(3, 0))

    tk.Label(
        row, text=label, bg=PANEL, fg=TEXT,
        font=("Segoe UI", 8), anchor="w",
    ).pack(fill="x")

    w = widget_factory(row)
    w.pack(anchor="w", pady=(1, 0))

    tk.Label(
        row, text=help_text, bg=PANEL, fg=DIM,
        font=("Segoe UI", 7, "italic"), anchor="w", wraplength=280, justify="left",
    ).pack(fill="x", pady=(1, 4))

    return w


# ---------------------------------------------------------------------------
# Overlay drawing helpers
# ---------------------------------------------------------------------------

def _draw_bbox_overlay(
    img: "Image.Image",
    bbox: Tuple[int, int, int, int],
    color: Tuple[int, int, int] = (0, 255, 0),
    width: int = 3,
) -> "Image.Image":
    """Return a copy of *img* (RGB) with *bbox* rectangle drawn on it."""
    out = img.convert("RGB").copy()
    draw = ImageDraw.Draw(out)
    x, y, w, h = bbox
    for i in range(width):
        draw.rectangle([x - i, y - i, x + w + i, y + h + i], outline=color)
    del draw
    return out


def _draw_microgrid_overlay(
    img: "Image.Image",
    micro_result: "MicrogridResult",
    offset_x: int = 0,
    offset_y: int = 0,
) -> "Image.Image":
    """Return a copy of *img* with micro-grid lines drawn (cyan, thin)."""
    out = img.convert("RGB").copy()
    draw = ImageDraw.Draw(out)
    w, h = out.size
    sx = micro_result.micro_step_x or 1
    sy = micro_result.micro_step_y or 1
    ox = micro_result.offset_x
    oy = micro_result.offset_y

    # Vertical micro lines
    x = (ox - offset_x) % sx
    while x < w:
        draw.line([(x, 0), (x, h - 1)], fill=(0, 220, 220), width=1)
        x += sx

    # Horizontal micro lines
    y = (oy - offset_y) % sy
    while y < h:
        draw.line([(0, y), (w - 1, y)], fill=(0, 220, 220), width=1)
        y += sy

    del draw
    return out


def _draw_module_grid_overlay(
    img: "Image.Image",
    micro_result: "MicrogridResult",
    offset_x: int = 0,
    offset_y: int = 0,
) -> "Image.Image":
    """Return a copy of *img* with module grid lines drawn (yellow)."""
    out = img.convert("RGB").copy()
    draw = ImageDraw.Draw(out)
    iw, ih = out.size
    msx = micro_result.module_step_x or 1
    msy = micro_result.module_step_y or 1
    ox = micro_result.offset_x
    oy = micro_result.offset_y

    x = (ox - offset_x) % msx
    while x < iw:
        draw.line([(x, 0), (x, ih - 1)], fill=(220, 200, 0), width=2)
        x += msx

    y = (oy - offset_y) % msy
    while y < ih:
        draw.line([(0, y), (iw - 1, y)], fill=(220, 200, 0), width=2)
        y += msy

    del draw
    return out


def _edges_to_pil(edges: Any) -> "Image.Image":
    """Convert a Sobel edge array (numpy or list-of-lists) to an 'L' PIL Image."""
    if _HAVE_NUMPY and hasattr(edges, "astype"):
        arr = np.clip(edges, 0, 255).astype(np.uint8)
        return Image.fromarray(arr, mode="L")
    # PIL FIND_EDGES fallback – already a PIL image
    if hasattr(edges, "convert"):
        return edges.convert("L")
    # list-of-lists fallback
    h = len(edges)
    w = len(edges[0]) if h else 0
    flat = [int(min(255, max(0, edges[r][c]))) for r in range(h) for c in range(w)]
    img = Image.new("L", (w, h))
    img.putdata(flat)
    return img


# ===========================================================================
# Main application class
# ===========================================================================

class CalibrationApp(_TkBase):  # type: ignore[misc]
    """Interactive calibration / debug application for bbox and microgrid detection."""

    def __init__(self) -> None:
        if not _HAVE_TK:
            raise RuntimeError("tkinter is not available in this Python installation.")
        super().__init__()

        self.title("D&D Live Map – Calibration & Debug Tool")
        self.configure(bg=BG)
        self.geometry("1600x920")
        self.minsize(1000, 650)

        # State
        self._img_orig: Optional[Image.Image] = None
        self._stage_imgs: Dict[str, Image.Image] = {}
        self._tkimgs: List[Any] = []           # keep ImageTk refs alive
        self._thumb_tkimgs: List[Any] = []

        self._cv_scale: float = 1.0
        self._cv_off: Tuple[float, float] = (0.0, 0.0)
        self._cv_drag_start: Optional[Tuple[int, int]] = None
        self._cv_drag_off_start: Optional[Tuple[float, float]] = None

        self._draw_mode: bool = False
        self._draw_rect_start: Optional[Tuple[int, int]] = None
        self._draw_rect_id: Optional[int] = None
        self._manual_crop: Optional[Tuple[int, int, int, int]] = None

        self._current_stage: str = "original"
        self._running: bool = False

        # Tk vars for all settings
        self._sv: Dict[str, Any] = {}

        # Collapsible section for mgf (show/hide based on mode)
        self._mgf_section: Optional[_CollapsibleSection] = None

        self._build_ui()

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._build_toolbar()
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")
        self._build_main()

    def _build_toolbar(self) -> None:
        tb = tk.Frame(self, bg=PANEL2, pady=6)
        tb.pack(fill="x")

        _flat_btn(tb, "📂 Load Image", self._load_image).pack(side="left", padx=6)

        tk.Frame(tb, bg=BDR2, width=1).pack(side="left", fill="y", padx=4)

        _flat_btn(tb, "▶ Run Detection", self._run_detection,
                  fg="#111", bg=ACCENT).pack(side="left", padx=4)

        tk.Frame(tb, bg=BDR2, width=1).pack(side="left", fill="y", padx=4)

        _flat_btn(tb, "💾 Save Preset", self._save_preset,
                  fg=DIM).pack(side="left", padx=2)
        _flat_btn(tb, "📂 Load Preset", self._load_preset,
                  fg=DIM).pack(side="left", padx=2)

        tk.Frame(tb, bg=BDR2, width=1).pack(side="left", fill="y", padx=4)

        tk.Label(tb, text="Mode:", bg=PANEL2, fg=DIM,
                 font=("Segoe UI", 9)).pack(side="left", padx=(4, 2))
        self._mode_var = tk.StringVar(value=MODES[0])
        mode_cb = ttk.Combobox(
            tb, textvariable=self._mode_var,
            values=MODES, state="readonly",
            font=("Segoe UI", 9), width=28,
        )
        mode_cb.pack(side="left")
        mode_cb.bind("<<ComboboxSelected>>", self._on_mode_change)

        self._status_var = tk.StringVar(value="Load an image to begin.")
        tk.Label(
            tb, textvariable=self._status_var,
            bg=PANEL2, fg=DIM, font=("Segoe UI", 9),
            padx=12, anchor="w", wraplength=500,
        ).pack(side="left", fill="x", expand=True)

    def _build_main(self) -> None:
        paned = tk.PanedWindow(self, orient="horizontal", bg=BG,
                               sashwidth=5, sashrelief="flat")
        paned.pack(fill="both", expand=True)

        # ── Left sidebar ──────────────────────────────────
        left = tk.Frame(paned, bg=PANEL, width=320)
        left.pack_propagate(False)
        paned.add(left, minsize=240)
        self._build_left_sidebar(left)

        # ── Centre canvas ─────────────────────────────────
        centre = tk.Frame(paned, bg=BG)
        paned.add(centre, minsize=500)
        self._build_canvas(centre)

        # ── Right panel ───────────────────────────────────
        right = tk.Frame(paned, bg=PANEL, width=280)
        right.pack_propagate(False)
        paned.add(right, minsize=220)
        self._build_right_panel(right)

    # ── Left sidebar ──────────────────────────────────────

    def _build_left_sidebar(self, parent: "tk.Widget") -> None:
        tk.Label(parent, text="Detection Settings", bg=PANEL, fg=ACCENT,
                 font=("Segoe UI", 10, "bold"), padx=8, pady=6).pack(anchor="w")
        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x")

        # Scrollable container
        outer = tk.Frame(parent, bg=PANEL)
        outer.pack(fill="both", expand=True)

        cv = tk.Canvas(outer, bg=PANEL, bd=0, highlightthickness=0)
        sb = tk.Scrollbar(outer, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        cv.pack(side="left", fill="both", expand=True)

        inner = tk.Frame(cv, bg=PANEL)
        win_id = cv.create_window((0, 0), window=inner, anchor="nw")

        inner.bind("<Configure>", lambda _e: cv.configure(
            scrollregion=cv.bbox("all")))
        cv.bind("<Configure>", lambda e: cv.itemconfig(win_id, width=e.width))

        def _mw(e: Any) -> None:
            cv.yview_scroll(int(-1 * (e.delta / 120)), "units")

        cv.bind("<Enter>", lambda _: cv.bind_all("<MouseWheel>", _mw))
        cv.bind("<Leave>", lambda _: cv.unbind_all("<MouseWheel>"))

        self._build_settings_sections(inner)

    def _build_settings_sections(self, parent: "tk.Widget") -> None:
        sv = self._sv

        def _slider_spin(
            p: "tk.Widget",
            key: str,
            from_: float,
            to: float,
            resolution: float,
            default: float,
            is_int: bool = False,
        ) -> "tk.Frame":
            """Compound slider + spinbox."""
            v = tk.DoubleVar(value=default) if not is_int else tk.IntVar(value=int(default))
            sv[key] = v
            row = tk.Frame(p, bg=PANEL)
            inc = resolution

            if is_int:
                sl = tk.Scale(
                    row, variable=v, from_=from_, to=to, orient="horizontal",
                    resolution=1, bg=PANEL, fg=TEXT, troughcolor=PANEL2,
                    highlightthickness=0, bd=0, font=("Segoe UI", 7),
                    length=170, showvalue=False,
                )
                spin = ttk.Spinbox(row, from_=from_, to=to, increment=1,
                                   textvariable=v, width=5)
            else:
                sl = tk.Scale(
                    row, variable=v, from_=from_, to=to, orient="horizontal",
                    resolution=resolution, digits=5,
                    bg=PANEL, fg=TEXT, troughcolor=PANEL2,
                    highlightthickness=0, bd=0, font=("Segoe UI", 7),
                    length=170, showvalue=False,
                )
                spin = ttk.Spinbox(row, from_=from_, to=to, increment=inc,
                                   textvariable=v, width=7, format="%.3f")
            sl.pack(side="left")
            spin.pack(side="left", padx=(4, 0))
            return row

        def _spinbox(
            p: "tk.Widget",
            key: str,
            from_: int,
            to: int,
            default: int,
        ) -> "ttk.Spinbox":
            v = tk.IntVar(value=default)
            sv[key] = v
            return ttk.Spinbox(p, from_=from_, to=to, increment=1,
                               textvariable=v, width=6)

        def _checkbox(p: "tk.Widget", key: str, default: bool) -> "tk.Checkbutton":
            v = tk.BooleanVar(value=default)
            sv[key] = v
            return tk.Checkbutton(
                p, variable=v, bg=PANEL, activebackground=PANEL,
                fg=TEXT, selectcolor=PANEL2,
            )

        def _combobox(p: "tk.Widget", key: str, values: List[str], default: str) -> "ttk.Combobox":
            v = tk.StringVar(value=default)
            sv[key] = v
            return ttk.Combobox(p, textvariable=v, values=values,
                                state="readonly", width=16)

        # ── Section 1: BBox Outer Border ──────────────────
        s1 = _CollapsibleSection(parent, "📦 BBox – Outer Border Detection")
        s1.pack(fill="x", padx=4, pady=2)

        _setting_row(s1.body, "dark_thresh",
                     lambda p: _slider_spin(p, "dark_thresh", 20, 150, 1, 65, is_int=True),
                     "Max mean brightness to accept as map region. ↓ = stricter (rejects bright maps)."
                     " ↑ = lenient (false detections). Typical: 50–80."
                     " Raise if 'FAILED: all candidates above threshold'.")
        _setting_row(s1.body, "bbox_method",
                     lambda p: _combobox(p, "bbox_method",
                                         ["mean", "median", "trimmed_mean", "edge"], "mean"),
                     "'edge' uses edge density (better for varying lighting). 'mean' is fastest."
                     " Switch to 'edge' if brightness-based method gives wrong region.")
        _setting_row(s1.body, "search_margin",
                     lambda p: _slider_spin(p, "search_margin", 0.0, 0.25, 0.005, 0.05),
                     "Fraction of screen to ignore at each edge. ↑ if map never reaches screen edge."
                     " ↓ if map is near edge. Typical: 0.02–0.10.")
        _setting_row(s1.body, "min_frac",
                     lambda p: _slider_spin(p, "min_frac", 0.10, 0.95, 0.01, 0.20),
                     "Min map size to try as fraction of inner region."
                     " ↑ if small false detections occur. Typical: 0.15–0.30.")
        _setting_row(s1.body, "max_frac",
                     lambda p: _slider_spin(p, "max_frac", 0.10, 0.95, 0.01, 0.90),
                     "Max map size to try. ↓ if map is smaller than expected."
                     " Typical: 0.70–0.95.")
        _setting_row(s1.body, "prefer_darkest",
                     lambda p: _checkbox(p, "prefer_darkest", False),
                     "Always pick darkest patch regardless of threshold."
                     " Useful when the map is always the darkest region.")
        _setting_row(s1.body, "use_edge_contour",
                     lambda p: _checkbox(p, "use_edge_contour", True),
                     "Use edge+contour method to find bright outer border."
                     " More robust than dark-box alone. Recommended ON.")
        _setting_row(s1.body, "canny_low",
                     lambda p: _slider_spin(p, "canny_low", 10, 200, 1, 30, is_int=True),
                     "Canny lower threshold. ↓ = more edges detected."
                     " Lower both if no border found. Raise both if too many false edges.")
        _setting_row(s1.body, "canny_high",
                     lambda p: _slider_spin(p, "canny_high", 10, 200, 1, 100, is_int=True),
                     "Canny upper threshold. Should be ~3× canny_low."
                     " Raise if background noise creates too many contours.")
        _setting_row(s1.body, "min_border_brightness",
                     lambda p: _slider_spin(p, "min_border_brightness", 30, 180, 1, 80, is_int=True),
                     "Min brightness for outer border region to qualify."
                     " ↑ if detecting dark UI elements as map border. Typical: 60–120.")
        _setting_row(s1.body, "contour_min_area_frac",
                     lambda p: _slider_spin(p, "contour_min_area_frac", 0.01, 0.95, 0.005, 0.05),
                     "Min contour area as fraction of image."
                     " ↑ to reject small spurious contours. Typical: 0.03–0.15.")
        _setting_row(s1.body, "contour_max_area_frac",
                     lambda p: _slider_spin(p, "contour_max_area_frac", 0.01, 0.95, 0.005, 0.85),
                     "Max contour area as fraction of image."
                     " ↓ if the entire image is matched as a contour. Typical: 0.70–0.90.")
        _setting_row(s1.body, "contour_min_solidity",
                     lambda p: _slider_spin(p, "contour_min_solidity", 0.3, 1.0, 0.01, 0.70),
                     "How 'filled' the contour must be (area / convex-hull ratio)."
                     " ↓ to allow more irregular shapes. ↑ to reject non-rectangular contours.")

        # ── Section 2: BBox Refinement ────────────────────
        s2 = _CollapsibleSection(parent, "🔧 BBox – Refinement")
        s2.pack(fill="x", padx=4, pady=2)

        _setting_row(s2.body, "bbox_refine",
                     lambda p: _checkbox(p, "bbox_refine", True),
                     "Edge-snap the seed bbox outward to nearest strong edges."
                     " Recommended ON. Turn OFF to debug the raw seed bbox.")
        _setting_row(s2.body, "bbox_refine_band_pct",
                     lambda p: _slider_spin(p, "bbox_refine_band_pct", 0.02, 0.49, 0.005, 0.12),
                     "Width of band near each edge to sample for refinement."
                     " ↑ = more aggressive snap (may over-expand). Typical: 0.08–0.20.")
        _setting_row(s2.body, "bbox_refine_max_expand_pct",
                     lambda p: _slider_spin(p, "bbox_refine_max_expand_pct", 0.0, 0.40, 0.005, 0.10),
                     "Max expansion per side as fraction of bbox size."
                     " ↑ allows larger snaps. ↓ prevents expanding into wrong region. Typical: 0.05–0.15.")

        # ── Section 3: Micro-Grid Detection ───────────────
        s3 = _CollapsibleSection(parent, "🔬 Micro-Grid Detection")
        s3.pack(fill="x", padx=4, pady=2)

        _setting_row(s3.body, "min_micro",
                     lambda p: _spinbox(p, "min_micro", 1, 60, 2),
                     "Min search value for micro-cell period (px)."
                     " Decrease if map is very zoomed in. Typical: 2–5.")
        _setting_row(s3.body, "max_micro",
                     lambda p: _spinbox(p, "max_micro", 1, 60, 30),
                     "Max search value for micro-cell period (px)."
                     " ↑ for zoomed-out maps where cells are large. Typical: 15–40.")
        _setting_row(s3.body, "min_module",
                     lambda p: _spinbox(p, "min_module", 5, 600, 15),
                     "Min search value for module period (px)."
                     " Each module = 10 micro-cells. ↓ for very small maps. Typical: 15–50.")
        _setting_row(s3.body, "max_module",
                     lambda p: _spinbox(p, "max_module", 5, 600, 500),
                     "Max search value for module period (px). ↑ for large/zoomed-in maps. Typical: 200–600.")
        _setting_row(s3.body, "micro_cells",
                     lambda p: _spinbox(p, "micro_cells", 1, 20, 10),
                     "Number of micro-cells per module (always 10 in D&D)."
                     " Only change when testing other games.")
        _setting_row(s3.body, "force_micro_period",
                     lambda p: _spinbox(p, "force_micro_period", 0, 60, 0),
                     "Force a specific micro-cell period. 0 = auto-detect via FFT."
                     " Set manually if FFT returns wrong period.")
        _setting_row(s3.body, "force_module_period",
                     lambda p: _spinbox(p, "force_module_period", 0, 500, 0),
                     "Force a specific module period. 0 = auto-detect."
                     " Set when FFT is misled by other periodic structures.")
        _setting_row(s3.body, "min_grid_size",
                     lambda p: _spinbox(p, "min_grid_size", 1, 15, 2),
                     "Min allowed module count per axis."
                     " ↑ if single-module detections are incorrect. Typical: 2–4.")
        _setting_row(s3.body, "max_grid_size",
                     lambda p: _spinbox(p, "max_grid_size", 1, 15, 10),
                     "Max allowed module count per axis."
                     " ↓ to constrain to known map sizes (e.g. 5 for 5×5). Typical: 5–10.")

        # ── Section 4: Micro-Grid-First Search ────────────
        mgf_sec = _CollapsibleSection(parent, "🔍 Micro-Grid-First Search")
        mgf_sec.pack(fill="x", padx=4, pady=2)
        self._mgf_section = mgf_sec

        _setting_row(mgf_sec.body, "use_microgrid_first",
                     lambda p: _checkbox(p, "use_microgrid_first", False),
                     "Locate map by finding strongest micro-grid signal in a coarse window scan."
                     " Bypasses bbox detection entirely.")
        _setting_row(mgf_sec.body, "mgf_window_frac",
                     lambda p: _slider_spin(p, "mgf_window_frac", 0.2, 0.9, 0.01, 0.5),
                     "Window size as fraction of shorter image dimension."
                     " ↑ = more context. ↓ = more focused scan. Typical: 0.4–0.6.")
        _setting_row(mgf_sec.body, "mgf_search_steps",
                     lambda p: _spinbox(p, "mgf_search_steps", 3, 15, 7),
                     "Number of scan positions per axis."
                     " More steps = finer but slower. Typical: 5–9.")

        # ── Section 5: Preprocessing ──────────────────────
        s5 = _CollapsibleSection(parent, "🖼 Preprocessing")
        s5.pack(fill="x", padx=4, pady=2)

        _setting_row(s5.body, "gamma",
                     lambda p: _slider_spin(p, "gamma", 0.3, 3.0, 0.05, 1.0),
                     "Gamma correction. <1 brightens (find faint grids). >1 darkens."
                     " Start at 1.0. ↓ if grid lines too dark to detect.")
        _setting_row(s5.body, "clahe",
                     lambda p: _checkbox(p, "clahe", False),
                     "CLAHE contrast enhancement. Dramatically improves visibility of faint grid lines."
                     " Try ON if grid is not detected. Do not combine with autocontrast.")
        _setting_row(s5.body, "autocontrast",
                     lambda p: _checkbox(p, "autocontrast", False),
                     "Simple autocontrast stretch. Less aggressive than CLAHE."
                     " Use one or the other, not both.")
        _setting_row(s5.body, "unsharp",
                     lambda p: _checkbox(p, "unsharp", False),
                     "Unsharp mask sharpening. Enhances grid edges."
                     " Useful when grid lines are blurry or low-contrast.")

        # ── Section 6: Manual Crop ────────────────────────
        s6 = _CollapsibleSection(parent, "📐 Manual Crop")
        s6.pack(fill="x", padx=4, pady=2)

        _flat_btn(s6.body, "✏ Draw Crop Rectangle", self._enable_draw_mode,
                  bg=BTN_BG, padx=8).pack(anchor="w", pady=(2, 2))

        self._crop_status_var = tk.StringVar(value="No crop set")
        tk.Label(s6.body, textvariable=self._crop_status_var,
                 bg=PANEL, fg=DIM, font=("Segoe UI", 8), anchor="w").pack(anchor="w")

        _flat_btn(s6.body, "✕ Clear Crop", self._clear_crop,
                  bg=BTN_BG, fg=DIM, padx=8).pack(anchor="w", pady=(2, 2))

        tk.Label(
            s6.body,
            text="Click and drag on the image to define the map region manually,"
                 " then run detection on this crop.",
            bg=PANEL, fg=DIM, font=("Segoe UI", 7, "italic"),
            wraplength=280, justify="left", anchor="w",
        ).pack(fill="x")

    # ── Centre canvas ─────────────────────────────────────

    def _build_canvas(self, parent: "tk.Widget") -> None:
        # Stage selector strip at top
        strip = tk.Frame(parent, bg=PANEL2, pady=3)
        strip.pack(fill="x")

        tk.Label(strip, text="View:", bg=PANEL2, fg=DIM,
                 font=("Segoe UI", 8)).pack(side="left", padx=(8, 2))

        self._stage_var = tk.StringVar(value="original")
        for sid, slabel in STAGES:
            rb = tk.Radiobutton(
                strip, text=slabel, variable=self._stage_var, value=sid,
                bg=PANEL2, fg=DIM, selectcolor=PANEL2, activebackground=PANEL2,
                activeforeground=ACCENT, font=("Segoe UI", 8),
                indicatoron=False, bd=0, relief="flat", padx=6, pady=2,
                cursor="hand2",
                command=self._show_stage,
            )
            rb.pack(side="left", padx=1)

        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x")

        self._cv = tk.Canvas(parent, bg=BG, bd=0, highlightthickness=0,
                             cursor="crosshair")
        self._cv.pack(fill="both", expand=True)

        # Pixel inspector bar at bottom
        self._px_var = tk.StringVar(value="")
        tk.Label(parent, textvariable=self._px_var,
                 bg=PANEL2, fg=DIM, font=("Consolas", 8),
                 anchor="w", padx=8).pack(fill="x")

        # Bind canvas events
        self._cv.bind("<Configure>",        lambda _: self._show_stage())
        self._cv.bind("<ButtonPress-1>",    self._on_btn1_press)
        self._cv.bind("<B1-Motion>",        self._on_btn1_motion)
        self._cv.bind("<ButtonRelease-1>",  self._on_btn1_release)
        self._cv.bind("<ButtonPress-2>",    self._on_mid_press)
        self._cv.bind("<B2-Motion>",        self._on_mid_motion)
        self._cv.bind("<ButtonPress-3>",    self._on_mid_press)   # right = pan on some OS
        self._cv.bind("<B3-Motion>",        self._on_mid_motion)
        self._cv.bind("<MouseWheel>",       self._on_mousewheel)
        self._cv.bind("<Button-4>",         self._on_mousewheel)  # Linux scroll up
        self._cv.bind("<Button-5>",         self._on_mousewheel)  # Linux scroll down
        self._cv.bind("<Motion>",           self._on_cv_motion)

    # ── Right panel ───────────────────────────────────────

    def _build_right_panel(self, parent: "tk.Widget") -> None:
        # Splitter: thumbnails (top) | log (bottom)
        paned = tk.PanedWindow(parent, orient="vertical", bg=PANEL,
                               sashwidth=5, sashrelief="flat")
        paned.pack(fill="both", expand=True)

        # ── Thumbnails ────────────────────────────────────
        thumb_frame = tk.Frame(paned, bg=PANEL)
        paned.add(thumb_frame, minsize=150)

        tk.Label(thumb_frame, text="Stages", bg=PANEL, fg=ACCENT,
                 font=("Segoe UI", 9, "bold"), padx=8, pady=4).pack(anchor="w")
        tk.Frame(thumb_frame, bg=BORDER, height=1).pack(fill="x")

        thumb_outer = tk.Frame(thumb_frame, bg=PANEL)
        thumb_outer.pack(fill="both", expand=True)

        thumb_cv = tk.Canvas(thumb_outer, bg=PANEL, bd=0, highlightthickness=0)
        thumb_sb = tk.Scrollbar(thumb_outer, orient="vertical",
                                command=thumb_cv.yview)
        thumb_cv.configure(yscrollcommand=thumb_sb.set)
        thumb_sb.pack(side="right", fill="y")
        thumb_cv.pack(side="left", fill="both", expand=True)

        self._thumb_inner = tk.Frame(thumb_cv, bg=PANEL)
        thumb_win = thumb_cv.create_window((0, 0), window=self._thumb_inner, anchor="nw")

        self._thumb_inner.bind("<Configure>", lambda _e: thumb_cv.configure(
            scrollregion=thumb_cv.bbox("all")))
        thumb_cv.bind("<Configure>", lambda e: thumb_cv.itemconfig(thumb_win, width=e.width))

        def _tmw(e: Any) -> None:
            thumb_cv.yview_scroll(int(-1 * (e.delta / 120)), "units")

        thumb_cv.bind("<Enter>", lambda _: thumb_cv.bind_all("<MouseWheel>", _tmw))
        thumb_cv.bind("<Leave>", lambda _: thumb_cv.unbind_all("<MouseWheel>"))

        self._thumb_cv = thumb_cv

        # ── Debug log ─────────────────────────────────────
        log_frame = tk.Frame(paned, bg=PANEL)
        paned.add(log_frame, minsize=150)

        log_header = tk.Frame(log_frame, bg=PANEL2)
        log_header.pack(fill="x")
        tk.Label(log_header, text="Debug Log", bg=PANEL2, fg=ACCENT,
                 font=("Segoe UI", 9, "bold"), padx=8, pady=3).pack(side="left")
        _flat_btn(log_header, "Clear", self._clear_log,
                  fg=DIM, bg=PANEL2, padx=6, pady=2,
                  font=("Segoe UI", 8)).pack(side="right", padx=4)

        tk.Frame(log_frame, bg=BORDER, height=1).pack(fill="x")

        log_inner = tk.Frame(log_frame, bg=PANEL)
        log_inner.pack(fill="both", expand=True)

        self._log = tk.Text(
            log_inner, bg=PANEL2, fg=TEXT, bd=0,
            font=("Consolas", 7), wrap="word",
            state="disabled", highlightthickness=0,
        )
        log_sb = tk.Scrollbar(log_inner, command=self._log.yview)
        self._log["yscrollcommand"] = log_sb.set
        log_sb.pack(side="right", fill="y")
        self._log.pack(fill="both", expand=True, padx=2, pady=2)

    # -----------------------------------------------------------------------
    # Canvas zoom / pan helpers
    # -----------------------------------------------------------------------

    def _img_to_canvas(self, ix: float, iy: float) -> Tuple[float, float]:
        """Convert image coordinates to canvas coordinates."""
        cx = ix * self._cv_scale + self._cv_off[0]
        cy = iy * self._cv_scale + self._cv_off[1]
        return cx, cy

    def _canvas_to_img(self, cx: float, cy: float) -> Tuple[float, float]:
        """Convert canvas coordinates to image coordinates."""
        ix = (cx - self._cv_off[0]) / max(self._cv_scale, 1e-6)
        iy = (cy - self._cv_off[1]) / max(self._cv_scale, 1e-6)
        return ix, iy

    def _fit_to_canvas(self, img: "Image.Image") -> None:
        """Reset zoom/pan so *img* fits the canvas."""
        cw = self._cv.winfo_width() or 800
        ch = self._cv.winfo_height() or 600
        iw, ih = img.size
        scale = min(cw / max(iw, 1), ch / max(ih, 1), 1.0)
        self._cv_scale = scale
        self._cv_off = (
            (cw - iw * scale) / 2,
            (ch - ih * scale) / 2,
        )

    # ── Mouse event handlers ──────────────────────────────

    def _on_btn1_press(self, event: Any) -> None:
        if self._draw_mode:
            self._draw_rect_start = (event.x, event.y)
            if self._draw_rect_id is not None:
                self._cv.delete(self._draw_rect_id)
                self._draw_rect_id = None
        else:
            # Pan
            self._cv_drag_start = (event.x, event.y)
            self._cv_drag_off_start = self._cv_off

    def _on_btn1_motion(self, event: Any) -> None:
        if self._draw_mode and self._draw_rect_start is not None:
            x0, y0 = self._draw_rect_start
            if self._draw_rect_id is not None:
                self._cv.delete(self._draw_rect_id)
            self._draw_rect_id = self._cv.create_rectangle(
                x0, y0, event.x, event.y,
                outline="#00ff88", width=2, dash=(4, 2),
            )
        elif self._cv_drag_start is not None and self._cv_drag_off_start is not None:
            dx = event.x - self._cv_drag_start[0]
            dy = event.y - self._cv_drag_start[1]
            self._cv_off = (
                self._cv_drag_off_start[0] + dx,
                self._cv_drag_off_start[1] + dy,
            )
            self._render_canvas()

    def _on_btn1_release(self, event: Any) -> None:
        if self._draw_mode and self._draw_rect_start is not None:
            x0c, y0c = self._draw_rect_start
            x1c, y1c = event.x, event.y
            # Convert canvas → image coords
            ix0, iy0 = self._canvas_to_img(min(x0c, x1c), min(y0c, y1c))
            ix1, iy1 = self._canvas_to_img(max(x0c, x1c), max(y0c, y1c))
            w = int(ix1 - ix0)
            h = int(iy1 - iy0)
            if w > 10 and h > 10:
                self._manual_crop = (int(ix0), int(iy0), w, h)
                self._crop_status_var.set(
                    f"Crop: x={int(ix0)}, y={int(iy0)}, w={w}, h={h}"
                )
            self._draw_mode = False
            self._cv.configure(cursor="crosshair")
            if self._draw_rect_id is not None:
                self._cv.delete(self._draw_rect_id)
                self._draw_rect_id = None
            self._draw_rect_start = None
        self._cv_drag_start = None
        self._cv_drag_off_start = None

    def _on_mid_press(self, event: Any) -> None:
        self._cv_drag_start = (event.x, event.y)
        self._cv_drag_off_start = self._cv_off

    def _on_mid_motion(self, event: Any) -> None:
        if self._cv_drag_start is not None and self._cv_drag_off_start is not None:
            dx = event.x - self._cv_drag_start[0]
            dy = event.y - self._cv_drag_start[1]
            self._cv_off = (
                self._cv_drag_off_start[0] + dx,
                self._cv_drag_off_start[1] + dy,
            )
            self._render_canvas()

    def _on_mousewheel(self, event: Any) -> None:
        if event.num == 4:
            delta = 120
        elif event.num == 5:
            delta = -120
        else:
            delta = event.delta

        factor = 1.1 if delta > 0 else (1 / 1.1)
        cx, cy = event.x, event.y
        # Zoom centred on cursor
        new_scale = max(0.05, min(32.0, self._cv_scale * factor))
        ratio = new_scale / self._cv_scale
        self._cv_off = (
            cx - ratio * (cx - self._cv_off[0]),
            cy - ratio * (cy - self._cv_off[1]),
        )
        self._cv_scale = new_scale
        self._render_canvas()

    def _on_cv_motion(self, event: Any) -> None:
        """Show pixel info in the status bar below the canvas."""
        img = self._current_pil_img()
        if img is None:
            return
        ix, iy = self._canvas_to_img(event.x, event.y)
        iw, ih = img.size
        if 0 <= ix < iw and 0 <= iy < ih:
            try:
                px = img.getpixel((int(ix), int(iy)))
            except Exception:
                px = "?"
            self._px_var.set(f"  x={int(ix)}, y={int(iy)}  |  {px}")
        else:
            self._px_var.set("")

    # -----------------------------------------------------------------------
    # Stage / canvas rendering
    # -----------------------------------------------------------------------

    def _current_pil_img(self) -> Optional["Image.Image"]:
        stage = self._stage_var.get()
        return self._stage_imgs.get(stage)

    def _show_stage(self) -> None:
        self._render_canvas()

    def _render_canvas(self) -> None:
        img = self._current_pil_img()
        if img is None:
            self._cv.delete("all")
            cw = self._cv.winfo_width() or 400
            ch = self._cv.winfo_height() or 300
            self._cv.create_text(cw // 2, ch // 2,
                                 text="No image loaded", fill=DIM,
                                 font=("Segoe UI", 12))
            return

        iw, ih = img.size
        dw = max(1, int(iw * self._cv_scale))
        dh = max(1, int(ih * self._cv_scale))

        try:
            disp = img.resize((dw, dh), Image.NEAREST if self._cv_scale >= 8 else Image.LANCZOS)
        except Exception:
            disp = img.resize((dw, dh))

        tkimg = ImageTk.PhotoImage(disp)
        self._tkimgs = [tkimg]  # Replace ref (single image shown at a time)

        self._cv.delete("all")
        ox, oy = int(self._cv_off[0]), int(self._cv_off[1])
        self._cv.create_image(ox, oy, anchor="nw", image=tkimg)

        # Draw manual crop rect if set and viewing original
        if self._manual_crop is not None and self._stage_var.get() in ("original", "bbox"):
            x, y, w, h = self._manual_crop
            cx0, cy0 = self._img_to_canvas(x, y)
            cx1, cy1 = self._img_to_canvas(x + w, y + h)
            self._cv.create_rectangle(cx0, cy0, cx1, cy1,
                                      outline="#00ff88", width=2, dash=(4, 2))

    def _set_stage_img(self, stage_id: str, img: "Image.Image") -> None:
        self._stage_imgs[stage_id] = img.convert("RGB")

    def _update_thumbnails(self) -> None:
        """Rebuild the thumbnails panel from current stage images."""
        for w in self._thumb_inner.winfo_children():
            w.destroy()
        self._thumb_tkimgs = []

        TW, TH = 120, 90

        for sid, slabel in STAGES:
            img = self._stage_imgs.get(sid)
            if img is None:
                continue

            card = tk.Frame(self._thumb_inner, bg=PANEL2,
                            highlightbackground=BORDER, highlightthickness=1)
            card.pack(fill="x", padx=4, pady=3)

            try:
                thumb = img.copy()
                thumb.thumbnail((TW, TH), Image.LANCZOS)
                tkthumb = ImageTk.PhotoImage(thumb)
            except Exception:
                continue

            self._thumb_tkimgs.append(tkthumb)

            def _select(s=sid) -> None:
                self._stage_var.set(s)
                self._show_stage()

            lbl_img = tk.Label(card, image=tkthumb, bg=PANEL2, cursor="hand2")
            lbl_img.pack()
            lbl_img.bind("<Button-1>", lambda _e, s=sid: _select(s))

            lbl_txt = tk.Label(card, text=slabel, bg=PANEL2, fg=TEXT,
                               font=("Segoe UI", 7), cursor="hand2")
            lbl_txt.pack()
            lbl_txt.bind("<Button-1>", lambda _e, s=sid: _select(s))

        self._thumb_cv.update_idletasks()
        self._thumb_cv.configure(scrollregion=self._thumb_cv.bbox("all"))

    # -----------------------------------------------------------------------
    # Log helpers
    # -----------------------------------------------------------------------

    def _log_line(self, text: str) -> None:
        self._log.configure(state="normal")
        ts = time.strftime("%H:%M:%S")
        self._log.insert("end", f"[{ts}] {text}\n")
        self._log.configure(state="disabled")
        self._log.see("end")

    def _clear_log(self) -> None:
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")

    # -----------------------------------------------------------------------
    # Settings → config builders
    # -----------------------------------------------------------------------

    def _get_float(self, key: str, default: float = 0.0) -> float:
        v = self._sv.get(key)
        if v is None:
            return default
        try:
            return float(v.get())
        except (ValueError, tk.TclError):
            return default

    def _get_int(self, key: str, default: int = 0) -> int:
        v = self._sv.get(key)
        if v is None:
            return default
        try:
            return int(float(v.get()))
        except (ValueError, tk.TclError):
            return default

    def _get_bool(self, key: str, default: bool = False) -> bool:
        v = self._sv.get(key)
        if v is None:
            return default
        try:
            return bool(v.get())
        except (ValueError, tk.TclError):
            return default

    def _get_str(self, key: str, default: str = "") -> str:
        v = self._sv.get(key)
        if v is None:
            return default
        try:
            return str(v.get())
        except (ValueError, tk.TclError):
            return default

    def _build_bbox_config(self) -> "BboxConfig":
        mode = self._mode_var.get()
        use_ec = (mode == "Outer Square (Edge+Contour)") or self._get_bool("use_edge_contour", True)
        if mode == "Outer Square (Dark Box)":
            use_ec = False
        elif mode == "Outer Square (Edge+Contour)":
            use_ec = True

        return BboxConfig(
            dark_thresh=self._get_int("dark_thresh", 65),
            bbox_method=self._get_str("bbox_method", "mean"),
            search_margin=self._get_float("search_margin", 0.05),
            min_frac=self._get_float("min_frac", 0.20),
            max_frac=self._get_float("max_frac", 0.90),
            prefer_darkest=self._get_bool("prefer_darkest", False),
            use_edge_contour=use_ec,
            canny_low=self._get_int("canny_low", 30),
            canny_high=self._get_int("canny_high", 100),
            min_border_brightness=self._get_int("min_border_brightness", 80),
            contour_min_area_frac=self._get_float("contour_min_area_frac", 0.05),
            contour_max_area_frac=self._get_float("contour_max_area_frac", 0.85),
            contour_min_solidity=self._get_float("contour_min_solidity", 0.70),
            bbox_refine=self._get_bool("bbox_refine", True),
            bbox_refine_band_pct=self._get_float("bbox_refine_band_pct", 0.12),
            bbox_refine_max_expand_pct=self._get_float("bbox_refine_max_expand_pct", 0.10),
        )

    def _build_micro_config(self) -> "MicrogridConfig":
        return MicrogridConfig(
            min_micro=self._get_int("min_micro", 2),
            max_micro=self._get_int("max_micro", 30),
            min_module=self._get_int("min_module", 15),
            max_module=self._get_int("max_module", 500),
            micro_cells=self._get_int("micro_cells", 10),
            force_micro_period=self._get_int("force_micro_period", 0),
            force_module_period=self._get_int("force_module_period", 0),
            min_grid_size=self._get_int("min_grid_size", 2),
            max_grid_size=self._get_int("max_grid_size", 10),
            use_microgrid_first=(self._mode_var.get() == "Micro-Grid First"),
            mgf_window_frac=self._get_float("mgf_window_frac", 0.5),
            mgf_search_steps=self._get_int("mgf_search_steps", 7),
        )

    def _build_preprocess_params(self) -> dict:
        return {
            "gamma": self._get_float("gamma", 1.0),
            "clahe": self._get_bool("clahe", False),
            "autocontrast": self._get_bool("autocontrast", False),
            "unsharp": self._get_bool("unsharp", False),
        }

    # -----------------------------------------------------------------------
    # Mode change
    # -----------------------------------------------------------------------

    def _on_mode_change(self, _event: Any = None) -> None:
        mode = self._mode_var.get()
        # Update use_edge_contour checkbox to reflect mode
        if mode == "Outer Square (Edge+Contour)":
            if "use_edge_contour" in self._sv:
                self._sv["use_edge_contour"].set(True)
        elif mode == "Outer Square (Dark Box)":
            if "use_edge_contour" in self._sv:
                self._sv["use_edge_contour"].set(False)
        elif mode == "Micro-Grid First":
            if "use_microgrid_first" in self._sv:
                self._sv["use_microgrid_first"].set(True)
        self._status_var.set(f"Mode: {mode}")

    # -----------------------------------------------------------------------
    # Load image
    # -----------------------------------------------------------------------

    def _load_image(self) -> None:
        path = filedialog.askopenfilename(
            title="Load Image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.tiff"), ("All", "*.*")],
        )
        if not path:
            return
        try:
            img = Image.open(path).convert("RGB")
        except Exception as exc:
            messagebox.showerror("Load Error", str(exc))
            return

        self._img_orig = img
        self._stage_imgs.clear()
        self._set_stage_img("original", img)
        self._stage_var.set("original")
        self._fit_to_canvas(img)
        self._show_stage()
        self._update_thumbnails()
        self._status_var.set(f"Loaded: {Path(path).name}  ({img.width}×{img.height})")
        self._log_line(f"Loaded image: {path}  ({img.width}×{img.height})")

    # -----------------------------------------------------------------------
    # Draw mode (manual crop)
    # -----------------------------------------------------------------------

    def _enable_draw_mode(self) -> None:
        if self._img_orig is None:
            messagebox.showwarning("No Image", "Please load an image first.")
            return
        self._draw_mode = True
        self._cv.configure(cursor="tcross")
        self._status_var.set("Draw mode: click and drag on image to set crop rectangle.")

    def _clear_crop(self) -> None:
        self._manual_crop = None
        self._crop_status_var.set("No crop set")
        self._render_canvas()

    # -----------------------------------------------------------------------
    # Detection pipeline (runs in background thread)
    # -----------------------------------------------------------------------

    def _run_detection(self) -> None:
        if self._img_orig is None:
            messagebox.showwarning("No Image", "Please load an image first.")
            return
        if self._running:
            return
        if not _HAVE_DETECTION:
            messagebox.showerror(
                "Detection Unavailable",
                f"Could not import detection module:\n{_det_err_msg}",
            )
            return

        self._running = True
        self._status_var.set("⏳ Running detection…")
        self._log_line("─" * 40)
        self._log_line(f"Mode: {self._mode_var.get()}")

        bbox_cfg = self._build_bbox_config()
        micro_cfg = self._build_micro_config()
        pp_params = self._build_preprocess_params()
        img = self._img_orig.copy()
        mode = self._mode_var.get()
        manual_crop = self._manual_crop

        def _worker() -> None:
            try:
                self._detection_worker(img, mode, manual_crop,
                                       bbox_cfg, micro_cfg, pp_params)
            except Exception as exc:  # pragma: no cover
                import traceback
                tb = traceback.format_exc()
                self.after(0, lambda: self._log_line(f"UNHANDLED ERROR in detection thread:\n{tb}"))
                self.after(0, lambda: self._status_var.set(f"Detection failed: {exc}"))
            finally:
                self._running = False

        threading.Thread(target=_worker, daemon=True).start()

    def _detection_worker(
        self,
        img: "Image.Image",
        mode: str,
        manual_crop: Optional[Tuple[int, int, int, int]],
        bbox_cfg: "BboxConfig",
        micro_cfg: "MicrogridConfig",
        pp_params: dict,
    ) -> None:
        # Stage: original
        self.after(0, lambda: self._set_stage_img("original", img))

        # ── 1. Determine bbox ────────────────────────────
        bbox: Optional[Tuple[int, int, int, int]] = None
        bbox_result: Optional[BboxResult] = None

        if mode == "Manual Crop":
            if manual_crop is None:
                self.after(0, lambda: self._log_line(
                    "Manual Crop mode: no crop rectangle set – using full image."))
                bbox = (0, 0, img.width, img.height)
            else:
                bbox = manual_crop
                self.after(0, lambda: self._log_line(
                    f"Manual crop: x={bbox[0]}, y={bbox[1]}, w={bbox[2]}, h={bbox[3]}"))

        elif mode == "Micro-Grid First":
            self.after(0, lambda: self._log_line("Running microgrid-first bbox search…"))
            mgf_bbox, mgf_score = search_microgrid_first(img, micro_cfg)
            if mgf_bbox is not None:
                bbox = mgf_bbox
                self.after(0, lambda: self._log_line(
                    f"MGF bbox: {mgf_bbox}  score={mgf_score:.1f}"))
            else:
                self.after(0, lambda: self._log_line(
                    "MGF search returned no result – using full image."))
                bbox = (0, 0, img.width, img.height)

        else:
            self.after(0, lambda: self._log_line("Running find_map_bbox…"))
            bbox_result = find_map_bbox(img, bbox_cfg)
            for line in bbox_result.log:
                self.after(0, lambda l=line: self._log_line(l))
            if bbox_result.ok and bbox_result.bbox:
                bbox = bbox_result.bbox
            else:
                err = bbox_result.error if bbox_result else "unknown"
                self.after(0, lambda: self._log_line(f"bbox FAILED: {err}. Using full image."))
                bbox = (0, 0, img.width, img.height)

        # ── 2. BBox overlay stage ────────────────────────
        if bbox is not None:
            bbox_overlay = _draw_bbox_overlay(img, bbox, color=(0, 220, 80), width=3)
            self.after(0, lambda o=bbox_overlay: self._set_stage_img("bbox", o))
            self.after(0, lambda: self._log_line(
                f"bbox: x={bbox[0]}, y={bbox[1]}, w={bbox[2]}, h={bbox[3]}"))

        # ── 3. Crop ───────────────────────────────────────
        bx, by, bw, bh = bbox if bbox else (0, 0, img.width, img.height)
        bx = max(0, bx)
        by = max(0, by)
        bw = max(1, min(bw, img.width - bx))
        bh = max(1, min(bh, img.height - by))

        map_crop = img.crop((bx, by, bx + bw, by + bh))
        self.after(0, lambda c=map_crop: self._set_stage_img("crop", c))

        # ── 4. Preprocess ─────────────────────────────────
        self.after(0, lambda: self._log_line(
            f"Preprocess: gamma={pp_params['gamma']:.2f}, clahe={pp_params['clahe']},"
            f" autocontrast={pp_params['autocontrast']}, unsharp={pp_params['unsharp']}"))

        pp = preprocess_for_detection(
            map_crop,
            gamma=pp_params["gamma"],
            clahe=pp_params["clahe"],
            autocontrast=pp_params["autocontrast"],
            unsharp=pp_params["unsharp"],
        )
        enhanced: "Image.Image" = pp["enhanced"]
        edges_raw = pp["edges"]

        self.after(0, lambda e=enhanced: self._set_stage_img("preprocessed", e))

        edges_pil = _edges_to_pil(edges_raw)
        self.after(0, lambda ep=edges_pil: self._set_stage_img("edges", ep))

        # Brightness / edge density metrics
        try:
            import numpy as _np
            arr = _np.asarray(enhanced, dtype=_np.float32)
            brightness = float(arr.mean())
            earr = _np.asarray(edges_pil, dtype=_np.float32)
            edge_density = float((earr > 32).mean())
        except Exception:
            brightness = -1.0
            edge_density = -1.0
        self.after(0, lambda: self._log_line(
            f"Crop brightness={brightness:.1f}, edge_density={edge_density:.3f}"))

        # ── 5. Microgrid detection ────────────────────────
        self.after(0, lambda: self._log_line("Running detect_microgrid…"))
        micro_result: MicrogridResult = detect_microgrid(enhanced, micro_cfg)

        for line in micro_result.log:
            self.after(0, lambda l=line: self._log_line(l))

        self.after(0, lambda: self._log_line(
            f"micro_step=({micro_result.micro_step_x}, {micro_result.micro_step_y})"
            f"  module_step=({micro_result.module_step_x}, {micro_result.module_step_y})"
            f"  offset=({micro_result.offset_x}, {micro_result.offset_y})"
            f"  score=({micro_result.periodicity_score_x:.1f},"
            f" {micro_result.periodicity_score_y:.1f})"
        ))

        # ── 6. Overlay stages ─────────────────────────────
        mg_overlay = _draw_microgrid_overlay(map_crop, micro_result, 0, 0)
        grid_overlay = _draw_module_grid_overlay(map_crop, micro_result, 0, 0)

        self.after(0, lambda o=mg_overlay: self._set_stage_img("microgrid", o))
        self.after(0, lambda o=grid_overlay: self._set_stage_img("grid", o))

        # Infer grid size
        cw_px = micro_result.module_step_x or 1
        ch_px = micro_result.module_step_y or 1
        n_cols = max(1, round(bw / cw_px))
        n_rows = max(1, round(bh / ch_px))
        self.after(0, lambda: self._log_line(
            f"Grid size: ~{n_cols}×{n_rows} modules"))
        self.after(0, lambda: self._log_line("Detection complete."))

        # ── 7. Update UI ──────────────────────────────────
        def _finish() -> None:
            self._stage_var.set("bbox")
            self._update_thumbnails()
            self._fit_to_canvas(self._stage_imgs.get("bbox") or img)
            self._show_stage()
            self._status_var.set(
                f"Done. bbox=({bx},{by},{bw},{bh})  "
                f"micro=({micro_result.micro_step_x},{micro_result.micro_step_y})  "
                f"grid={n_cols}×{n_rows}"
            )

        self.after(0, _finish)

    # -----------------------------------------------------------------------
    # Preset save / load
    # -----------------------------------------------------------------------

    def _collect_settings_dict(self) -> dict:
        """Collect all current widget values into a plain dict."""
        d: Dict[str, Any] = {}
        d["mode"] = self._mode_var.get()
        for key, var in self._sv.items():
            try:
                d[key] = var.get()
            except Exception:
                pass
        return d

    def _apply_settings_dict(self, d: dict) -> None:
        """Apply a settings dict to all widgets."""
        if "mode" in d:
            self._mode_var.set(d["mode"])
            self._on_mode_change()
        for key, val in d.items():
            if key == "mode":
                continue
            var = self._sv.get(key)
            if var is None:
                continue
            try:
                var.set(val)
            except Exception:
                pass

    def _save_preset(self) -> None:
        name = simpledialog.askstring(
            "Save Preset", "Enter preset name:", parent=self)
        if not name:
            return
        name = name.strip().replace(" ", "_")
        if not name:
            return
        PRESET_DIR.mkdir(parents=True, exist_ok=True)
        path = PRESET_DIR / f"{name}.json"
        try:
            data = self._collect_settings_dict()
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
            self._log_line(f"Preset saved: {path}")
            self._status_var.set(f"Preset saved: {name}")
        except Exception as exc:
            messagebox.showerror("Save Error", str(exc))

    def _load_preset(self) -> None:
        PRESET_DIR.mkdir(parents=True, exist_ok=True)
        presets = sorted(PRESET_DIR.glob("*.json"))
        if not presets:
            messagebox.showinfo("No Presets", f"No presets found in:\n{PRESET_DIR}")
            return

        # Show a simple list dialog
        win = tk.Toplevel(self)
        win.title("Load Preset")
        win.configure(bg=PANEL)
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()

        tk.Label(win, text="Select a preset:", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 9), padx=12, pady=8).pack()

        lb = tk.Listbox(win, bg=PANEL2, fg=TEXT, selectbackground=ACCENT,
                        selectforeground="#111", font=("Segoe UI", 9),
                        width=36, height=min(len(presets), 12),
                        highlightthickness=0, bd=0)
        lb.pack(padx=12, pady=4)

        name_map = {}
        for p in presets:
            display = p.stem
            lb.insert("end", display)
            name_map[display] = p

        def _do_load() -> None:
            sel = lb.curselection()
            if not sel:
                return
            chosen = lb.get(sel[0])
            path = name_map[chosen]
            try:
                with open(path) as f:
                    data = json.load(f)
                self._apply_settings_dict(data)
                self._log_line(f"Preset loaded: {path}")
                self._status_var.set(f"Preset loaded: {chosen}")
            except Exception as exc:
                messagebox.showerror("Load Error", str(exc))
            win.destroy()

        btns = tk.Frame(win, bg=PANEL)
        btns.pack(pady=8)
        _flat_btn(btns, "Load", _do_load, fg="#111", bg=ACCENT).pack(side="left", padx=4)
        _flat_btn(btns, "Cancel", win.destroy, fg=DIM).pack(side="left", padx=4)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not _HAVE_TK:
        print("ERROR: tkinter is not available.", file=sys.stderr)
        sys.exit(1)
    if not _HAVE_PIL:
        print("ERROR: Pillow is not installed. Run: pip install Pillow", file=sys.stderr)
        sys.exit(1)
    app = CalibrationApp()
    app.mainloop()
