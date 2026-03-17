#!/usr/bin/env python3
"""
map_scanner.py  –  Standalone Dark & Darker Map Scanner & Debug Tool
=====================================================================
Run directly:
    python map_scanner.py

This tool can:
  * Load a screenshot or any image file to analyse.
  * Take a live screenshot of your primary monitor.
  * Detect the dungeon-map overlay region on screen regardless of game
    resolution or UI scale.
  * Split the detected region into a grid of modules.
  * Template-match each cell against all known module PNGs (with all four
    90° rotations).
  * Show step-by-step debug overlays so you can see exactly how each stage
    works.

Dependencies: Pillow  (numpy optional, but improves speed)
"""

from __future__ import annotations

import json
import math
import re
import threading
import tkinter as tk
from tkinter import filedialog, ttk
from pathlib import Path
from typing import Optional

try:
    from PIL import Image, ImageDraw, ImageTk, ImageFilter
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

# ── Paths (match map_viewer.py) ──────────────────────────
ROOT    = Path(__file__).parent
DATA    = ROOT / "data"
RAW     = DATA / "raw"
MODULES = DATA / "modules"
MANIF   = DATA / "map_manifest.json"

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

PNG_SIG = b"\x89PNG\r\n\x1a\n"

def _is_valid_png(p: Path) -> bool:
    try:
        with open(p, "rb") as f:
            return f.read(8) == PNG_SIG
    except Exception:
        return False


def flat_btn(parent, text, cmd, fg=TEXT, bg=BTN_BG, font=("Segoe UI", 9),
             padx=10, pady=4, width=None):
    kw = dict(text=text, command=cmd, bg=bg, fg=fg, bd=0, font=font,
              padx=padx, pady=pady, activebackground=BTN_H,
              activeforeground=TEXT, relief="flat", cursor="hand2")
    if width:
        kw["width"] = width
    return tk.Button(parent, **kw)


# ── Map scanner logic ─────────────────────────────────────
_MAP_DARK_THRESHOLD = 50
_MATCH_THRESHOLD    = 0.35


def grab_screen() -> Optional[Image.Image]:
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


def find_map_area(screen: Image.Image) -> Optional[tuple[int, int, int, int]]:
    """Locate the dungeon-map rectangle (x, y, w, h) in a screenshot.

    Strategy: scan from large to small, looking for a square region in the
    centre of the screen with a predominantly dark average brightness.
    This works regardless of game resolution or UI scale because the dungeon
    map is always rendered as a dark area.
    """
    sw, sh = screen.size
    # Restrict search to the inner 90 % to skip edge chrome / taskbar
    x0, y0 = int(sw * 0.05), int(sh * 0.05)
    x1, y1 = int(sw * 0.95), int(sh * 0.95)
    region = screen.crop((x0, y0, x1, y1)).convert("L")
    rw, rh = region.size

    for size_frac in (0.80, 0.70, 0.60, 0.50, 0.40, 0.30):
        side = int(min(rw, rh) * size_frac)
        cx   = rw // 2 - side // 2
        cy   = rh // 2 - side // 2
        patch = region.crop((cx, cy, cx + side, cy + side))
        if _HAVE_NUMPY:
            avg = float(np.asarray(patch, dtype=np.float32).mean())
        else:
            data = patch.getdata()
            avg  = sum(data) / len(data)
        if avg < _MAP_DARK_THRESHOLD:
            return (x0 + cx, y0 + cy, side, side)
    return None


def load_templates(map_name: str, module_keys: list[str],
                   patch_size: int) -> dict[str, list[Image.Image]]:
    """Load module PNGs as (mod_key → [rot0, rot90, rot180, rot270])."""
    templates: dict = {}
    mod_dir = MODULES / map_name
    if not mod_dir.is_dir():
        return templates
    for mk in module_keys:
        p = mod_dir / f"{mk}.png"
        if not (p.exists() and _is_valid_png(p)):
            continue
        try:
            base = Image.open(p).convert("L").resize(
                (patch_size, patch_size), Image.LANCZOS)
            templates[mk] = [
                base,
                base.rotate(90,  expand=True).resize((patch_size, patch_size), Image.LANCZOS),
                base.rotate(180, expand=True).resize((patch_size, patch_size), Image.LANCZOS),
                base.rotate(270, expand=True).resize((patch_size, patch_size), Image.LANCZOS),
            ]
        except Exception:
            pass
    return templates


