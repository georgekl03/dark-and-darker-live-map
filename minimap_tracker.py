#!/usr/bin/env python3
"""
minimap_tracker.py  –  Standalone Dark & Darker Minimap Tracker & Debug Tool
=============================================================================
Run directly:
    python minimap_tracker.py

This tool can:
  * Capture a configurable screen region (your minimap).
  * Load a test image from disk for offline debugging.
  * Detect the green dot at the player's feet (the definitive position marker).
  * Estimate the player's facing direction from the pale cursor arrow.
  * Show real-time debug overlays (green dot circle, direction ray, arrow mask).
  * Let you adjust all detection parameters with live preview.

Algorithm
---------
1. Capture the minimap region (configurable bounding box).
2. Convert to RGB and find "green" pixels: G channel dominant, S > threshold.
   The green dot in the centre of the player cursor is the most distinctive
   reliable feature.  Walls/floors do not contain this specific green.
3. Centroid of the green cluster = player world position (within the module).
4. For direction: sample a ring of pixels at a fixed radius around the
   green centroid.  The player cursor is a pale (high-brightness) shape with
   a black outline – the direction of the peak pale cluster on this ring is
   the facing direction.
5. A second, smaller ring can confirm or refine the direction.

Why this is robust
------------------
* Black wall borders and floor textures do not contain the unique green colour.
* The pale cursor is visually distinct from the map's grey/brown floor even
  when partially overlapping walls.
* Fixed-radius ring sampling means the logic is resolution-agnostic once the
  "green-dot radius" parameter is calibrated.

Dependencies: Pillow  (numpy optional but recommended)
"""

from __future__ import annotations

import json
import math
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk
from pathlib import Path
from typing import Optional, Callable

try:
    from PIL import Image, ImageDraw, ImageTk
    _HAVE_PIL = True
except ImportError:
    _HAVE_PIL = False

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

# ── Palette ──────────────────────────────────────────────
BG     = "#141414"
PANEL  = "#1c1c1c"
PANEL2 = "#242424"
BORDER = "#303030"
BDR2   = "#424242"
ACCENT = "#c8a84b"
TEXT   = "#e0ddd8"
DIM    = "#808080"
BTN_BG = "#2a2a2a"
BTN_H  = "#3c3c3c"


def flat_btn(parent, text, cmd, fg=TEXT, bg=BTN_BG, font=("Segoe UI", 9),
             padx=10, pady=4):
    return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, bd=0,
                     font=font, padx=padx, pady=pady,
                     activebackground=BTN_H, activeforeground=TEXT,
                     relief="flat", cursor="hand2")


# ── Screen capture ────────────────────────────────────────
def grab_region(left: int, top: int, width: int, height: int
                ) -> Optional[Image.Image]:
    box = (left, top, left + width, top + height)
    if _HAVE_MSS:
        try:
            with _mss_mod.mss() as sct:
                raw = sct.grab({"left": left, "top": top,
                                "width": width, "height": height})
                return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        except Exception:
            pass
    if _HAVE_IMAGEGRAB:
        try:
            return ImageGrab.grab(bbox=box)
        except Exception:
            pass
    return None


