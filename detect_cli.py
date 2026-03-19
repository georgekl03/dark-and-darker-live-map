"""detect_cli.py – Headless CLI entrypoint for Dark & Darker map detection."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw

from detection import (
    BboxConfig,
    MicrogridConfig,
    detect_microgrid,
    find_map_bbox,
    preprocess_for_detection,
    search_microgrid_first,
)
from detection.bbox import load_preset, save_preset

_PRESET_DIR = Path("data/debug/presets")
_PRESETS_FILE = _PRESET_DIR / "presets.json"
_DEFAULT_OUTPUT_DIR = Path("data/debug/cli_output")


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="detect_cli.py",
        description="Headless detection CLI for Dark & Darker live map.",
    )
    p.add_argument("image_path", help="Path to input image (PNG or JPG)")
    p.add_argument(
        "--mode",
        choices=["edge_contour", "dark_box", "microgrid_first"],
        default="edge_contour",
        help="Detection mode (default: edge_contour)",
    )
    p.add_argument(
        "--output-dir",
        default=str(_DEFAULT_OUTPUT_DIR),
        metavar="DIR",
        help="Output directory (default: data/debug/cli_output)",
    )
    # BBox params
    p.add_argument("--dark-thresh", type=int, default=65)
    p.add_argument("--canny-low", type=int, default=30)
    p.add_argument("--canny-high", type=int, default=100)
    p.add_argument("--min-border-brightness", type=int, default=80)
    # Preprocessing params
    p.add_argument("--gamma", type=float, default=1.0)
    p.add_argument("--clahe", action="store_true", help="Enable CLAHE contrast enhancement")
    p.add_argument("--autocontrast", action="store_true")
    p.add_argument("--unsharp", action="store_true")
    # Microgrid params
    p.add_argument("--force-micro-period", type=int, default=0)
    p.add_argument("--force-module-period", type=int, default=0)
    p.add_argument("--min-grid-size", type=int, default=2)
    p.add_argument("--max-grid-size", type=int, default=10)
    # Preset management
    p.add_argument(
        "--preset",
        metavar="NAME",
        help="Load settings from data/debug/presets/presets.json",
    )
    p.add_argument(
        "--save-preset",
        metavar="NAME",
        help="Save used settings as a preset after running",
    )
    # Output control
    p.add_argument("--no-images", action="store_true", help="Skip saving debug images")
    p.add_argument("--verbose", action="store_true", help="Print detailed log to stdout")
    return p


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _build_bbox_config(args: argparse.Namespace, preset: BboxConfig | None) -> BboxConfig:
    base = preset if preset is not None else BboxConfig()
    # CLI args override preset values only when they differ from their defaults
    defaults = _build_parser().parse_args(["_placeholder_"])
    overrides: dict = {}
    if args.dark_thresh != defaults.dark_thresh:
        overrides["dark_thresh"] = args.dark_thresh
    if args.canny_low != defaults.canny_low:
        overrides["canny_low"] = args.canny_low
    if args.canny_high != defaults.canny_high:
        overrides["canny_high"] = args.canny_high
    if args.min_border_brightness != defaults.min_border_brightness:
        overrides["min_border_brightness"] = args.min_border_brightness
    if args.mode == "edge_contour":
        overrides.setdefault("use_edge_contour", True)
    elif args.mode == "dark_box":
        overrides["use_edge_contour"] = False
    if overrides:
        import dataclasses
        base = dataclasses.replace(base, **overrides)
    return base


def _build_micro_config(args: argparse.Namespace, preset: BboxConfig | None) -> MicrogridConfig:
    # MicrogridConfig is not stored in presets (presets only hold BboxConfig),
    # so we always start from defaults and apply CLI overrides.
    cfg = MicrogridConfig()
    import dataclasses
    overrides: dict = {}
    defaults = _build_parser().parse_args(["_placeholder_"])
    if args.force_micro_period != defaults.force_micro_period:
        overrides["force_micro_period"] = args.force_micro_period
    if args.force_module_period != defaults.force_module_period:
        overrides["force_module_period"] = args.force_module_period
    if args.min_grid_size != defaults.min_grid_size:
        overrides["min_grid_size"] = args.min_grid_size
    if args.max_grid_size != defaults.max_grid_size:
        overrides["max_grid_size"] = args.max_grid_size
    if overrides:
        cfg = dataclasses.replace(cfg, **overrides)
    return cfg


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def _draw_bbox_overlay(image: Image.Image, bbox: tuple) -> Image.Image:
    overlay = image.convert("RGB").copy()
    draw = ImageDraw.Draw(overlay)
    x, y, w, h = bbox
    for offset in range(3):
        draw.rectangle(
            [x - offset, y - offset, x + w + offset, y + h + offset],
            outline=(0, 220, 0),
        )
    return overlay


def _draw_microgrid_overlay(crop: Image.Image, mgrid_result) -> Image.Image:
    overlay = crop.convert("RGB").copy()
    draw = ImageDraw.Draw(overlay)
    w, h = overlay.size
    sx = mgrid_result.micro_step_x
    sy = mgrid_result.micro_step_y
    ox = mgrid_result.offset_x
    oy = mgrid_result.offset_y
    if sx and sx > 0:
        x = ox % sx
        while x < w:
            draw.line([(x, 0), (x, h - 1)], fill=(0, 220, 220), width=1)
            x += sx
    if sy and sy > 0:
        y = oy % sy
        while y < h:
            draw.line([(0, y), (w - 1, y)], fill=(0, 220, 220), width=1)
            y += sy
    return overlay


def _edges_to_image(edges) -> Image.Image:
    """Convert edge array (any numeric) to a uint8 grayscale PIL image."""
    try:
        import numpy as np
        arr = np.array(edges, dtype=np.float32)
        mn, mx = arr.min(), arr.max()
        if mx > mn:
            arr = (arr - mn) / (mx - mn) * 255.0
        return Image.fromarray(arr.astype(np.uint8), mode="L")
    except Exception:
        return Image.new("L", (1, 1))


# ---------------------------------------------------------------------------
# Core run logic
# ---------------------------------------------------------------------------

def run_detection(args: argparse.Namespace) -> int:
    """Execute detection and save outputs.  Returns exit code (0=ok, 1=fail)."""
    t_start = time.perf_counter()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    report: dict = {
        "image": args.image_path,
        "mode": args.mode,
        "bbox": None,
        "bbox_method_used": None,
        "bbox_seed": None,
        "bbox_refined": None,
        "microgrid": None,
        "bbox_log": [],
        "microgrid_log": [],
        "timings": {"bbox": 0.0, "preprocess": 0.0, "microgrid": 0.0, "total": 0.0},
        "ok": False,
        "error": "",
    }

    # ------------------------------------------------------------------
    # Load image
    # ------------------------------------------------------------------
    try:
        image = Image.open(args.image_path)
    except Exception as exc:
        report["error"] = f"Cannot open image: {exc}"
        _save_report(report, output_dir)
        print(f"✗ Cannot open image: {exc}")
        print(f"✗ Report saved to: {output_dir / 'report.json'}")
        return 1

    # ------------------------------------------------------------------
    # Load preset (BboxConfig only)
    # ------------------------------------------------------------------
    preset: BboxConfig | None = None
    if args.preset:
        _PRESET_DIR.mkdir(parents=True, exist_ok=True)
        preset = load_preset(args.preset, _PRESETS_FILE)
        if preset is None and args.verbose:
            print(f"  [warn] Preset '{args.preset}' not found; using defaults.")

    bbox_config = _build_bbox_config(args, preset)
    micro_config = _build_micro_config(args, preset)

    # ------------------------------------------------------------------
    # BBox detection
    # ------------------------------------------------------------------
    t_bbox0 = time.perf_counter()
    bbox_result = None
    bbox_tuple: tuple | None = None

    if args.mode == "microgrid_first":
        try:
            bbox_tuple, _score = search_microgrid_first(image, micro_config)
        except Exception as exc:
            report["error"] = f"microgrid_first search failed: {exc}"
        if bbox_tuple is not None:
            report["bbox"] = list(bbox_tuple)
            report["bbox_method_used"] = "microgrid_first"
    else:
        try:
            bbox_result = find_map_bbox(image, bbox_config)
        except Exception as exc:
            report["error"] = f"bbox detection failed: {exc}"
            bbox_result = None

        if bbox_result is not None:
            report["bbox_log"] = bbox_result.log or []
            if bbox_result.ok and bbox_result.bbox:
                bbox_tuple = tuple(bbox_result.bbox)
                report["bbox"] = list(bbox_result.bbox)
                report["bbox_method_used"] = bbox_result.method_used
                report["bbox_seed"] = (
                    list(bbox_result.seed_bbox) if bbox_result.seed_bbox else None
                )
                report["bbox_refined"] = (
                    list(bbox_result.refined_bbox) if bbox_result.refined_bbox else None
                )
            else:
                if not report["error"]:
                    report["error"] = bbox_result.error or "BBox detection returned no result"

    report["timings"]["bbox"] = time.perf_counter() - t_bbox0

    if bbox_tuple is None:
        # Failure path
        _save_report(report, output_dir)
        err_msg = report["error"] or "BBox detection failed"
        print(f"✗ BBox detection failed: {err_msg}")
        print(f"✗ Report saved to: {output_dir / 'report.json'}")
        return 1

    # ------------------------------------------------------------------
    # Crop + preprocess
    # ------------------------------------------------------------------
    t_pre0 = time.perf_counter()
    x, y, w, h = (int(v) for v in bbox_tuple)
    map_crop = image.crop((x, y, x + w, y + h))

    pre_result = preprocess_for_detection(
        map_crop,
        gamma=args.gamma,
        clahe=args.clahe,
        autocontrast=args.autocontrast,
        unsharp=args.unsharp,
    )
    preprocessed_img: Image.Image = pre_result.get("enhanced") or pre_result.get("gray") or map_crop
    edges = pre_result.get("edges")
    report["timings"]["preprocess"] = time.perf_counter() - t_pre0

    # ------------------------------------------------------------------
    # Microgrid detection
    # ------------------------------------------------------------------
    t_micro0 = time.perf_counter()
    mgrid_result = None
    try:
        mgrid_result = detect_microgrid(preprocessed_img, micro_config)
    except Exception as exc:
        if args.verbose:
            print(f"  [warn] Microgrid detection error: {exc}")

    if mgrid_result is not None:
        report["microgrid_log"] = mgrid_result.log or []
        report["microgrid"] = {
            "module_step_x": mgrid_result.module_step_x,
            "module_step_y": mgrid_result.module_step_y,
            "micro_step_x": mgrid_result.micro_step_x,
            "micro_step_y": mgrid_result.micro_step_y,
            "offset_x": mgrid_result.offset_x,
            "offset_y": mgrid_result.offset_y,
            "periodicity_score_x": mgrid_result.periodicity_score_x,
            "periodicity_score_y": mgrid_result.periodicity_score_y,
        }
    report["timings"]["microgrid"] = time.perf_counter() - t_micro0

    report["ok"] = True
    report["timings"]["total"] = time.perf_counter() - t_start

    # ------------------------------------------------------------------
    # Verbose log
    # ------------------------------------------------------------------
    if args.verbose:
        for line in report["bbox_log"]:
            print(f"  [bbox] {line}")
        for line in report["microgrid_log"]:
            print(f"  [grid] {line}")

    # ------------------------------------------------------------------
    # Save report
    # ------------------------------------------------------------------
    _save_report(report, output_dir)

    # ------------------------------------------------------------------
    # Save debug images
    # ------------------------------------------------------------------
    if not args.no_images:
        image.convert("RGB").save(output_dir / "01_original.png")
        preprocessed_img.convert("RGB").save(output_dir / "02_preprocessed.png")
        if edges is not None:
            _edges_to_image(edges).save(output_dir / "03_edges.png")
        _draw_bbox_overlay(image, (x, y, w, h)).save(output_dir / "04_bbox_overlay.png")
        map_crop.convert("RGB").save(output_dir / "05_map_crop.png")
        if mgrid_result is not None:
            _draw_microgrid_overlay(map_crop, mgrid_result).save(
                output_dir / "06_microgrid_overlay.png"
            )

    # ------------------------------------------------------------------
    # Save preset if requested
    # ------------------------------------------------------------------
    if args.save_preset:
        _PRESET_DIR.mkdir(parents=True, exist_ok=True)
        save_preset(bbox_config, args.save_preset, _PRESETS_FILE)
        if args.verbose:
            print(f"  [preset] Saved preset '{args.save_preset}' to {_PRESETS_FILE}")

    # ------------------------------------------------------------------
    # Summary output
    # ------------------------------------------------------------------
    print(f"✓ BBox: (x={x}, y={y}, w={w}, h={h})  method={report['bbox_method_used']}")
    if mgrid_result is not None:
        mx = mgrid_result.module_step_x
        my = mgrid_result.module_step_y
        ux = mgrid_result.micro_step_x
        uy = mgrid_result.micro_step_y
        sx = mgrid_result.periodicity_score_x
        sy = mgrid_result.periodicity_score_y
        print(f"✓ Micro-grid: module_step={mx}×{my}  micro_step={ux}×{uy}  score=({sx:.3f}, {sy:.3f})")
    print(f"✓ Report saved to: {output_dir / 'report.json'}")
    if not args.no_images:
        print(f"✓ Debug images saved to: {output_dir}/")
    return 0


def _save_report(report: dict, output_dir: Path) -> None:
    try:
        with open(output_dir / "report.json", "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
    except Exception as exc:
        print(f"  [warn] Could not save report.json: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return run_detection(args)


if __name__ == "__main__":
    sys.exit(main())