def nmse(a: Image.Image, b: Image.Image) -> float:
    """Normalised mean-squared error [0..1] between two greyscale images."""
    if _HAVE_NUMPY:
        aa = np.asarray(a, dtype=np.float32) / 255.0
        bb = np.asarray(b, dtype=np.float32) / 255.0
        return float(np.mean((aa - bb) ** 2))
    da = list(a.getdata())
    db = list(b.getdata())
    n  = len(da)
    if n == 0:
        return 1.0
    return sum((x - y) ** 2 for x, y in zip(da, db)) / (n * 255.0 ** 2)


def match_modules(map_img: Image.Image,
                  modules: dict,
                  templates: dict,
                  patch_px: int) -> dict[str, tuple[int, int, int, float]]:
    """Return {mk: (row, col, best_rot_deg, best_err)} for each matched module."""
    result = {}
    for mk, mod in modules.items():
        if mk not in templates:
            continue
        row, col, span = mod["row"], mod["col"], mod["span"]
        px0 = col * patch_px
        py0 = row * patch_px
        px1 = px0 + span * patch_px
        py1 = py0 + span * patch_px
        if px1 > map_img.width or py1 > map_img.height:
            continue
        patch = map_img.crop((px0, py0, px1, py1)).resize(
            (patch_px, patch_px), Image.LANCZOS)
        best_err, best_rot = float("inf"), 0
        for rot_idx, tmpl in enumerate(templates[mk]):
            e = nmse(patch, tmpl)
            if e < best_err:
                best_err, best_rot = e, rot_idx * 90
        result[mk] = (row, col, best_rot, best_err)
    return result


# ── Debug overlay renderer ────────────────────────────────
def render_step_overlay(step: str,
                        image: Image.Image,
                        area: Optional[tuple[int, int, int, int]] = None,
                        modules: Optional[dict] = None,
                        matches: Optional[dict] = None,
                        patch_px: int = 64) -> Image.Image:
    """Return an RGBA image with a debug overlay for the given analysis step."""
    out = image.convert("RGBA").copy()
    draw = ImageDraw.Draw(out)

    if step == "area" and area is not None:
        ax, ay, aw, ah = area
        draw.rectangle([ax, ay, ax + aw, ay + ah],
                       outline=(100, 220, 100, 220), width=4)
        draw.text((ax + 4, ay + 4), "Detected map area", fill=(100, 220, 100, 220))

    elif step == "grid" and area is not None and modules is not None:
        ax, ay, aw, ah = area
        # Draw the grid over the detected map area
        max_col = max(m["col"] + m["span"] for m in modules.values())
        max_row = max(m["row"] + m["span"] for m in modules.values())
        cell_w  = aw / max(max_col, 1)
        cell_h  = ah / max(max_row, 1)
        for col in range(max_col + 1):
            x = int(ax + col * cell_w)
            draw.line([(x, ay), (x, ay + ah)], fill=(200, 180, 80, 160), width=1)
        for row in range(max_row + 1):
            y = int(ay + row * cell_h)
            draw.line([(ax, y), (ax + aw, y)], fill=(200, 180, 80, 160), width=1)
        draw.rectangle([ax, ay, ax + aw, ay + ah],
                       outline=(200, 180, 80, 200), width=2)

    elif step == "matches" and area is not None and modules is not None and matches is not None:
        ax, ay, aw, ah = area
        max_col = max(m["col"] + m["span"] for m in modules.values())
        max_row = max(m["row"] + m["span"] for m in modules.values())
        cell_w  = aw / max(max_col, 1)
        cell_h  = ah / max(max_row, 1)
        for mk, (row, col, rot, err) in matches.items():
            cx = int(ax + col * cell_w)
            cy = int(ay + row * cell_h)
            cw = int(cell_w)
            ch = int(cell_h)
            color = (80, 220, 80, 160) if err < _MATCH_THRESHOLD else (220, 80, 80, 160)
            draw.rectangle([cx + 1, cy + 1, cx + cw - 1, cy + ch - 1],
                           outline=color, width=2)
            lbl = f"{mk[:10]}\n{err:.3f} {rot}°"
            draw.text((cx + 3, cy + 3), lbl, fill=color)

    del draw
    return out