# ── Detection logic ───────────────────────────────────────
def find_green_dot(img: Image.Image,
                   g_min: int = 120,
                   g_r_ratio: float = 1.8,
                   g_b_ratio: float = 1.8
                   ) -> Optional[tuple[float, float]]:
    """Find the centroid of the green dot in a minimap RGB image.

    Parameters
    ----------
    img         : RGB minimap image.
    g_min       : Minimum G channel value (0-255) for a pixel to count as green.
    g_r_ratio   : G must be ≥ this multiple of R.
    g_b_ratio   : G must be ≥ this multiple of B.

    Returns
    -------
    (cx, cy) centroid in image pixel coords, or None if no green found.
    """
    w, h   = img.size
    pixels = list(img.getdata())
    green  = []
    for i, (r, g, b) in enumerate(pixels):
        if (g >= g_min
                and g >= g_r_ratio * max(r, 1)
                and g >= g_b_ratio * max(b, 1)):
            green.append((i % w, i // w))
    if len(green) < 2:
        return None
    cx = sum(x for x, _ in green) / len(green)
    cy = sum(y for _, y in green) / len(green)
    return (cx, cy)


def find_direction(img: Image.Image,
                   center: tuple[float, float],
                   ring_radius: float = 15.0,
                   samples: int = 36,
                   bright_thresh: int = 180
                   ) -> Optional[float]:
    """Estimate facing direction by sampling a ring of pixels around *center*.

    The player cursor is a pale (bright) arrow.  We sample *samples* equally-
    spaced points on a circle of *ring_radius* pixels around the green dot and
    find the direction with the most bright pixels.

    Parameters
    ----------
    ring_radius   : Radius in image pixels.  Should be large enough to reach the
                    arrow tip but not so large it picks up map features.
    samples       : Angular resolution.  36 → 10° steps.
    bright_thresh : Minimum pixel brightness (0-255) to count as cursor.

    Returns
    -------
    Angle in degrees (0 = up/north, 90 = right/east), or None.
    """
    w, h = img.size
    cx, cy = center
    gray   = img.convert("L")
    data   = gray.load()

    angle_scores: list[tuple[float, float]] = []  # (angle_deg, brightness)
    for i in range(samples):
        angle_deg = (360.0 / samples) * i
        angle_rad = math.radians(angle_deg - 90)   # 0° = north
        px = cx + ring_radius * math.cos(angle_rad)
        py = cy + ring_radius * math.sin(angle_rad)
        px_i, py_i = int(round(px)), int(round(py))
        if 0 <= px_i < w and 0 <= py_i < h:
            v = data[px_i, py_i]
        else:
            v = 0
        angle_scores.append((angle_deg, float(v)))

    # Apply a simple running-average smoothing to handle multi-pixel edges
    n = len(angle_scores)
    smoothed = []
    window = 3
    for i in range(n):
        avg = sum(angle_scores[(i + j) % n][1] for j in range(-window, window + 1))
        avg /= (2 * window + 1)
        smoothed.append((angle_scores[i][0], avg))

    # Find peak
    best = max(smoothed, key=lambda x: x[1])
    if best[1] < bright_thresh:
        return None
    return best[0]


def render_debug_overlay(img: Image.Image,
                         center: Optional[tuple[float, float]],
                         direction: Optional[float],
                         ring_radius: float = 15.0,
                         dot_radius: float = 4.0,
                         show_ring: bool = True,
                         show_direction: bool = True
                         ) -> Image.Image:
    """Return a copy of *img* with debug overlays drawn."""
    out  = img.convert("RGBA").copy()
    draw = ImageDraw.Draw(out)

    if center is not None:
        cx, cy = center
        r = dot_radius
        # Green dot circle
        draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                     outline=(0, 255, 0, 230), width=2)
        # Cross-hair
        draw.line([(cx - r * 2, cy), (cx + r * 2, cy)], fill=(0, 255, 0, 180), width=1)
        draw.line([(cx, cy - r * 2), (cx, cy + r * 2)], fill=(0, 255, 0, 180), width=1)

        if show_ring:
            # Ring used for direction sampling
            rr = ring_radius
            draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                         outline=(255, 200, 50, 120), width=1)

        if show_direction and direction is not None:
            ang_rad = math.radians(direction - 90)
            tip_len = ring_radius * 1.5
            tx = cx + tip_len * math.cos(ang_rad)
            ty = cy + tip_len * math.sin(ang_rad)
            draw.line([(cx, cy), (tx, ty)], fill=(255, 80, 80, 230), width=2)
            # Arrow head
            for da in (-30, 30):
                bx = tx + (tip_len * 0.3) * math.cos(math.radians(direction + 180 - da - 90))
                by = ty + (tip_len * 0.3) * math.sin(math.radians(direction + 180 - da - 90))
                draw.line([(tx, ty), (bx, by)], fill=(255, 80, 80, 230), width=2)

    del draw
    return out


# ── Live tracker thread ───────────────────────────────────
class LiveTracker:
    _INTERVAL_S = 0.12   # ~8 Hz

    def __init__(self, region: dict, callback: Callable, params: dict):
        self._region   = region
        self._callback = callback
        self._params   = params
        self._stop     = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.running   = False

    def start(self):
        self._stop.clear()
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self.running = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _loop(self):
        r = self._region
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                img = grab_region(r["left"], r["top"], r["width"], r["height"])
                if img is not None:
                    p = self._params
                    center = find_green_dot(img,
                                           g_min=p["g_min"],
                                           g_r_ratio=p["g_r_ratio"],
                                           g_b_ratio=p["g_b_ratio"])
                    direction = None
                    if center is not None:
                        direction = find_direction(img, center,
                                                   ring_radius=p["ring_radius"],
                                                   samples=p["samples"],
                                                   bright_thresh=p["bright_thresh"])
                    overlay = render_debug_overlay(img, center, direction,
                                                   ring_radius=p["ring_radius"],
                                                   dot_radius=p["dot_radius"],
                                                   show_ring=p["show_ring"],
                                                   show_direction=p["show_direction"])
                    self._callback(overlay, center, direction)
            except Exception:
                pass
            elapsed = time.monotonic() - t0
            self._stop.wait(max(0.0, self._INTERVAL_S - elapsed))


