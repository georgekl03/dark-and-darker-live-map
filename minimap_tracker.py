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
from tkinter import filedialog, colorchooser
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


# -- Colour helpers -----------------------------------------------------------
def _rgba_to_hex(r, g, b, a=255):
    return "#{:02x}{:02x}{:02x}".format(int(r), int(g), int(b))


def _hex_to_rgb(hex_str):
    hex_str = hex_str.strip("#")
    if len(hex_str) == 6:
        return tuple(int(hex_str[i:i+2], 16) for i in (0, 2, 4))
    return (255, 255, 255)


def flat_btn(parent, text, cmd, fg=TEXT, bg=BTN_BG, font=("Segoe UI", 9),
             padx=10, pady=4):
    return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, bd=0,
                     font=font, padx=padx, pady=pady,
                     activebackground=BTN_H, activeforeground=TEXT,
                     relief="flat", cursor="hand2")


def _add_tooltip(widget, text):
    """Attach a simple hover tooltip to *widget*."""
    tip_win = [None]

    def _enter(event):
        if tip_win[0]:
            return
        x = widget.winfo_rootx() + 20
        y = widget.winfo_rooty() + widget.winfo_height() + 4
        tw = tk.Toplevel(widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry("+{}+{}".format(x, y))
        lbl = tk.Label(tw, text=text, justify="left",
                       background="#1e1e1e", foreground="#d0cdc8",
                       relief="flat", borderwidth=1,
                       font=("Segoe UI", 8),
                       wraplength=260, padx=6, pady=4)
        lbl.pack()
        tip_win[0] = tw

    def _leave(event):
        if tip_win[0]:
            tip_win[0].destroy()
            tip_win[0] = None

    widget.bind("<Enter>", _enter)
    widget.bind("<Leave>", _leave)


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
def _c(colors, key, fallback=(255, 255, 255, 200)):
    """Return an RGBA tuple from a colors dict or a fallback."""
    v = colors.get(key, fallback) if colors else fallback
    if isinstance(v, (list, tuple)) and len(v) == 4:
        return tuple(int(x) for x in v)
    return fallback


def render_overlay(img, det, overlays, view_mode="composite", colors=None):
    """Compose all debug overlays onto img and return an RGBA image.

    Parameters
    ----------
    img       : Raw minimap RGB image (PIL).
    det       : Detection result dict from run_detection().
    overlays  : Bool flags keyed by overlay name.
    view_mode : "composite"      – all overlays drawn on the raw minimap image.
                "green_mask"    – shows the HSV green-dot mask tinted green.
                "outline_mask"  – shows the dark-pixel cursor outline mask tinted red.
    colors    : Optional dict from settings["colors"] with per-element RGBA lists
                (each [R, G, B, A] where A=0 means fully transparent).
                Falls back to built-in defaults when not supplied.
    """
    if colors is None:
        colors = cd.DEFAULT_MINIMAP_SETTINGS.get("colors", {})

    green_tint_col   = _c(colors, "green_tint",   (0, 220, 80, 90))
    outline_tint_col = _c(colors, "outline_tint",  (220, 60, 60, 130))

    if view_mode == "green_mask":
        gm = det.get("green_mask_img")
        base = img.convert("RGBA")
        if gm is not None:
            alpha_img = Image.eval(gm, lambda v: min(200, v))
            tint = Image.new("RGBA", base.size, green_tint_col[:3] + (0,))
            base.paste(tint, mask=alpha_img)
        return base

    if view_mode == "outline_mask":
        om = det.get("outline_mask_img")
        mi = det.get("mask_info")
        base = img.convert("RGBA")
        if om is not None and mi is not None:
            ox, oy = mi["origin"]
            crop_rgba = Image.new("RGBA", om.size, outline_tint_col[:3] + (0,))
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
            a_cap = min(255, green_tint_col[3])
            alpha = Image.eval(gm, lambda v: min(a_cap, v // 3))
            tint  = Image.new("RGBA", out.size, green_tint_col[:3] + (0,))
            out.paste(tint, mask=alpha)

    # Outline mask tint in local crop region
    if overlays.get("show_outline_mask") and mask_info is not None:
        om = det.get("outline_mask_img")
        if om is not None:
            ox, oy = mask_info["origin"]
            a_cap     = min(255, outline_tint_col[3])
            alpha     = Image.eval(om, lambda v: min(a_cap, v // 2))
            tint_crop = Image.new("RGBA", om.size, outline_tint_col[:3] + (0,))
            patch     = Image.new("RGBA", om.size, (0, 0, 0, 0))
            patch.paste(tint_crop, mask=alpha)
            out.paste(patch, (ox, oy), patch)

    # Pivot crosshair
    if overlays.get("show_pivot") and center is not None:
        cx, cy = center
        r   = 5
        arm = r * 2
        draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                     outline=_c(colors, "pivot_outline", (0, 255, 100, 230)), width=2)
        draw.line([(cx - arm, cy), (cx + arm, cy)],
                  fill=_c(colors, "pivot_crosshair", (0, 255, 100, 160)), width=1)
        draw.line([(cx, cy - arm), (cx, cy + arm)],
                  fill=_c(colors, "pivot_crosshair", (0, 255, 100, 160)), width=1)

    # Sampling circles (R1=yellow, R2=blue)
    if overlays.get("show_circles") and center is not None and dir_result is not None:
        cx, cy = center
        r1 = dir_result.get("r1", 0)
        r2 = dir_result.get("r2", 0)
        if r1 > 0:
            draw.ellipse([cx - r1, cy - r1, cx + r1, cy + r1],
                         outline=_c(colors, "r1_circle", (255, 200, 50, 110)), width=1)
        if r2 > 0:
            draw.ellipse([cx - r2, cy - r2, cx + r2, cy + r2],
                         outline=_c(colors, "r2_circle", (80, 200, 255, 110)), width=1)

    # Cone visualisation (forward detection cone based on R2 tip hint)
    if overlays.get("show_cone") and center is not None and dir_result is not None:
        cone_dir = dir_result.get("cone_dir")
        c_half   = dir_result.get("cone_half_angle", 90.0)
        r1 = dir_result.get("r1", 0)
        r2 = dir_result.get("r2", 0)
        r_cone = max(r1, r2) + 8
        if cone_dir is not None and r_cone > 2:
            cx, cy = center
            cone_fill_col    = _c(colors, "cone_fill",    (255, 200, 50, 35))
            cone_outline_col = _c(colors, "cone_outline", (255, 200, 50, 180))
            # Draw as a filled pie-slice on a separate layer so alpha compositing works
            cone_layer = Image.new("RGBA", out.size, (0, 0, 0, 0))
            cd_draw    = ImageDraw.Draw(cone_layer)
            # PIL arc: 0 deg = east, clockwise; our convention: 0=north, clockwise
            # Conversion: pil_angle = 90 - our_angle
            start_ang = 90.0 - (cone_dir + c_half)
            end_ang   = 90.0 - (cone_dir - c_half)
            bbox_arc  = [cx - r_cone, cy - r_cone, cx + r_cone, cy + r_cone]
            cd_draw.pieslice(bbox_arc, start=start_ang, end=end_ang,
                             fill=cone_fill_col, outline=cone_outline_col)
            del cd_draw
            out = Image.alpha_composite(out, cone_layer)
            draw = ImageDraw.Draw(out)

    # Circle hit points
    if overlays.get("show_hits") and center is not None and dir_result is not None:
        cx, cy = center
        for src, rad, col_key, col_fb in [
            (dir_result.get("r1_samples", []), dir_result.get("r1", 0),
             "r1_hits", (255, 140, 0, 210)),
            (dir_result.get("r2_samples", []), dir_result.get("r2", 0),
             "r2_hits", (0, 200, 255, 210)),
        ]:
            col = _c(colors, col_key, col_fb)
            for angle_deg, hit in src:
                if hit:
                    ang_rad = math.radians(angle_deg)
                    hx = int(round(cx + rad * math.sin(ang_rad)))
                    hy = int(round(cy - rad * math.cos(ang_rad)))
                    draw.ellipse([hx - 2, hy - 2, hx + 2, hy + 2], fill=col)

    # Cluster midpoint lines + raw bisector
    if overlays.get("show_bisector") and center is not None and dir_result is not None:
        cx, cy = center
        r_vis  = dir_result.get("r2", 20) + 10
        cl_col = _c(colors, "cluster_lines", (160, 80, 220, 180))
        for cl in (dir_result.get("clusters") or [])[:2]:
            mid_rad = math.radians(cl["mid"])
            mx = cx + r_vis * math.sin(mid_rad)
            my = cy - r_vis * math.cos(mid_rad)
            draw.line([(cx, cy), (mx, my)], fill=cl_col, width=1)
            draw.ellipse([mx - 3, my - 3, mx + 3, my + 3], fill=cl_col)
        bisector = dir_result.get("bisector")
        if bisector is not None:
            bis_col = _c(colors, "bisector", (200, 100, 255, 160))
            bis_rad = math.radians(bisector)
            blen    = r_vis + 12
            bx = cx + blen * math.sin(bis_rad)
            by = cy - blen * math.cos(bis_rad)
            draw.line([(cx, cy), (bx, by)], fill=bis_col, width=1)

    # Final heading arrow
    if overlays.get("show_heading") and center is not None and heading is not None:
        cx, cy  = center
        arr_col = _c(colors, "heading_arrow", (255, 70, 70, 240))
        tip_len = (dir_result.get("r2", 20) if dir_result else 20) * 1.6
        ang_rad = math.radians(heading)
        tx = cx + tip_len * math.sin(ang_rad)
        ty = cy - tip_len * math.cos(ang_rad)
        draw.line([(cx, cy), (tx, ty)], fill=arr_col, width=2)
        for da in (-30, 30):
            back_rad = math.radians(heading + 180.0 + da)
            head_len = tip_len * 0.3
            bx = tx + head_len * math.sin(back_rad)
            by = ty - head_len * math.cos(back_rad)
            draw.line([(tx, ty), (bx, by)], fill=arr_col, width=2)

    # Tip (ring) and tracking point (dot)
    if overlays.get("show_tip") and tip_result is not None:
        tip = tip_result.get("tip")
        trk = tip_result.get("track_pt")
        if tip:
            draw.ellipse([tip[0] - 3, tip[1] - 3, tip[0] + 3, tip[1] + 3],
                         outline=_c(colors, "tip_outline", (255, 40, 200, 210)), width=2)
        if trk:
            draw.ellipse([trk[0] - 2, trk[1] - 2, trk[0] + 2, trk[1] + 2],
                         fill=_c(colors, "track_pt", (255, 255, 255, 200)))

    # Cursor bounding box
    if overlays.get("show_bbox") and dir_result is not None:
        bbox = dir_result.get("bbox")
        if bbox is not None:
            draw.rectangle(list(bbox),
                           outline=_c(colors, "bbox", (200, 200, 255, 140)), width=1)

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
        r2_hint = (dir_result or {}).get("r2_hint")
        if r2_hint is not None:
            lines.append("R2 hint {:.1f} deg".format(r2_hint))
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
        self._col_vars = self._make_col_vars()
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
            "r1":              tk.DoubleVar(value=d["r1"]),
            "r2":              tk.DoubleVar(value=d["r2"]),
            "samples":         tk.IntVar(value=d["samples"]),
            "cluster_gap":     tk.DoubleVar(value=d["cluster_gap"]),
            "min_hits":        tk.IntVar(value=d["min_hits"]),
            "cone_enabled":    tk.BooleanVar(value=d.get("cone_enabled", False)),
            "cone_half_angle": tk.DoubleVar(value=d.get("cone_half_angle", 90.0)),
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
        defaults = cd.DEFAULT_MINIMAP_SETTINGS["overlays"]
        saved    = self._settings.get("overlays", {})
        merged   = {k: saved.get(k, v) for k, v in defaults.items()}
        return {k: tk.BooleanVar(value=v) for k, v in merged.items()}

    def _make_col_vars(self):
        """Create per-channel IntVars for every colour entry in settings."""
        defaults = cd.DEFAULT_MINIMAP_SETTINGS.get("colors", {})
        saved    = self._settings.get("colors", {})
        result   = {}
        for key, def_rgba in defaults.items():
            rgba = saved.get(key, def_rgba)
            result[key] = {
                "r": tk.IntVar(value=int(rgba[0])),
                "g": tk.IntVar(value=int(rgba[1])),
                "b": tk.IntVar(value=int(rgba[2])),
                "a": tk.IntVar(value=int(rgba[3])),
                "_hex": tk.StringVar(value=_rgba_to_hex(*rgba)),
            }
        return result

    # -- Trace changes -> debounced re-analyse --------------------------------
    def _trace_all_vars(self):
        col_flat = []
        for ch_dict in self._col_vars.values():
            col_flat.extend([ch_dict["r"], ch_dict["g"],
                             ch_dict["b"], ch_dict["a"]])
        all_vars = (list(self._gd_vars.values())  +
                    list(self._out_vars.values()) +
                    list(self._dir_vars.values()) +
                    list(self._tip_vars.values()) +
                    list(self._sm_vars.values())  +
                    list(self._ov_vars.values())  +
                    col_flat +
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
        for val, label, tip in [
            ("composite",    "Composite",
             "All debug overlays drawn on the raw minimap image"),
            ("green_mask",   "Green Mask",
             "Shows the HSV colour mask used to detect the green dot "
             "(tinted green).  Use this to tune H/S/V thresholds."),
            ("outline_mask", "Outline Mask",
             "Shows the dark-pixel cursor outline mask inside the local "
             "window (tinted red).  Use this to tune the dark threshold "
             "and local window size."),
        ]:
            rb = tk.Radiobutton(
                tb, text=label, variable=self._view_var, value=val,
                bg=PANEL2, fg=TEXT, selectcolor=BG,
                activebackground=PANEL2, activeforeground=ACCENT,
                font=("Segoe UI", 9),
            )
            rb.pack(side="left", padx=2)
            _add_tooltip(rb, tip)

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
                         font=("Segoe UI", 7), wraplength=200,
                         justify="left").pack(side="left", padx=2)
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
                         font=("Segoe UI", 7), wraplength=180,
                         justify="left").pack(side="left", padx=4)

        def color_row(lbl, col_key, hint=""):
            """Colour editor: swatch button + alpha spinbox."""
            row = tk.Frame(ctrl, bg=PANEL)
            row.pack(fill="x", pady=1)
            tk.Label(row, text=lbl, bg=PANEL, fg=TEXT, font=("Segoe UI", 9),
                     width=22, anchor="w").pack(side="left")
            ch = self._col_vars[col_key]
            swatch_var = ch["_hex"]

            swatch = tk.Button(
                row, textvariable=swatch_var, width=8,
                bg=swatch_var.get(), fg="#000",
                font=("Segoe UI", 8), bd=0, relief="flat", cursor="hand2")
            swatch.pack(side="left", padx=2)

            def pick(sv=swatch_var, cv=ch, sw=swatch):
                init = sv.get()
                result = colorchooser.askcolor(color=init, title="Pick colour")
                if result and result[1]:
                    hex_val = result[1]
                    r2, g2, b2 = _hex_to_rgb(hex_val)
                    cv["r"].set(r2); cv["g"].set(g2); cv["b"].set(b2)
                    sv.set(hex_val)
                    sw.config(bg=hex_val)
                    self._analyse_static()

            swatch.config(command=pick)

            tk.Label(row, text="A:", bg=PANEL, fg=DIM,
                     font=("Segoe UI", 8)).pack(side="left", padx=(4, 0))
            tk.Spinbox(row, from_=0, to=255, increment=5,
                       textvariable=ch["a"], width=4,
                       bg=PANEL2, fg=TEXT, bd=0,
                       buttonbackground=PANEL2,
                       font=("Segoe UI", 9)).pack(side="left", padx=1)
            if hint:
                tk.Label(row, text=hint, bg=PANEL, fg=DIM,
                         font=("Segoe UI", 7), wraplength=120,
                         justify="left").pack(side="left", padx=2)

        # Section: Capture Region
        sec("Capture Region",
            "Pixel coordinates of your minimap on screen. "
            "Set these so the captured region contains only the minimap. "
            "Use 'Grab Region' to preview the current crop.")
        spin("Left  (screen X)",  self._roi_vars["left"],   0, 7680, 1,
             "X pixel of the left edge of your minimap on screen")
        spin("Top   (screen Y)",  self._roi_vars["top"],    0, 4320, 1,
             "Y pixel of the top edge")
        spin("Width   (px)",      self._roi_vars["width"],  32, 800, 1,
             "Horizontal size of the capture area")
        spin("Height  (px)",      self._roi_vars["height"], 32, 800, 1,
             "Vertical size of the capture area")

        # Section: Green Dot Detection
        sec("Green Dot Detection",
            "Finds the bright green dot at the cursor centre using HSV "
            "colour filtering.  H is hue (0-179), S saturation, V brightness "
            "(both 0-255).  Use the Green Mask view to tune these.")
        spin("Hue min  (H_min)",        self._gd_vars["h_min"],  0, 179, 1,
             "Lower hue bound – pure green is ~60, yellow-green ~40")
        spin("Hue max  (H_max)",        self._gd_vars["h_max"],  0, 179, 1,
             "Upper hue bound – increase if the dot looks lime/yellow")
        spin("Saturation min",          self._gd_vars["s_min"],  0, 255, 5,
             "Minimum saturation – raise to reject grey / white map pixels")
        spin("Saturation max",          self._gd_vars["s_max"],  0, 255, 5,
             "Maximum saturation – keep at 255 unless over-saturated")
        spin("Value (brightness) min",  self._gd_vars["v_min"],  0, 255, 5,
             "Minimum brightness – lower to catch dim dot edges")
        spin("Value (brightness) max",  self._gd_vars["v_max"],  0, 255, 5,
             "Maximum brightness – keep at 255")
        spin("Morph kernel  (px)",      self._gd_vars["morph_kernel"], 0, 10, 1,
             "Morphological close size – joins fragmented blobs; raise if "
             "the dot is detected as several small pieces")
        spin("Min area  (px2)",         self._gd_vars["min_area"],  1, 500, 1,
             "Blobs smaller than this are noise – raise to filter specs")
        spin("Max area  (px2)",         self._gd_vars["max_area"],  4, 2000, 10,
             "Blobs larger than this are false positives – lower if a "
             "coloured region of the map is being detected")

        # Section: Cursor Outline Mask
        sec("Cursor Outline Mask",
            "Extracts the dark outline pixels of the cursor in a square "
            "window around the green dot.  These pixels feed the direction "
            "detection circles.  Use the Outline Mask view to tune these.  "
            "The outline must stand out from the map background.")
        spin("Dark threshold",     self._out_vars["dark_thresh"],  10, 200, 5,
             "Pixels with grey value BELOW this are treated as cursor "
             "outline.  Raise if the outline is thin / not fully captured; "
             "lower if map walls are being picked up.")
        spin("Local window  (px)", self._out_vars["local_radius"], 8, 80, 1,
             "Half-size (radius) of the analysis window around the pivot.  "
             "Must be larger than R2 + a few pixels.")
        spin("Morph kernel  (px)", self._out_vars["morph_kernel"], 0, 8, 1,
             "Morphological close passes – connects broken outline segments. "
             "Raise if the cursor outline appears dashed.")

        # Section: Direction Detection
        sec("Direction Detection",
            "Two sampling circles (R1 inner, R2 outer) probe the outline "
            "mask.  R1 should bisect the cursor sides; R2 should just touch "
            "the cursor tip.  R2 hits are used to determine which half of "
            "the bisector axis is forward (fixing the arrow direction).")
        spin("R1 - inner radius (px)", self._dir_vars["r1"],  2, 60, 0.5,
             "Inner circle radius.  Should cross the two side-lines of the "
             "cursor WITHOUT hitting the back arc.  Increase if R1 misses "
             "the sides; decrease if it clips the back arc too.")
        spin("R2 - outer radius (px)", self._dir_vars["r2"],  4, 80, 0.5,
             "Outer circle radius.  Should sit at the cursor tip so that it "
             "gets exactly one hit near the heading direction.  R2 hits are "
             "used to pick the correct arrow direction.")
        spin("Samples per circle",     self._dir_vars["samples"], 16, 360, 4,
             "Number of evenly-spaced probe points per circle.  "
             "Higher = finer angular resolution but slower.")
        spin("Cluster gap  (deg)",     self._dir_vars["cluster_gap"], 5, 90, 1,
             "Angular gap in degrees that separates two distinct hit "
             "clusters.  If the L and R cursor edges merge into one cluster, "
             "reduce this value.")
        spin("Min hits per cluster",   self._dir_vars["min_hits"], 1, 20, 1,
             "A cluster needs at least this many consecutive hit angles to "
             "be considered valid.  Raise to filter single-pixel noise.")
        chk("Enable cone filter", self._dir_vars["cone_enabled"],
            "When on, only R1 hits within ±cone angle of the R2 tip "
            "direction are used.  This removes hits from the cursor back "
            "arc when R1 is large enough to touch it.")
        spin("Cone half-angle (deg)",  self._dir_vars["cone_half_angle"],
             5, 180, 5,
             "Half-width of the forward cone.  90° = half-circle (front only); "
             "180° = full circle (no filtering).  Reduce to narrow the cone "
             "if R1 still picks up back-arc hits.")

        # Section: Tip Detection
        sec("Tip Detection",
            "Optional raycast from the pivot along the heading to find the "
            "cursor tip.  Useful as an extra stability check.")
        chk("Enable tip raycast", self._tip_vars["enabled"],
            "Toggles the tip raycast.  The tip is marked by a ring; the "
            "stable tracking point by a dot.")
        spin("Max raycast dist (px)",   self._tip_vars["raycast_max"], 4, 100, 1,
             "How far along the heading ray to search for cursor pixels.  "
             "Set to slightly larger than R2.")
        spin("Tracking pt dist (px)",   self._tip_vars["track_dist"], 0, 50, 0.5,
             "Fixed distance from the pivot along the heading used as a "
             "stable tracking coordinate.  0 = use pivot only.")

        # Section: Temporal Smoothing
        sec("Smoothing",
            "Exponential moving average (EMA) filter applied to heading "
            "and position over time to reduce per-frame jitter.")
        spin("Heading alpha",          self._sm_vars["heading_alpha"],
             0.05, 1.0, 0.05,
             "EMA weight for the newest heading sample.  "
             "1.0 = no smoothing (raw); 0.1 = heavy smoothing.")
        spin("Pivot alpha",            self._sm_vars["pivot_alpha"],
             0.05, 1.0, 0.05,
             "EMA weight for the newest pivot position.  "
             "Lower = smoother but laggier position.")
        spin("Max heading delta (deg)", self._sm_vars["max_heading_delta"],
             5.0, 180.0, 5.0,
             "Clamps the per-frame heading change.  Prevents sudden 180° "
             "flips when detection is briefly wrong.")

        # Section: Overlay options
        sec("Overlay Options",
            "Toggle individual debug layers shown in Composite view.  "
            "Turn off layers that clutter the small cursor image.  "
            "Green Mask / Outline Mask views are controlled by the toolbar.")
        for key, label, hint in [
            ("show_green_mask",   "Green blob overlay",
             "Tints detected green pixels – shows what the dot detector sees"),
            ("show_outline_mask", "Outline mask overlay",
             "Tints dark cursor-outline pixels red inside the local window"),
            ("show_pivot",        "Pivot crosshair",
             "Circle + cross at the green dot centroid"),
            ("show_circles",      "R1 / R2 sampling circles",
             "R1 = inner (yellow), R2 = outer (blue)"),
            ("show_cone",         "Forward cone",
             "Pie-slice showing the R2-based forward cone used to filter R1 hits"),
            ("show_hits",         "Circle hit points",
             "Dots where R1/R2 circles intersect the outline mask; "
             "orange = R1, cyan = R2"),
            ("show_bisector",     "Cluster lines and bisector",
             "Lines to the two largest cluster midpoints + the raw bisector axis"),
            ("show_heading",      "Heading arrow",
             "Red arrow pointing in the confirmed forward direction"),
            ("show_tip",          "Tip and tracking point",
             "Ring at the raycast cursor tip; dot at the stable tracking point"),
            ("show_bbox",         "Cursor bounding box",
             "Rectangle around all detected dark outline pixels"),
            ("show_debug_text",   "Debug text readout",
             "Pivot position, heading angle and confidence numbers"),
        ]:
            if key in self._ov_vars:
                chk(label, self._ov_vars[key], "  " + hint)

        # Section: Colours
        sec("Colours",
            "Click the swatch to pick a colour; adjust 'A' (alpha, 0-255) "
            "for transparency.  A=0 makes the element invisible without "
            "disabling it.  Colours take effect immediately.")
        color_row("Pivot outline",    "pivot_outline",
                  "Circle around the green dot centroid")
        color_row("Pivot crosshair",  "pivot_crosshair",
                  "Crosshair lines through the pivot")
        color_row("R1 circle",        "r1_circle",
                  "Inner sampling circle")
        color_row("R2 circle",        "r2_circle",
                  "Outer sampling circle (tip)")
        color_row("R1 hit points",    "r1_hits",
                  "Dots where R1 touches outline")
        color_row("R2 hit points",    "r2_hits",
                  "Dots where R2 touches outline")
        color_row("Cone fill",        "cone_fill",
                  "Fill tint of the forward cone")
        color_row("Cone outline",     "cone_outline",
                  "Edge of the forward cone")
        color_row("Cluster lines",    "cluster_lines",
                  "Lines to cluster midpoints")
        color_row("Bisector line",    "bisector",
                  "Raw bisector axis (before disambiguation)")
        color_row("Heading arrow",    "heading_arrow",
                  "Confirmed forward direction arrow")
        color_row("Tip ring",         "tip_outline",
                  "Ring at the cursor tip")
        color_row("Tracking point",   "track_pt",
                  "Stable tracking dot along heading")
        color_row("Bounding box",     "bbox",
                  "Rectangle of cursor outline pixels")
        color_row("Green mask tint",  "green_tint",
                  "Tint colour for the Green Mask view")
        color_row("Outline mask tint","outline_tint",
                  "Tint colour for the Outline Mask view")

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
        colors = {}
        for col_key, ch in self._col_vars.items():
            colors[col_key] = [ch["r"].get(), ch["g"].get(),
                               ch["b"].get(), ch["a"].get()]
        s["colors"] = colors
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
        saved_colors = s.get("colors", {})
        for col_key, ch in self._col_vars.items():
            rgba = saved_colors.get(col_key)
            if rgba and len(rgba) == 4:
                ch["r"].set(int(rgba[0])); ch["g"].set(int(rgba[1]))
                ch["b"].set(int(rgba[2])); ch["a"].set(int(rgba[3]))
                ch["_hex"].set(_rgba_to_hex(*rgba))


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
            colors=settings.get("colors"),
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
            colors=settings.get("colors"),
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
        disp = img.resize((nw, nh), Image.LANCZOS).convert("RGBA")
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