# ── Main App ──────────────────────────────────────────────
class ScannerApp(tk.Tk):
    """Debug GUI for the map scanner."""

    def __init__(self):
        super().__init__()
        self.title("D&D Map Scanner  –  Debug Tool")
        self.configure(bg=BG)
        self.geometry("1300x800")
        self.minsize(900, 600)

        self._img_orig:   Optional[Image.Image] = None   # loaded / grabbed image
        self._map_name:   str = ""
        self._manifest:   dict = {}
        self._modules:    dict = {}
        self._area:       Optional[tuple] = None
        self._matches:    Optional[dict] = None
        self._step_imgs:  dict[str, Image.Image] = {}
        self._tkimgs:     list = []
        self._step_var    = tk.StringVar(value="original")

        self._load_manifest()
        self._build()

    # ── Manifest / module loading ─────────────────────────
    def _load_manifest(self):
        if MANIF.exists():
            with open(MANIF) as f:
                self._manifest = json.load(f)
        # Pick the first map that has local data as default
        for name in self._manifest:
            if (RAW / f"{name}.json").exists():
                self._map_name = name
                self._load_map_modules(name)
                break

    def _load_map_modules(self, map_name: str):
        jf = RAW / f"{map_name}.json"
        if not jf.exists():
            self._modules = {}
            return
        with open(jf) as f:
            raw = json.load(f)
        modules = {}
        layout_key = "N_Layout"
        for mk, mval in raw.items():
            if not isinstance(mval, dict):
                continue
            layout = mval.get(layout_key) or mval.get("HR_Layout") or {}
            if not layout:
                continue
            modules[mk] = {
                "row":  int(layout.get("row", 0)),
                "col":  int(layout.get("col", 0)),
                "span": int(layout.get("span", 1)),
            }
        self._modules = modules

    # ── UI ────────────────────────────────────────────────
    def _build(self):
        # ── Toolbar ──────────────────────────────────────
        tb = tk.Frame(self, bg=PANEL2, pady=6)
        tb.pack(fill="x")

        flat_btn(tb, "📂  Load Image",  self._load_image).pack(side="left", padx=6)
        flat_btn(tb, "📷  Screenshot",  self._do_screenshot).pack(side="left", padx=2)
        tk.Frame(tb, bg=BDR2, width=1).pack(side="left", fill="y", padx=8)
        flat_btn(tb, "🔍  Detect Map Area",  self._do_detect, fg=TEXT, bg="#2e3020").pack(side="left", padx=2)
        flat_btn(tb, "⬛  Show Grid",        self._do_grid,   fg=TEXT, bg="#2e3020").pack(side="left", padx=2)
        flat_btn(tb, "✅  Match Modules",    self._do_match,  fg="#111", bg=ACCENT).pack(side="left", padx=2)
        tk.Frame(tb, bg=BDR2, width=1).pack(side="left", fill="y", padx=8)

        # Map selector
        tk.Label(tb, text="Map:", bg=PANEL2, fg=DIM,
                 font=("Segoe UI", 9)).pack(side="left", padx=(4, 2))
        self._map_var = tk.StringVar(value=self._map_name)
        map_names = [n for n in self._manifest
                     if (RAW / f"{n}.json").exists()]
        cb = ttk.Combobox(tb, textvariable=self._map_var,
                          values=map_names, state="readonly",
                          font=("Segoe UI", 9), width=24)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", self._on_map_change)

        # Step selector
        tk.Frame(tb, bg=BDR2, width=1).pack(side="left", fill="y", padx=8)
        tk.Label(tb, text="View:", bg=PANEL2, fg=DIM,
                 font=("Segoe UI", 9)).pack(side="left", padx=(4, 2))
        self._steps = [
            ("original", "Original"),
            ("area",     "Map Area"),
            ("grid",     "Grid"),
            ("matches",  "Matches"),
        ]
        step_cb = ttk.Combobox(tb, textvariable=self._step_var,
                               values=[s[0] for s in self._steps],
                               state="readonly", font=("Segoe UI", 9), width=12)
        step_cb.pack(side="left")
        step_cb.bind("<<ComboboxSelected>>", lambda _: self._show_current_step())

        # Status
        self._status = tk.StringVar(value="Load an image or take a screenshot to begin.")
        tk.Label(tb, textvariable=self._status, bg=PANEL2, fg=DIM,
                 font=("Segoe UI", 9), padx=12, anchor="w",
                 wraplength=400).pack(side="left", fill="x", expand=True)

        # ── Main area: image canvas + log panel ──────────
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")
        main = tk.PanedWindow(self, orient="horizontal", bg=BG,
                              sashwidth=4, sashrelief="flat")
        main.pack(fill="both", expand=True)

        # Canvas
        cv_frame = tk.Frame(main, bg=BG)
        main.add(cv_frame, minsize=600)
        self._cv = tk.Canvas(cv_frame, bg=BG, bd=0, highlightthickness=0)
        self._cv.pack(fill="both", expand=True)
        self._cv.bind("<Configure>", lambda _: self._show_current_step())

        # Log panel
        log_frame = tk.Frame(main, bg=PANEL, width=320)
        log_frame.pack_propagate(False)
        main.add(log_frame, minsize=200)

        tk.Label(log_frame, text="Analysis Log", bg=PANEL, fg=ACCENT,
                 font=("Segoe UI", 10, "bold"), padx=10, pady=8).pack(anchor="w")
        tk.Frame(log_frame, bg=BORDER, height=1).pack(fill="x")

        self._log = tk.Text(log_frame, bg=PANEL2, fg=TEXT, bd=0,
                            font=("Consolas", 9), wrap="word", state="disabled",
                            highlightthickness=0)
        self._log.pack(fill="both", expand=True, padx=4, pady=4)

        sb = tk.Scrollbar(log_frame, command=self._log.yview)
        self._log["yscrollcommand"] = sb.set

        flat_btn(log_frame, "Clear Log", self._clear_log,
                 fg=DIM, bg=PANEL, padx=8, pady=3,
                 font=("Segoe UI", 8)).pack(anchor="e", padx=4, pady=4)

    # ── Actions ───────────────────────────────────────────
    def _on_map_change(self, _=None):
        name = self._map_var.get()
        if name and name != self._map_name:
            self._map_name = name
            self._load_map_modules(name)
            self._log_msg(f"Switched to map: {name}  ({len(self._modules)} modules)")

    def _load_image(self):
        if not _HAVE_PIL:
            self._status.set("Pillow not installed.")
            return
        path = filedialog.askopenfilename(
            title="Open image",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.tiff"), ("All", "*.*")])
        if not path:
            return
        try:
            self._img_orig = Image.open(path).convert("RGB")
            self._area    = None
            self._matches = None
            self._step_imgs.clear()
            self._step_imgs["original"] = self._img_orig.copy()
            self._step_var.set("original")
            self._show_current_step()
            self._log_msg(f"Loaded: {Path(path).name}  {self._img_orig.size}")
            self._status.set(f"Image loaded: {Path(path).name}")
        except Exception as e:
            self._log_msg(f"Error loading image: {e}")

    def _do_screenshot(self):
        if not (_HAVE_MSS or _HAVE_IMAGEGRAB):
            self._status.set("Install mss or Pillow[ImageGrab] for screenshots.")
            return
        self._status.set("Taking screenshot…")
        self.update_idletasks()
        img = grab_screen()
        if img is None:
            self._status.set("Screenshot failed.")
            return
        self._img_orig = img
        self._area    = None
        self._matches = None
        self._step_imgs.clear()
        self._step_imgs["original"] = img.copy()
        self._step_var.set("original")
        self._show_current_step()
        self._log_msg(f"Screenshot taken: {img.size}")
        self._status.set(f"Screenshot captured: {img.size[0]}×{img.size[1]}")

    def _do_detect(self):
        if self._img_orig is None:
            self._status.set("Load an image first.")
            return
        self._log_msg("Detecting map area…")
        area = find_map_area(self._img_orig)
        if area is None:
            self._status.set("[Detect] No dark map area found.")
            self._log_msg("  ✗  No map area detected.\n"
                          "  Tips:\n"
                          "  – Open your in-game map (M by default)\n"
                          "  – Make sure the map overlay is visible and not\n"
                          "    cropped by the edge of the screenshot\n"
                          "  – The detection threshold is "
                          f"{_MAP_DARK_THRESHOLD} (avg brightness ≤ this)")
            return
        self._area = area
        ax, ay, aw, ah = area
        self._log_msg(f"  ✓  Area found: x={ax} y={ay} w={aw} h={ah}")
        overlay = render_step_overlay("area", self._img_orig, area=area)
        self._step_imgs["area"] = overlay
        self._step_imgs["grid"] = overlay  # will be replaced when grid is drawn
        self._step_var.set("area")
        self._show_current_step()
        self._status.set(f"Map area detected at ({ax}, {ay}), size {aw}×{ah}")

    def _do_grid(self):
        if self._img_orig is None:
            self._status.set("Load an image first.")
            return
        if self._area is None:
            self._do_detect()
            if self._area is None:
                return
        if not self._modules:
            self._status.set("No module data – select a map that has been downloaded.")
            return
        self._log_msg("Overlaying module grid…")
        overlay = render_step_overlay("grid", self._img_orig,
                                      area=self._area, modules=self._modules)
        self._step_imgs["grid"] = overlay
        self._step_var.set("grid")
        self._show_current_step()
        max_col = max(m["col"] + m["span"] for m in self._modules.values())
        max_row = max(m["row"] + m["span"] for m in self._modules.values())
        self._log_msg(f"  Grid: {max_col}×{max_row}  ({len(self._modules)} modules)")
        self._status.set(f"Grid shown: {max_col} cols × {max_row} rows")

    def _do_match(self):
        if self._img_orig is None:
            self._status.set("Load an image first.")
            return
        if self._area is None:
            self._do_detect()
            if self._area is None:
                return
        if not self._modules:
            self._status.set("No module data.")
            return
        self._log_msg(f"Matching modules for {self._map_name}…")
        self._status.set("Matching modules (this may take a moment)…")
        self.update_idletasks()

        ax, ay, aw, ah = self._area
        map_img = self._img_orig.crop((ax, ay, ax + aw, ay + ah)).convert("L")

        max_col = max(m["col"] + m["span"] for m in self._modules.values())
        max_row = max(m["row"] + m["span"] for m in self._modules.values())
        patch_px = max(32, min(aw // max(max_col, 1), ah // max(max_row, 1)))

        templates = load_templates(self._map_name, list(self._modules.keys()), patch_px)
        if not templates:
            self._log_msg("  ✗  No module templates found.\n"
                          "  Run dad_downloader.py to download map PNG tiles first.")
            self._status.set("No templates – download map data first.")
            return

        matches = match_modules(map_img, self._modules, templates, patch_px)
        self._matches = matches

        accepted = {mk: v for mk, v in matches.items() if v[3] < _MATCH_THRESHOLD}
        rejected = {mk: v for mk, v in matches.items() if v[3] >= _MATCH_THRESHOLD}

        self._log_msg(f"  patch_px={patch_px}  templates={len(templates)}")
        self._log_msg(f"  Accepted ({len(accepted)}) error < {_MATCH_THRESHOLD}:")
        for mk, (r, c, rot, err) in sorted(accepted.items(), key=lambda x: x[1][3]):
            self._log_msg(f"    {mk:<30}  err={err:.4f}  rot={rot}°")
        if rejected:
            self._log_msg(f"  Rejected ({len(rejected)}) error ≥ {_MATCH_THRESHOLD}:")
            for mk, (r, c, rot, err) in sorted(rejected.items(), key=lambda x: x[1][3]):
                self._log_msg(f"    {mk:<30}  err={err:.4f}  rot={rot}°")

        overlay = render_step_overlay("matches", self._img_orig,
                                      area=self._area, modules=self._modules,
                                      matches=matches, patch_px=patch_px)
        self._step_imgs["matches"] = overlay
        self._step_var.set("matches")
        self._show_current_step()
        self._status.set(
            f"Matched: {len(accepted)} accepted, {len(rejected)} rejected "
            f"(threshold={_MATCH_THRESHOLD})")

    # ── Canvas rendering ──────────────────────────────────
    def _show_current_step(self):
        step = self._step_var.get()
        img  = self._step_imgs.get(step) or self._step_imgs.get("original")
        if img is None:
            self._cv.delete("all")
            self._cv.create_text(
                self._cv.winfo_width() // 2, self._cv.winfo_height() // 2,
                text="No image loaded.\nUse the toolbar to load an image\nor take a screenshot.",
                fill=DIM, font=("Segoe UI", 12), justify="center")
            return
        cw = max(1, self._cv.winfo_width())
        ch = max(1, self._cv.winfo_height())
        iw, ih = img.size
        scale  = min(cw / iw, ch / ih, 1.0)
        nw     = max(1, int(iw * scale))
        nh     = max(1, int(ih * scale))
        disp   = img.resize((nw, nh), Image.LANCZOS).convert("RGBA")
        ti     = ImageTk.PhotoImage(disp)
        self._tkimgs = [ti]
        self._cv.delete("all")
        self._cv.create_image(cw // 2, ch // 2, anchor="center", image=ti)

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


if __name__ == "__main__":
    if not _HAVE_PIL:
        print("ERROR: Pillow is required.  Run:  pip install Pillow")
        raise SystemExit(1)
    app = ScannerApp()
    app.mainloop()