# ── Main App ──────────────────────────────────────────────
class TrackerApp(tk.Tk):
    """Debug GUI for the minimap tracker."""

    def __init__(self):
        super().__init__()
        self.title("D&D Minimap Tracker  –  Debug Tool")
        self.configure(bg=BG)
        self.geometry("900x700")
        self.minsize(700, 500)

        self._tracker: Optional[LiveTracker] = None
        self._static_img: Optional[Image.Image] = None   # image loaded from disk
        self._tkimgs: list = []

        # Detection parameters
        self._params = {
            "g_min":        tk.IntVar(value=120),
            "g_r_ratio":    tk.DoubleVar(value=1.8),
            "g_b_ratio":    tk.DoubleVar(value=1.8),
            "ring_radius":  tk.DoubleVar(value=15.0),
            "samples":      tk.IntVar(value=36),
            "bright_thresh":tk.IntVar(value=160),
            "dot_radius":   tk.DoubleVar(value=4.0),
            "show_ring":    tk.BooleanVar(value=True),
            "show_direction":tk.BooleanVar(value=True),
        }

        # Capture region
        self._region = {
            "left":   tk.IntVar(value=1700),
            "top":    tk.IntVar(value=860),
            "width":  tk.IntVar(value=220),
            "height": tk.IntVar(value=220),
        }

        self._last_center:    Optional[tuple[float, float]] = None
        self._last_direction: Optional[float] = None

        self._build()

    def _get_params(self) -> dict:
        return {k: v.get() for k, v in self._params.items()}

    def _get_region(self) -> dict:
        return {k: v.get() for k, v in self._region.items()}

    # ── UI ────────────────────────────────────────────────
    def _build(self):
        # Toolbar
        tb = tk.Frame(self, bg=PANEL2, pady=6)
        tb.pack(fill="x")

        flat_btn(tb, "📂  Load Image",   self._load_image).pack(side="left", padx=6)
        flat_btn(tb, "📷  Grab Region",  self._do_grab).pack(side="left", padx=2)
        tk.Frame(tb, bg=BDR2, width=1).pack(side="left", fill="y", padx=6)
        self._live_btn = flat_btn(tb, "▶  Start Live",
                                  self._toggle_live, fg="#111", bg=ACCENT)
        self._live_btn.pack(side="left", padx=2)
        flat_btn(tb, "🔍  Analyse",      self._analyse_static,
                 fg=TEXT, bg="#2e3020").pack(side="left", padx=4)

        self._status = tk.StringVar(value="Load an image or grab a region to begin.")
        tk.Label(tb, textvariable=self._status, bg=PANEL2, fg=DIM,
                 font=("Segoe UI", 9), padx=12, anchor="w",
                 wraplength=350).pack(side="left", fill="x", expand=True)

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        # Main PanedWindow: canvas (left) | controls (right)
        main = tk.PanedWindow(self, orient="horizontal", bg=BG, sashwidth=4)
        main.pack(fill="both", expand=True)

        # Image display canvas
        cv_frame = tk.Frame(main, bg=BG)
        main.add(cv_frame, minsize=480)
        self._cv = tk.Canvas(cv_frame, bg=BG, bd=0, highlightthickness=0)
        self._cv.pack(fill="both", expand=True)

        # Info bar below canvas
        info_f = tk.Frame(cv_frame, bg=PANEL2, pady=3)
        info_f.pack(fill="x")
        self._pos_lbl = tk.Label(info_f, text="Position: —", bg=PANEL2, fg=TEXT,
                                  font=("Segoe UI", 9), padx=8)
        self._pos_lbl.pack(side="left")
        self._dir_lbl = tk.Label(info_f, text="Direction: —", bg=PANEL2, fg=DIM,
                                  font=("Segoe UI", 9), padx=8)
        self._dir_lbl.pack(side="left")

        # Controls panel (scrollable)
        ctrl_outer = tk.Frame(main, bg=PANEL, width=310)
        ctrl_outer.pack_propagate(False)
        main.add(ctrl_outer, minsize=200)

        ctrl = tk.Frame(ctrl_outer, bg=PANEL)
        ctrl.pack(fill="both", expand=True, padx=8, pady=6)

        def sec(txt):
            tk.Frame(ctrl, bg=BORDER, height=1).pack(fill="x", pady=(8, 2))
            tk.Label(ctrl, text=txt.upper(), bg=PANEL, fg=DIM,
                     font=("Segoe UI", 8), anchor="w").pack(fill="x")

        # ── Capture region ────────────────────────────────
        sec("Capture Region  (screen pixels)")
        for lbl, key, lo, hi in [
            ("Left",   "left",   0, 7680),
            ("Top",    "top",    0, 4320),
            ("Width",  "width",  32, 800),
            ("Height", "height", 32, 800),
        ]:
            row = tk.Frame(ctrl, bg=PANEL)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=lbl, bg=PANEL, fg=TEXT,
                     font=("Segoe UI", 9), width=8, anchor="w").pack(side="left")
            sp = tk.Spinbox(row, from_=lo, to=hi, increment=1,
                            textvariable=self._region[key],
                            width=6, bg=PANEL2, fg=TEXT, bd=0,
                            buttonbackground=PANEL2, font=("Segoe UI", 9))
            sp.pack(side="left", padx=4)

        # ── Green dot detection ───────────────────────────
        sec("Green Dot Detection")
        for lbl, key, lo, hi, step in [
            ("Min G value",    "g_min",    0,   255, 5),
            ("G / R ratio",    "g_r_ratio",1.0, 5.0, 0.1),
            ("G / B ratio",    "g_b_ratio",1.0, 5.0, 0.1),
            ("Dot radius (px)","dot_radius",1,  30,  0.5),
        ]:
            row = tk.Frame(ctrl, bg=PANEL)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=lbl, bg=PANEL, fg=TEXT,
                     font=("Segoe UI", 9), width=17, anchor="w").pack(side="left")
            sp = tk.Spinbox(row, from_=lo, to=hi, increment=step,
                            textvariable=self._params[key],
                            width=6, bg=PANEL2, fg=TEXT, bd=0,
                            buttonbackground=PANEL2, font=("Segoe UI", 9))
            sp.pack(side="left", padx=4)
            sp.bind("<Return>", lambda e: self._analyse_static())

        # ── Direction detection ───────────────────────────
        sec("Direction Detection")
        for lbl, key, lo, hi, step in [
            ("Ring radius (px)",  "ring_radius",   5,  100, 1),
            ("Sample points",     "samples",       8,  360, 4),
            ("Bright threshold",  "bright_thresh", 50, 255, 5),
        ]:
            row = tk.Frame(ctrl, bg=PANEL)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=lbl, bg=PANEL, fg=TEXT,
                     font=("Segoe UI", 9), width=17, anchor="w").pack(side="left")
            sp = tk.Spinbox(row, from_=lo, to=hi, increment=step,
                            textvariable=self._params[key],
                            width=6, bg=PANEL2, fg=TEXT, bd=0,
                            buttonbackground=PANEL2, font=("Segoe UI", 9))
            sp.pack(side="left", padx=4)
            sp.bind("<Return>", lambda e: self._analyse_static())

        # ── Overlay options ───────────────────────────────
        sec("Overlay Options")
        for lbl, key in [("Show ring",      "show_ring"),
                          ("Show direction", "show_direction")]:
            tk.Checkbutton(ctrl, text=lbl, variable=self._params[key],
                           bg=PANEL, fg=TEXT, selectcolor=PANEL2,
                           activebackground=PANEL, activeforeground=ACCENT,
                           font=("Segoe UI", 9),
                           command=self._analyse_static).pack(anchor="w", pady=1)

        flat_btn(ctrl, "Apply & Refresh", self._analyse_static,
                 fg="#111", bg=ACCENT, padx=8, pady=3,
                 font=("Segoe UI", 9)).pack(pady=(10, 4), anchor="w")

    # ── Actions ───────────────────────────────────────────
    def _load_image(self):
        if not _HAVE_PIL:
            self._status.set("Pillow not installed.")
            return
        path = filedialog.askopenfilename(
            title="Open minimap image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp"), ("All", "*.*")])
        if not path:
            return
        try:
            self._static_img = Image.open(path).convert("RGB")
            self._status.set(f"Loaded: {Path(path).name}  {self._static_img.size}")
            self._analyse_static()
        except Exception as e:
            self._status.set(f"Error: {e}")

    def _do_grab(self):
        r = self._get_region()
        img = grab_region(r["left"], r["top"], r["width"], r["height"])
        if img is None:
            self._status.set("Grab failed – install mss or Pillow[ImageGrab].")
            return
        self._static_img = img
        self._status.set(f"Grabbed {img.size[0]}×{img.size[1]} from ({r['left']},{r['top']})")
        self._analyse_static()

    def _analyse_static(self, *_):
        if self._static_img is None:
            return
        img = self._static_img
        p = self._get_params()
        center = find_green_dot(img,
                                g_min=p["g_min"],
                                g_r_ratio=p["g_r_ratio"],
                                g_b_ratio=p["g_b_ratio"])
        direction = None
        if center is not None:
            direction = find_direction(img, center,
                                       ring_radius=p["ring_radius"],
                                       samples=p["samples"],
                                       bright_thresh=p["bright_thresh"])
        overlay = render_debug_overlay(img, center, direction,
                                       ring_radius=p["ring_radius"],
                                       dot_radius=p["dot_radius"],
                                       show_ring=p["show_ring"],
                                       show_direction=p["show_direction"])
        self._last_center    = center
        self._last_direction = direction
        self._display(overlay)
        self._update_info(center, direction)

    def _toggle_live(self):
        if self._tracker and self._tracker.running:
            self._tracker.stop()
            self._tracker = None
            self._live_btn.config(text="▶  Start Live", fg="#111", bg=ACCENT)
            self._status.set("Live tracking stopped.")
        else:
            if not (_HAVE_MSS or _HAVE_IMAGEGRAB):
                self._status.set("Install mss or Pillow[ImageGrab] for live tracking.")
                return
            params  = self._get_params()
            region  = self._get_region()
            self._tracker = LiveTracker(region, self._on_live_frame, params)
            self._tracker.start()
            self._live_btn.config(text="⏹  Stop Live", fg=TEXT, bg="#553030")
            self._status.set("Live tracking active…")

    def _on_live_frame(self, overlay: Image.Image,
                       center: Optional[tuple[float, float]],
                       direction: Optional[float]):
        """Called from the tracker thread – schedule GUI update on main thread."""
        self.after(0, self._apply_live_frame, overlay, center, direction)

    def _apply_live_frame(self, overlay: Image.Image,
                          center: Optional[tuple[float, float]],
                          direction: Optional[float]):
        # Update params from UI spinboxes while live
        if self._tracker:
            self._tracker._params = self._get_params()
        self._display(overlay)
        self._update_info(center, direction)

    def _display(self, img: Image.Image):
        cw = max(1, self._cv.winfo_width())
        ch = max(1, self._cv.winfo_height())
        iw, ih = img.size
        # Scale up for small minimap images so they're visible
        scale_up = min(cw / iw, ch / ih)
        nw = max(1, int(iw * scale_up))
        nh = max(1, int(ih * scale_up))
        disp = img.resize((nw, nh), Image.NEAREST).convert("RGBA")
        ti   = ImageTk.PhotoImage(disp)
        self._tkimgs = [ti]
        self._cv.delete("all")
        self._cv.create_image(cw // 2, ch // 2, anchor="center", image=ti)

    def _update_info(self, center, direction):
        if center is None:
            self._pos_lbl.config(text="Position: not detected", fg=DIM)
        else:
            self._pos_lbl.config(
                text=f"Position: ({center[0]:.1f}, {center[1]:.1f})", fg=TEXT)
        if direction is None:
            self._dir_lbl.config(text="Direction: not detected", fg=DIM)
        else:
            self._dir_lbl.config(text=f"Direction: {direction:.1f}°", fg=ACCENT)

    def destroy(self):
        if self._tracker:
            self._tracker.stop()
        super().destroy()


if __name__ == "__main__":
    if not _HAVE_PIL:
        print("ERROR: Pillow is required.  Run:  pip install Pillow")
        raise SystemExit(1)
    app = TrackerApp()
    app.mainloop()
