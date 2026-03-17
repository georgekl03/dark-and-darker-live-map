#!/usr/bin/env python3
"""
minimap_tracker.py  -  Dark & Darker Minimap Tracker  -  Calibration & Debug Tool
====================================================================================
Run directly:
    python minimap_tracker.py

Features
--------
* Capture a configurable screen region (your minimap) or load a test image.
* Robust green-dot detection via HSV colour thresholding.
* Cursor outline mask isolates the black outline of the triangular cursor.
* Circle-based direction estimation with two sampling circles (R1/R2).
* Optional cursor tip detection via raycast.
* Temporal smoothing (EMA) on pivot position and heading angle.
* Live-editing UI - every parameter is editable and overlays update immediately.
* Settings persistence - all parameters saved to data/minimap_settings.json.

Dependencies: Pillow  (numpy optional but strongly recommended for speed)
"""

from __future__ import annotations

import math
import threading
import time
import tkinter as tk
from tkinter import filedialog
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

import cursor_detect as cd

# -- Palette ------------------------------------------------------------------
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


# -- Screen capture -----------------------------------------------------------
def grab_region(left: int, top: int, width: int, height: int
                ) -> Optional["Image.Image"]:
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


# -- Overlay renderer ---------------------------------------------------------
def render_overlay(img, det, overlays, view_mode="composite"):
    """Compose all debug overlays onto img and return an RGBA image.

    Parameters
    ----------
    img       : Raw minimap RGB image (PIL).
    det       : Detection result dict from run_detection().
    overlays  : Bool flags keyed by overlay name.
    view_mode : "composite" | "green_mask" | "outline_mask"
    """
    if view_mode == "green_mask":
        gm = det.get("green_mask_img")
        base = img.convert("RGBA")
        if gm is not None:
            alpha_img = Image.eval(gm, lambda v: min(200, v))
            tint = Image.new("RGBA", base.size, (0, 220, 80, 0))
            base.paste(tint, mask=alpha_img)
        return base

    if view_mode == "outline_mask":
        om = det.get("outline_mask_img")
        mi = det.get("mask_info")
        base = img.convert("RGBA")
        if om is not None and mi is not None:
            ox, oy = mi["origin"]
            crop_rgba = Image.new("RGBA", om.size, (220, 60, 60, 0))
            alpha_img = Image.eval(om, lambda v: min(200, v))
            crop_out  = Image.new("RGBA", om.size, (0, 0, 0, 0))
            crop_out.paste(crop_rgba, mask=alpha_img)
            base.paste(crop_out, (ox, oy), crop_out)
        return base

    # Full composite view
    out  = img.convert("RGBA").copy()
    draw = ImageDraw.Draw(out)

    center       = det.get("center")
    heading      = det.get("heading")
    mask_info    = det.get("mask_info")
    dir_result   = det.get("dir_result")
    tip_result   = det.get("tip_result")
    pivot_conf   = det.get("pivot_conf",   0.0)
    heading_conf = det.get("heading_conf", 0.0)

    # Green blob tint
    if overlays.get("show_green_mask"):
        gm = det.get("green_mask_img")
        if gm is not None:
            alpha = Image.eval(gm, lambda v: min(90, v // 3))
            tint  = Image.new("RGBA", out.size, (0, 220, 80, 0))
            out.paste(tint, mask=alpha)

    # Outline mask tint in local crop region
    if overlays.get("show_outline_mask") and mask_info is not None:
        om = det.get("outline_mask_img")
        if om is not None:
            ox, oy = mask_info["origin"]
            alpha     = Image.eval(om, lambda v: min(130, v // 2))
            tint_crop = Image.new("RGBA", om.size, (220, 60, 60, 0))
            patch     = Image.new("RGBA", om.size, (0, 0, 0, 0))
            patch.paste(tint_crop, mask=alpha)
            out.paste(patch, (ox, oy), patch)

    # Pivot crosshair
    if overlays.get("show_pivot") and center is not None:
        cx, cy = center
        r = 5
        draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                     outline=(0, 255, 100, 230), width=2)
        arm = r * 2
        draw.line([(cx - arm, cy), (cx + arm, cy)], fill=(0, 255, 100, 160), width=1)
        draw.line([(cx, cy - arm), (cx, cy + arm)], fill=(0, 255, 100, 160), width=1)

    # Sampling circles (R1=yellow, R2=blue)
    if overlays.get("show_circles") and center is not None and dir_result is not None:
        cx, cy = center
        r1 = dir_result.get("r1", 0)
        r2 = dir_result.get("r2", 0)
        if r1 > 0:
            draw.ellipse([cx - r1, cy - r1, cx + r1, cy + r1],
                         outline=(255, 200, 50, 110), width=1)
        if r2 > 0:
            draw.ellipse([cx - r2, cy - r2, cx + r2, cy + r2],
                         outline=(80, 200, 255, 110), width=1)

    # Circle hit points
    if overlays.get("show_hits") and center is not None and dir_result is not None:
        cx, cy = center
        for src, rad, col in [
            (dir_result.get("r1_samples", []), dir_result.get("r1", 0),
             (255, 140, 0, 210)),
            (dir_result.get("r2_samples", []), dir_result.get("r2", 0),
             (0, 200, 255, 210)),
        ]:
            for angle_deg, hit in src:
                if hit:
                    ang_rad = math.radians(angle_deg)
                    hx = int(round(cx + rad * math.sin(ang_rad)))
                    hy = int(round(cy - rad * math.cos(ang_rad)))
                    draw.ellipse([hx - 2, hy - 2, hx + 2, hy + 2], fill=col)

    # Cluster midpoint lines + raw bisector
    if overlays.get("show_bisector") and center is not None and dir_result is not None:
        cx, cy = center
        r_vis = dir_result.get("r2", 20) + 10
        for cl in (dir_result.get("clusters") or [])[:2]:
            mid_rad = math.radians(cl["mid"])
            mx = cx + r_vis * math.sin(mid_rad)
            my = cy - r_vis * math.cos(mid_rad)
            draw.line([(cx, cy), (mx, my)], fill=(160, 80, 220, 180), width=1)
            draw.ellipse([mx - 3, my - 3, mx + 3, my + 3],
                         fill=(160, 80, 220, 200))
        bisector = dir_result.get("bisector")
        if bisector is not None:
            bis_rad = math.radians(bisector)
            blen = r_vis + 12
            bx = cx + blen * math.sin(bis_rad)
            by = cy - blen * math.cos(bis_rad)
            draw.line([(cx, cy), (bx, by)], fill=(200, 100, 255, 160), width=1)

    # Final heading arrow (red)
    if overlays.get("show_heading") and center is not None and heading is not None:
        cx, cy = center
        tip_len = (dir_result.get("r2", 20) if dir_result else 20) * 1.6
        ang_rad = math.radians(heading)
        tx = cx + tip_len * math.sin(ang_rad)
        ty = cy - tip_len * math.cos(ang_rad)
        draw.line([(cx, cy), (tx, ty)], fill=(255, 70, 70, 240), width=2)
        for da in (-30, 30):
            back_rad = math.radians(heading + 180.0 + da)
            head_len = tip_len * 0.3
            bx = tx + head_len * math.sin(back_rad)
            by = ty - head_len * math.cos(back_rad)
            draw.line([(tx, ty), (bx, by)], fill=(255, 70, 70, 240), width=2)

    # Tip (magenta ring) and tracking point (white dot)
    if overlays.get("show_tip") and tip_result is not None:
        tip = tip_result.get("tip")
        trk = tip_result.get("track_pt")
        if tip:
            draw.ellipse([tip[0] - 3, tip[1] - 3, tip[0] + 3, tip[1] + 3],
                         outline=(255, 40, 200, 210), width=2)
        if trk:
            draw.ellipse([trk[0] - 2, trk[1] - 2, trk[0] + 2, trk[1] + 2],
                         fill=(255, 255, 255, 200))

    # Debug text readout
    if overlays.get("show_debug_text"):
        lines = []
        if center:
            lines.append(
                "Pivot ({:.1f}, {:.1f})  conf {:.2f}".format(
                    center[0], center[1], pivot_conf))
        else:
            lines.append("Pivot: not detected")
        if heading is not None:
            lines.append("Heading {:.1f} deg  conf {:.2f}".format(
                heading, heading_conf))
        else:
            lines.append("Heading: not detected")
        n_cl = len((dir_result or {}).get("clusters") or [])
        lines.append("Clusters: {}".format(n_cl))
        y0 = 4
        char_w = 6
        for ln in lines:
            draw.rectangle([2, y0 - 1, 2 + len(ln) * char_w, y0 + 12],
                           fill=(0, 0, 0, 140))
            draw.text((3, y0), ln, fill=(220, 220, 200, 230))
            y0 += 14

    del draw
    return out


# -- Single-frame detection ---------------------------------------------------
def run_detection(img, settings):
    """Run the full detection pipeline on img using settings.

    Returns a flat dict used by the overlay renderer and info panel.
    """
    result = {
        "center":           None,
        "heading":          None,
        "pivot_conf":       0.0,
        "heading_conf":     0.0,
        "green_mask_img":   None,
        "outline_mask_img": None,
        "mask_info":        None,
        "dir_result":       None,
        "tip_result":       None,
    }

    # 1) Green dot pivot
    gd = cd.find_green_dot(img, settings.get("green_dot"))
    if gd:
        result["green_mask_img"] = gd.get("mask_img")
        if gd.get("center") is not None:
            result["center"]     = gd["center"]
            result["pivot_conf"] = gd.get("confidence", 0.0)

    if result["center"] is None:
        return result

    pivot = result["center"]

    # 2) Outline mask
    mask_info = cd.build_outline_mask(img, pivot, settings.get("outline"))
    if mask_info:
        result["mask_info"]        = mask_info
        result["outline_mask_img"] = mask_info.get("mask_img")

    # 3) Circle-based direction
    dir_res = cd.find_direction_circles(
        img, pivot,
        params_outline=settings.get("outline"),
        params_dir=settings.get("direction"),
    )
    if dir_res:
        p_dir = settings.get("direction", cd.DEFAULT_MINIMAP_SETTINGS["direction"])
        dir_res["r1"] = float(p_dir["r1"])
        dir_res["r2"] = float(p_dir["r2"])
        result["dir_result"]   = dir_res
        result["heading"]      = dir_res.get("heading")
        result["heading_conf"] = dir_res.get("confidence", 0.0)

    # 4) Raycast tip (optional)
    if result["heading"] is not None and settings.get("tip", {}).get("enabled", True):
        result["tip_result"] = cd.raycast_tip(
            img, pivot, result["heading"],
            mask_info=mask_info,
            params_tip=settings.get("tip"),
            params_outline=settings.get("outline"),
        )

    return result


# -- Live tracker thread ------------------------------------------------------
class LiveTracker:
    _INTERVAL_S = 0.12

    def __init__(self, region, settings, callback):
        self._region   = region
        self._settings = settings
        self._callback = callback
        self._stop     = threading.Event()
        self._thread   = None
        self.running   = False
        self._prev_center  = None
        self._prev_heading = None

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

    def update_settings(self, settings):
        self._settings = settings

    def _loop(self):
        r = self._region
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                img = grab_region(r["left"], r["top"], r["width"], r["height"])
                if img is not None:
                    det = run_detection(img, self._settings)
                    sm  = self._settings.get(
                        "smoothing", cd.DEFAULT_MINIMAP_SETTINGS["smoothing"])
                    det["center"] = cd.smooth_pos(
                        det["center"], self._prev_center,
                        alpha=sm.get("pivot_alpha", 0.5))
                    det["heading"] = cd.smooth_angle(
                        det["heading"], self._prev_heading,
                        alpha=sm.get("heading_alpha", 0.4),
                        max_delta=sm.get("max_heading_delta", 60.0))
                    self._prev_center  = det["center"]
                    self._prev_heading = det["heading"]
                    self._callback(img, det)
            except Exception:
                pass
            elapsed = time.monotonic() - t0
            self._stop.wait(max(0.0, self._INTERVAL_S - elapsed))


# -- Scrollable inner frame ---------------------------------------------------
class _ScrollFrame(tk.Frame):
    def __init__(self, parent, bg=PANEL, **kwargs):
        super().__init__(parent, bg=bg, **kwargs)
        vbar = tk.Scrollbar(self, orient="vertical", bg=PANEL,
                            troughcolor=PANEL2)
        vbar.pack(side="right", fill="y")
        self._cv = tk.Canvas(self, bg=bg, bd=0, highlightthickness=0,
                             yscrollcommand=vbar.set)
        self._cv.pack(side="left", fill="both", expand=True)
        vbar.config(command=self._cv.yview)
        self.inner = tk.Frame(self._cv, bg=bg)
        self._win  = self._cv.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._inner_cfg)
        self._cv.bind("<Configure>",   self._cv_cfg)
        self._cv.bind("<MouseWheel>",  self._wheel)
        self._cv.bind("<Button-4>",    self._wheel)
        self._cv.bind("<Button-5>",    self._wheel)

    def _inner_cfg(self, _e):
        self._cv.configure(scrollregion=self._cv.bbox("all"))

    def _cv_cfg(self, e):
        self._cv.itemconfig(self._win, width=e.width)

    def _wheel(self, e):
        if e.num == 4 or getattr(e, "delta", 0) > 0:
            self._cv.yview_scroll(-1, "units")
        else:
            self._cv.yview_scroll(1, "units")


# -- Main App -----------------------------------------------------------------
class TrackerApp(tk.Tk):
    """Full calibration and debug GUI for the minimap cursor tracker."""

    def __init__(self):
        super().__init__()
        self.title("D&D Minimap Tracker  -  Calibration & Debug Tool")
        self.configure(bg=BG)
        self.geometry("1100x760")
        self.minsize(800, 560)

        self._settings = cd.load_minimap_settings()
        self._tracker    = None
        self._static_img = None
        self._tkimgs     = []
        self._debounce   = None

        # Build tk.Vars from loaded settings
        self._roi_vars = self._make_roi_vars()
        self._gd_vars  = self._make_gd_vars()
        self._out_vars = self._make_out_vars()
        self._dir_vars = self._make_dir_vars()
        self._tip_vars = self._make_tip_vars()
        self._sm_vars  = self._make_sm_vars()
        self._ov_vars  = self._make_ov_vars()
        self._view_var = tk.StringVar(value="composite")

        self._pivot_conf_var    = tk.StringVar(value="--")
        self._heading_conf_var  = tk.StringVar(value="--")
        self._cluster_count_var = tk.StringVar(value="--")

        self._build()
        self._trace_all_vars()

    # -- Variable factories ---------------------------------------------------
    def _make_roi_vars(self):
        r = self._settings["roi"]
        return {k: tk.IntVar(value=r[k]) for k in ("left", "top", "width", "height")}

    def _make_gd_vars(self):
        g = self._settings["green_dot"]
        return {
            "h_min":        tk.IntVar(value=g["h_min"]),
            "h_max":        tk.IntVar(value=g["h_max"]),
            "s_min":        tk.IntVar(value=g["s_min"]),
            "s_max":        tk.IntVar(value=g["s_max"]),
            "v_min":        tk.IntVar(value=g["v_min"]),
            "v_max":        tk.IntVar(value=g["v_max"]),
            "morph_kernel": tk.IntVar(value=g["morph_kernel"]),
            "min_area":     tk.IntVar(value=g["min_area"]),
            "max_area":     tk.IntVar(value=g["max_area"]),
        }

    def _make_out_vars(self):
        o = self._settings["outline"]
        return {
            "dark_thresh":  tk.IntVar(value=o["dark_thresh"]),
            "local_radius": tk.IntVar(value=o["local_radius"]),
            "morph_kernel": tk.IntVar(value=o["morph_kernel"]),
        }

    def _make_dir_vars(self):
        d = self._settings["direction"]
        return {
            "r1":          tk.DoubleVar(value=d["r1"]),
            "r2":          tk.DoubleVar(value=d["r2"]),
            "samples":     tk.IntVar(value=d["samples"]),
            "cluster_gap": tk.DoubleVar(value=d["cluster_gap"]),
            "min_hits":    tk.IntVar(value=d["min_hits"]),
        }

    def _make_tip_vars(self):
        t = self._settings["tip"]
        return {
            "enabled":     tk.BooleanVar(value=t["enabled"]),
            "raycast_max": tk.IntVar(value=t["raycast_max"]),
            "track_dist":  tk.DoubleVar(value=t["track_dist"]),
        }

    def _make_sm_vars(self):
        s = self._settings["smoothing"]
        return {
            "heading_alpha":     tk.DoubleVar(value=s["heading_alpha"]),
            "pivot_alpha":       tk.DoubleVar(value=s["pivot_alpha"]),
            "max_heading_delta": tk.DoubleVar(value=s["max_heading_delta"]),
        }

    def _make_ov_vars(self):
        return {k: tk.BooleanVar(value=v)
                for k, v in self._settings["overlays"].items()}

    # -- Trace changes -> debounced re-analyse --------------------------------
    def _trace_all_vars(self):
        all_vars = (list(self._gd_vars.values())  +
                    list(self._out_vars.values()) +
                    list(self._dir_vars.values()) +
                    list(self._tip_vars.values()) +
                    list(self._sm_vars.values())  +
                    list(self._ov_vars.values())  +
                    [self._view_var])
        for v in all_vars:
            v.trace_add("write", self._on_param_change)

    def _on_param_change(self, *_):
        if self._debounce:
            self.after_cancel(self._debounce)
        self._debounce = self.after(80, self._analyse_static)

    # -- UI construction ------------------------------------------------------
    def _build(self):
        # Toolbar
        tb = tk.Frame(self, bg=PANEL2, pady=6)
        tb.pack(fill="x")

        flat_btn(tb, "Load Image",  self._load_image).pack(side="left", padx=6)
        flat_btn(tb, "Grab Region", self._do_grab).pack(side="left", padx=2)
        tk.Frame(tb, bg=BDR2, width=1).pack(side="left", fill="y", padx=6)
        self._live_btn = flat_btn(tb, "Start Live", self._toggle_live,
                                  fg="#111", bg=ACCENT)
        self._live_btn.pack(side="left", padx=2)
        flat_btn(tb, "Analyse", self._analyse_static,
                 fg=TEXT, bg="#2e3020").pack(side="left", padx=4)

        # View mode selector
        tk.Frame(tb, bg=BDR2, width=1).pack(side="left", fill="y", padx=8)
        tk.Label(tb, text="View:", bg=PANEL2, fg=DIM,
                 font=("Segoe UI", 9)).pack(side="left")
        for val, label in [("composite",    "Composite"),
                            ("green_mask",   "Green Mask"),
                            ("outline_mask", "Outline Mask")]:
            tk.Radiobutton(
                tb, text=label, variable=self._view_var, value=val,
                bg=PANEL2, fg=TEXT, selectcolor=BG,
                activebackground=PANEL2, activeforeground=ACCENT,
                font=("Segoe UI", 9),
            ).pack(side="left", padx=2)

        self._status_var = tk.StringVar(
            value="Load an image or grab a region to begin.")
        tk.Label(tb, textvariable=self._status_var, bg=PANEL2, fg=DIM,
                 font=("Segoe UI", 9), padx=12, anchor="w",
                 wraplength=280).pack(side="left", fill="x", expand=True)

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        # Main PanedWindow: canvas (left) | controls (right)
        main = tk.PanedWindow(self, orient="horizontal", bg=BG, sashwidth=4)
        main.pack(fill="both", expand=True)

        # Canvas side
        cv_frame = tk.Frame(main, bg=BG)
        main.add(cv_frame, minsize=480)
        self._cv = tk.Canvas(cv_frame, bg=BG, bd=0, highlightthickness=0)
        self._cv.pack(fill="both", expand=True)

        info_f = tk.Frame(cv_frame, bg=PANEL2, pady=3)
        info_f.pack(fill="x")
        for txt, var, col in [
            ("Pivot:",   self._pivot_conf_var,    TEXT),
            ("Heading:", self._heading_conf_var,  ACCENT),
            ("Clusters:", self._cluster_count_var, DIM),
        ]:
            tk.Label(info_f, text=txt, bg=PANEL2, fg=DIM,
                     font=("Segoe UI", 9), padx=6).pack(side="left")
            tk.Label(info_f, textvariable=var, bg=PANEL2, fg=col,
                     font=("Segoe UI", 9), padx=2).pack(side="left")
            tk.Frame(info_f, bg=BDR2, width=1).pack(side="left", fill="y", padx=4)

        # Controls side (scrollable)
        ctrl_outer = tk.Frame(main, bg=PANEL, width=340)
        ctrl_outer.pack_propagate(False)
        main.add(ctrl_outer, minsize=220)

        sf   = _ScrollFrame(ctrl_outer, bg=PANEL)
        sf.pack(fill="both", expand=True)
        ctrl = sf.inner

        def sec(label, hint=""):
            tk.Frame(ctrl, bg=BORDER, height=1).pack(fill="x", pady=(10, 2))
            row = tk.Frame(ctrl, bg=PANEL)
            row.pack(fill="x")
            tk.Label(row, text=label.upper(), bg=PANEL, fg=DIM,
                     font=("Segoe UI", 8, "bold"), anchor="w").pack(side="left")
            if hint:
                tk.Label(row, text="  (" + hint + ")", bg=PANEL, fg=DIM,
                         font=("Segoe UI", 7), anchor="w").pack(side="left")

        def spin(lbl, var, lo, hi, inc, hint=""):
            row = tk.Frame(ctrl, bg=PANEL)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=lbl, bg=PANEL, fg=TEXT, font=("Segoe UI", 9),
                     width=22, anchor="w").pack(side="left")
            sp = tk.Spinbox(row, from_=lo, to=hi, increment=inc,
                            textvariable=var, width=7, bg=PANEL2, fg=TEXT, bd=0,
                            buttonbackground=PANEL2, font=("Segoe UI", 9))
            sp.pack(side="left", padx=3)
            if hint:
                tk.Label(row, text=hint, bg=PANEL, fg=DIM,
                         font=("Segoe UI", 7)).pack(side="left", padx=2)
            sp.bind("<Return>", lambda e: self._analyse_static())

        def chk(lbl, var, hint=""):
            row = tk.Frame(ctrl, bg=PANEL)
            row.pack(fill="x", pady=1)
            tk.Checkbutton(row, text=lbl, variable=var, bg=PANEL, fg=TEXT,
                           selectcolor=PANEL2, activebackground=PANEL,
                           activeforeground=ACCENT, font=("Segoe UI", 9),
                           command=self._analyse_static).pack(side="left")
            if hint:
                tk.Label(row, text=hint, bg=PANEL, fg=DIM,
                         font=("Segoe UI", 7)).pack(side="left", padx=4)

        # Section: Capture Region
        sec("Capture Region", "screen-pixel coordinates of your minimap")
        spin("Left  (screen X)",  self._roi_vars["left"],   0, 7680, 1)
        spin("Top   (screen Y)",  self._roi_vars["top"],    0, 4320, 1)
        spin("Width   (px)",      self._roi_vars["width"],  32, 800, 1)
        spin("Height  (px)",      self._roi_vars["height"], 32, 800, 1)

        # Section: Green Dot Detection
        sec("Green Dot Detection", "HSV colour thresholds  H 0-179, S/V 0-255")
        spin("Hue min  (H_min)",        self._gd_vars["h_min"],  0, 179, 1,
             "lower hue bound  (green ~40)")
        spin("Hue max  (H_max)",        self._gd_vars["h_max"],  0, 179, 1,
             "upper hue bound  (green ~90)")
        spin("Saturation min",          self._gd_vars["s_min"],  0, 255, 5,
             "reject grey/white pixels")
        spin("Saturation max",          self._gd_vars["s_max"],  0, 255, 5)
        spin("Value (brightness) min",  self._gd_vars["v_min"],  0, 255, 5,
             "catches dim dot edges")
        spin("Value (brightness) max",  self._gd_vars["v_max"],  0, 255, 5)
        spin("Morph kernel  (px)",      self._gd_vars["morph_kernel"], 0, 10, 1,
             "join blob fragments")
        spin("Min area  (px2)",         self._gd_vars["min_area"],  1, 500, 1,
             "discard noise specks")
        spin("Max area  (px2)",         self._gd_vars["max_area"],  4, 2000, 10,
             "discard large false blobs")

        # Section: Cursor Outline Mask
        sec("Cursor Outline Mask", "dark pixel extraction in local window")
        spin("Dark threshold",     self._out_vars["dark_thresh"],  10, 200, 5,
             "pixels < this are black outline")
        spin("Local window  (px)", self._out_vars["local_radius"], 8, 80, 1,
             "half-size of processing area")
        spin("Morph kernel  (px)", self._out_vars["morph_kernel"], 0, 8, 1,
             "connect outline fragments")

        # Section: Direction Detection
        sec("Direction Detection", "two circles sample outline intersections")
        spin("R1 - inner radius (px)", self._dir_vars["r1"],  2, 60, 0.5,
             "inner circle crosses cursor sides")
        spin("R2 - outer radius (px)", self._dir_vars["r2"],  4, 80, 0.5,
             "outer circle near cursor tip")
        spin("Samples per circle",     self._dir_vars["samples"], 16, 360, 4,
             "360/N = step in degrees")
        spin("Cluster gap  (deg)",     self._dir_vars["cluster_gap"], 5, 90, 1,
             "gap separating L/R edge clusters")
        spin("Min hits per cluster",   self._dir_vars["min_hits"], 1, 20, 1,
             "reject tiny noise clusters")

        # Section: Tip Detection
        sec("Tip Detection", "optional raycast from pivot along heading")
        chk("Enable tip raycast", self._tip_vars["enabled"])
        spin("Max raycast dist (px)",   self._tip_vars["raycast_max"], 4, 100, 1,
             "search distance from pivot")
        spin("Tracking pt dist (px)",   self._tip_vars["track_dist"], 0, 50, 0.5,
             "stable tracking point offset from pivot")

        # Section: Temporal Smoothing
        sec("Smoothing", "EMA temporal filter to reduce jitter")
        spin("Heading alpha",          self._sm_vars["heading_alpha"],
             0.05, 1.0, 0.05, "weight for newest heading  (1=no smooth)")
        spin("Pivot alpha",            self._sm_vars["pivot_alpha"],
             0.05, 1.0, 0.05, "weight for newest position  (1=no smooth)")
        spin("Max heading delta (deg)", self._sm_vars["max_heading_delta"],
             5.0, 180.0, 5.0, "clamp per-frame heading change")

        # Section: Overlay options
        sec("Overlay Options", "toggle visualisation layers in Composite view")
        for key, label, hint in [
            ("show_green_mask",   "Green blob overlay",
             "HSV mask tinted green"),
            ("show_outline_mask", "Outline mask overlay",
             "dark-pixel mask tinted red in local window"),
            ("show_pivot",        "Pivot crosshair",
             "green dot centroid circle"),
            ("show_circles",      "R1 / R2 sampling circles",
             "yellow=R1, blue=R2"),
            ("show_hits",         "Circle hit points",
             "outline intersections  orange=R1  cyan=R2"),
            ("show_bisector",     "Cluster lines and bisector",
             "edge midpoints + raw bisector"),
            ("show_heading",      "Heading arrow",
             "confirmed forward direction  (red)"),
            ("show_tip",          "Tip and tracking point",
             "magenta ring=tip  white dot=track pt"),
            ("show_debug_text",   "Debug text readout",
             "position, heading, confidence numbers"),
        ]:
            chk(label, self._ov_vars[key], "  " + hint)

        # Section: Save / Load
        tk.Frame(ctrl, bg=BORDER, height=1).pack(fill="x", pady=(10, 4))
        btn_row = tk.Frame(ctrl, bg=PANEL)
        btn_row.pack(fill="x", pady=4)
        flat_btn(btn_row, "Save Settings", self._save_settings,
                 fg="#111", bg=ACCENT, padx=8, pady=4).pack(side="left", padx=4)
        flat_btn(btn_row, "Load Settings", self._load_settings,
                 fg=TEXT, bg=BTN_BG, padx=8, pady=4).pack(side="left", padx=4)

        tk.Label(ctrl,
                 text="Saved to: data/minimap_settings.json\n"
                      "Loaded by map_viewer.py when tracking starts.",
                 bg=PANEL, fg=DIM, font=("Segoe UI", 8),
                 justify="left", wraplength=300
                 ).pack(anchor="w", padx=6, pady=(2, 10))

    # -- Settings helpers -----------------------------------------------------
    def _collect_settings(self):
        s = cd._deep_copy(self._settings)
        for k, v in self._roi_vars.items():
            s["roi"][k] = v.get()
        for k, v in self._gd_vars.items():
            s["green_dot"][k] = v.get()
        for k, v in self._out_vars.items():
            s["outline"][k] = v.get()
        for k, v in self._dir_vars.items():
            s["direction"][k] = v.get()
        for k, v in self._tip_vars.items():
            s["tip"][k] = v.get()
        for k, v in self._sm_vars.items():
            s["smoothing"][k] = v.get()
        for k, v in self._ov_vars.items():
            s["overlays"][k] = v.get()
        return s

    def _apply_settings_to_vars(self, s):
        for k, v in self._roi_vars.items():
            v.set(s["roi"].get(k, v.get()))
        for k, v in self._gd_vars.items():
            v.set(s["green_dot"].get(k, v.get()))
        for k, v in self._out_vars.items():
            v.set(s["outline"].get(k, v.get()))
        for k, v in self._dir_vars.items():
            v.set(s["direction"].get(k, v.get()))
        for k, v in self._tip_vars.items():
            v.set(s["tip"].get(k, v.get()))
        for k, v in self._sm_vars.items():
            v.set(s["smoothing"].get(k, v.get()))
        for k, v in self._ov_vars.items():
            v.set(s["overlays"].get(k, v.get()))

    # -- Actions --------------------------------------------------------------
    def _load_image(self):
        path = filedialog.askopenfilename(
            title="Open minimap image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp"), ("All", "*.*")])
        if not path:
            return
        try:
            self._static_img = Image.open(path).convert("RGB")
            self._status_var.set(
                "Loaded: {}  {}".format(Path(path).name, self._static_img.size))
            self._analyse_static()
        except Exception as exc:
            self._status_var.set("Error loading image: {}".format(exc))

    def _do_grab(self):
        img = grab_region(
            self._roi_vars["left"].get(),
            self._roi_vars["top"].get(),
            self._roi_vars["width"].get(),
            self._roi_vars["height"].get(),
        )
        if img is None:
            self._status_var.set(
                "Grab failed - install mss or Pillow[ImageGrab].")
            return
        self._static_img = img
        self._status_var.set(
            "Grabbed {}x{} from ({}, {})".format(
                img.size[0], img.size[1],
                self._roi_vars["left"].get(),
                self._roi_vars["top"].get()))
        self._analyse_static()

    def _analyse_static(self, *_):
        if self._static_img is None:
            return
        settings = self._collect_settings()
        det = run_detection(self._static_img, settings)
        overlay = render_overlay(
            self._static_img, det,
            overlays=settings["overlays"],
            view_mode=self._view_var.get(),
        )
        self._display(overlay)
        self._update_info(det)

    def _toggle_live(self):
        if self._tracker and self._tracker.running:
            self._tracker.stop()
            self._tracker = None
            self._live_btn.config(text="Start Live", fg="#111", bg=ACCENT)
            self._status_var.set("Live tracking stopped.")
        else:
            if not (_HAVE_MSS or _HAVE_IMAGEGRAB):
                self._status_var.set(
                    "Install mss or Pillow[ImageGrab] for live tracking.")
                return
            roi = {k: v.get() for k, v in self._roi_vars.items()}
            self._tracker = LiveTracker(
                roi, self._collect_settings(), self._on_live_frame)
            self._tracker.start()
            self._live_btn.config(text="Stop Live", fg=TEXT, bg="#553030")
            self._status_var.set("Live tracking active...")

    def _on_live_frame(self, img, det):
        self.after(0, self._apply_live_frame, img, det)

    def _apply_live_frame(self, img, det):
        if self._tracker:
            self._tracker.update_settings(self._collect_settings())
        settings = self._collect_settings()
        overlay = render_overlay(
            img, det,
            overlays=settings["overlays"],
            view_mode=self._view_var.get(),
        )
        self._display(overlay)
        self._update_info(det)

    def _save_settings(self):
        s = self._collect_settings()
        cd.save_minimap_settings(s)
        self._settings = s
        self._status_var.set("Saved to {}".format(cd.MINIMAP_SETTINGS_PATH.name))

    def _load_settings(self):
        s = cd.load_minimap_settings()
        self._settings = s
        self._apply_settings_to_vars(s)
        self._analyse_static()
        self._status_var.set(
            "Loaded from {}".format(cd.MINIMAP_SETTINGS_PATH.name))

    # -- Display helpers ------------------------------------------------------
    def _display(self, img):
        cw = max(1, self._cv.winfo_width())
        ch = max(1, self._cv.winfo_height())
        iw, ih = img.size
        scale = min(cw / max(1, iw), ch / max(1, ih))
        nw = max(1, int(iw * scale))
        nh = max(1, int(ih * scale))
        disp = img.resize((nw, nh), Image.NEAREST).convert("RGBA")
        ti   = ImageTk.PhotoImage(disp)
        self._tkimgs = [ti]
        self._cv.delete("all")
        self._cv.create_image(cw // 2, ch // 2, anchor="center", image=ti)

    def _update_info(self, det):
        center  = det.get("center")
        heading = det.get("heading")
        pc      = det.get("pivot_conf",   0.0)
        hc      = det.get("heading_conf", 0.0)
        n_cl    = len((det.get("dir_result") or {}).get("clusters") or [])

        self._pivot_conf_var.set(
            "({:.1f}, {:.1f})  conf {:.2f}".format(center[0], center[1], pc)
            if center else "not detected")
        self._heading_conf_var.set(
            "{:.1f} deg  conf {:.2f}".format(heading, hc)
            if heading is not None else "not detected")
        self._cluster_count_var.set(str(n_cl))

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
