#!/usr/bin/env python3
"""
=============================================================
  Dark and Darker – Map Data Tool
  Interactive menu — no command-line arguments needed.
=============================================================
"""

import os
import sys
import json
import time
import shutil
import hashlib
import threading
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── optional colour on Windows / terminals that support it ─
try:
    import ctypes
    ctypes.windll.kernel32.SetConsoleMode(ctypes.windll.kernel32.GetStdHandle(-11), 7)
except Exception:
    pass

try:
    import requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False

# ── optional SVG → PNG conversion ─────────────────────────
try:
    import cairosvg
    CAIROSVG_OK = True
except Exception:
    CAIROSVG_OK = False

try:
    from PIL import Image
    import io as _io
    PIL_OK = True
except ImportError:
    PIL_OK = False

# ─────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────
ROOT       = Path(__file__).parent
DATA       = ROOT / "data"
RAW        = DATA / "raw"           # original JSON files from server
MODULES    = DATA / "modules"       # per-map subfolder with module PNGs
ICONS      = DATA / "icons"         # loot / item icons
LOOT       = DATA / "loot"          # loot_data JSON files

# ─────────────────────────────────────────────────────────
# Server constants — discovered from Network tab
# ─────────────────────────────────────────────────────────
BASE_URL   = "https://darkanddarkertracker.com"
MANIFEST_URL = f"{BASE_URL}/ProcessedModules/map_manifest.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/122.0.0.0 Safari/537.36",
    "Referer": f"{BASE_URL}/maps",
    "Accept": "*/*",
}

# ─────────────────────────────────────────────────────────
# Console helpers
# ─────────────────────────────────────────────────────────
CLR = {
    "reset":  "\033[0m",
    "bold":   "\033[1m",
    "dim":    "\033[2m",
    "red":    "\033[91m",
    "green":  "\033[92m",
    "yellow": "\033[93m",
    "blue":   "\033[94m",
    "cyan":   "\033[96m",
    "white":  "\033[97m",
    "grey":   "\033[90m",
}

def c(text: str, colour: str) -> str:
    return f"{CLR.get(colour,'')}{text}{CLR['reset']}"

def banner():
    print()
    print(c("╔══════════════════════════════════════════════╗", "cyan"))
    print(c("║   ", "cyan") + c("Dark and Darker  –  Map Data Tool", "bold") + c("       ║", "cyan"))
    print(c("║   ", "cyan") + c("darkanddarkertracker.com downloader", "dim")  + c("    ║", "cyan"))
    print(c("╚══════════════════════════════════════════════╝", "cyan"))
    print()

def divider(title: str = ""):
    w = 50
    if title:
        pad = (w - len(title) - 2) // 2
        print(c("─" * pad + f" {title} " + "─" * pad, "grey"))
    else:
        print(c("─" * w, "grey"))

def ok(msg):   print(c("  ✓ ", "green")  + msg)
def err(msg):  print(c("  ✗ ", "red")    + msg)
def info(msg): print(c("  · ", "blue")   + msg)
def warn(msg): print(c("  ! ", "yellow") + msg)

def progress_bar(done: int, total: int, width: int = 40) -> str:
    pct = done / max(total, 1)
    filled = int(width * pct)
    bar = "█" * filled + "░" * (width - filled)
    return f"  [{c(bar, 'cyan')}] {done}/{total}"

def prompt(msg: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(c(f"\n  ▶ {msg}{suffix}: ", "white")).strip()
    return val if val else default

def choose(options: list[str], title: str = "Choose an option") -> int:
    """Show a numbered menu and return the 1-based index chosen."""
    print()
    divider(title)
    for i, opt in enumerate(options, 1):
        print(f"  {c(str(i), 'yellow')}  {opt}")
    divider()
    while True:
        raw = input(c("  Enter number: ", "white")).strip()
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(options):
                return n
        print(c("  Invalid choice, try again.", "red"))

def pause():
    input(c("\n  Press Enter to continue…", "grey"))

# ─────────────────────────────────────────────────────────
# HTTP helpers
# ─────────────────────────────────────────────────────────
def get(url: str, stream: bool = False, retries: int = 3, timeout: int = 20):
    """Fetch URL with retries. Returns requests.Response or None."""
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, stream=stream,
                              timeout=timeout, allow_redirects=True)
            if r.status_code == 200:
                return r
            warn(f"HTTP {r.status_code}  {url}")
            return None
        except requests.exceptions.ConnectionError as e:
            if attempt == 0:
                err(f"Connection failed: {e}")
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
    return None

def download_file(url: str, dest: Path, force: bool = False) -> bool:
    """Download url → dest. Returns True on success."""
    if dest.exists() and not force:
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    r = get(url, stream=True)
    if r is None:
        return False
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(chunk_size=65536):
            f.write(chunk)
    tmp.rename(dest)
    return True

# ─────────────────────────────────────────────────────────
# Manifest
# ─────────────────────────────────────────────────────────
def load_manifest() -> Optional[dict]:
    """Load the map manifest (local cache first, then server)."""
    local = DATA / "map_manifest.json"
    if local.exists():
        with open(local) as f:
            return json.load(f)
    return None

def fetch_manifest(force: bool = False) -> Optional[dict]:
    local = DATA / "map_manifest.json"
    if local.exists() and not force:
        with open(local) as f:
            return json.load(f)
    info("Fetching map manifest…")
    r = get(MANIFEST_URL)
    if r is None:
        err("Could not download map manifest.")
        return None
    data = r.json()
    local.parent.mkdir(parents=True, exist_ok=True)
    with open(local, "w") as f:
        json.dump(data, f, indent=2)
    ok(f"Manifest saved  ({len(data)} maps found)")
    return data

# ─────────────────────────────────────────────────────────
# Status / summary helpers
# ─────────────────────────────────────────────────────────
def count_files(folder: Path, ext: str = "*") -> int:
    if not folder.exists():
        return 0
    return len(list(folder.glob(f"**/*.{ext}" if ext != "*" else "**/*")))

def local_status(manifest: dict) -> None:
    """Print what has already been downloaded."""
    print()
    divider("Download Status")
    print(f"  {'Map':<20} {'JSON':>6}  {'PNGs':>6}")
    divider()
    for map_name in manifest:
        raw_path = RAW / f"{map_name}.json"
        png_dir  = MODULES / map_name
        json_ok  = c("✓", "green") if raw_path.exists() else c("✗", "red")
        png_cnt  = count_files(png_dir, "png")
        png_str  = c(str(png_cnt), "green") if png_cnt else c("0", "red")
        print(f"  {map_name:<20} {json_ok:>6}  {png_str:>6}")
    # Global assets
    loot_cnt = count_files(LOOT, "json")
    icon_cnt = count_files(ICONS, "png")
    divider()
    print(f"  {'Loot JSON files':<20} {c(str(loot_cnt), 'cyan'):>6}")
    print(f"  {'Loot icons':<20} {c(str(icon_cnt), 'cyan'):>6}")
    divider()

# ─────────────────────────────────────────────────────────
# PNG validation helper (used at download time and by purify)
# ─────────────────────────────────────────────────────────
PNG_SIG = b"\x89PNG\r\n\x1a\n"

def _is_valid_png(path: Path) -> bool:
    """Return True only if *path* starts with the 8-byte PNG signature."""
    try:
        with open(path, "rb") as f:
            return f.read(8) == PNG_SIG
    except Exception:
        return False

# ─────────────────────────────────────────────────────────
# Download: Map JSON
# ─────────────────────────────────────────────────────────
def download_map_json(map_name: str, map_info: dict, force: bool = False) -> bool:
    dest = RAW / f"{map_name}.json"
    if dest.exists() and not force:
        ok(f"{map_name}.json  (cached)")
        return True
    path = map_info.get("mapDataPath", f"/ProcessedModules/{map_name}/{map_name}.json")
    url  = f"{BASE_URL}{path}"
    # add cache-busting param matching what the browser does
    url += f"?_cb={int(time.time()*1000)}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = get(url)
    if r is None:
        err(f"Failed: {map_name}.json")
        return False
    with open(dest, "w", encoding="utf-8") as f:
        f.write(r.text)
    size_kb = dest.stat().st_size / 1024
    ok(f"{map_name}.json  ({size_kb:.0f} KB)")
    return True

# ─────────────────────────────────────────────────────────
# Download: Module PNGs
# ─────────────────────────────────────────────────────────
def get_module_png_url(map_name: str, module_key: str) -> str:
    return f"{BASE_URL}/ProcessedModules/{map_name}/{module_key}.png"

def download_module_pngs(map_name: str, module_keys: list[str],
                          force: bool = False, workers: int = 8) -> tuple[int, int]:
    """Download all module PNGs for a map. Returns (ok_count, total).

    After each download the file is verified to be a real PNG (8-byte magic).
    Files that are not valid PNGs (e.g. HTML error pages from the server) are
    deleted immediately so they do not pollute the cache.
    """
    out_dir = MODULES / map_name
    out_dir.mkdir(parents=True, exist_ok=True)

    tasks = []
    for key in module_keys:
        if key.endswith("_Arena"):
            continue
        dest = out_dir / f"{key}.png"
        url  = get_module_png_url(map_name, key)
        tasks.append((url, dest))

    done, total, failed = 0, len(tasks), 0

    def worker(args):
        url, dest = args
        # If already cached, validate it first
        if dest.exists() and not force:
            if _is_valid_png(dest):
                return True
            # Cached file is corrupt – delete and re-download
            try:
                dest.unlink()
            except Exception:
                pass
        # Download to a temp file, then validate before keeping
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".png.tmp")
        r = get(url, stream=True)
        if r is None:
            return False
        try:
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
        except Exception:
            try:
                tmp.unlink()
            except Exception:
                pass
            return False
        # Validate: only keep genuine PNG files
        if not _is_valid_png(tmp):
            try:
                tmp.unlink()
            except Exception:
                pass
            return False
        try:
            tmp.rename(dest)
        except Exception:
            try:
                tmp.unlink()
            except Exception:
                pass
            return False
        return True

    print(f"  Downloading {total} module PNGs for {c(map_name, 'cyan')}…")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(worker, t): t for t in tasks}
        for fut in as_completed(futures):
            if fut.result():
                done += 1
            else:
                failed += 1
            print(f"\r{progress_bar(done + failed, total)}", end="", flush=True)
    print()
    return done, total

# ─────────────────────────────────────────────────────────
# Download: Loot data JSON
# ─────────────────────────────────────────────────────────
LOOT_FILES = [
    "loot_data_2022.json",
]
LOOT_URL_TEMPLATE = f"{BASE_URL}/ProcessedModules/grades_data/{{filename}}"

def download_loot_data(force: bool = False) -> int:
    LOOT.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    for fname in LOOT_FILES:
        url  = LOOT_URL_TEMPLATE.format(filename=fname)
        url += f"?_cb={int(time.time()*1000)}"
        dest = LOOT / fname
        if dest.exists() and not force:
            ok(f"{fname}  (cached)")
            downloaded += 1
            continue
        r = get(url)
        if r is None:
            err(f"Failed: {fname}")
            continue
        with open(dest, "w", encoding="utf-8") as f:
            f.write(r.text)
        size_kb = dest.stat().st_size / 1024
        ok(f"{fname}  ({size_kb:.0f} KB)")
        downloaded += 1
    return downloaded

# ─────────────────────────────────────────────────────────
# Download: Loot / item icons
# ─────────────────────────────────────────────────────────
def collect_icon_ids_from_loot_json() -> set[str]:
    """Parse all downloaded loot JSON files and collect item IDs."""
    ids = set()
    for jf in LOOT.glob("*.json"):
        try:
            with open(jf) as f:
                data = json.load(f)
            # Loot data structure: dict of item_id → {loot entries}
            if isinstance(data, dict):
                ids.update(data.keys())
        except Exception:
            pass
    return ids

def collect_icon_ids_from_map_json(map_name: str) -> set[str]:
    """Collect all object_name IDs from a map's raw JSON for icon downloading."""
    raw = RAW / f"{map_name}.json"
    ids = set()
    if not raw.exists():
        return ids
    try:
        with open(raw) as f:
            data = json.load(f)
        for module_data in data.values():
            for tier_key in ("N_Data", "HR_Data"):
                for item in module_data.get(tier_key, []):
                    ids.add(item.get("object_name", ""))
    except Exception:
        pass
    ids.discard("")
    return ids

def icon_url(icon_id: str) -> list[str]:
    """Return candidate URLs for an icon. The site uses several patterns."""
    name = icon_id  # e.g. "Id_Props_GoldChest"
    return [
        f"{BASE_URL}/icons/{name}.png",
        f"{BASE_URL}/ProcessedModules/icons/{name}.png",
        f"{BASE_URL}/static/icons/{name}.png",
    ]

def download_icons(icon_ids: set[str], force: bool = False, workers: int = 10) -> tuple[int, int]:
    ICONS.mkdir(parents=True, exist_ok=True)
    done, failed = 0, 0
    total = len(icon_ids)

    def worker(icon_id: str) -> bool:
        for url in icon_url(icon_id):
            dest = ICONS / f"{icon_id}.png"
            if dest.exists() and not force:
                return True
            if download_file(url, dest, force=force):
                return True
        return False

    print(f"  Attempting {total} loot icons…")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(worker, iid): iid for iid in icon_ids}
        for fut in as_completed(futs):
            if fut.result():
                done += 1
            else:
                failed += 1
            print(f"\r{progress_bar(done + failed, total)}", end="", flush=True)
    print()
    return done, total

# ─────────────────────────────────────────────────────────
# Full download for one map
# ─────────────────────────────────────────────────────────
def download_single_map(map_name: str, map_info: dict,
                         skip_json: bool = False,
                         skip_pngs: bool = False,
                         force: bool = False):
    print()
    divider(f"  {map_name}")
    if not skip_json:
        download_map_json(map_name, map_info, force)
    if not skip_pngs:
        module_keys = map_info.get("moduleKeys", [])
        if module_keys:
            ok_n, total = download_module_pngs(map_name, module_keys, force)
            if ok_n < total:
                warn(f"{total - ok_n} PNGs failed (may not exist on server)")
        else:
            warn(f"No module keys listed for {map_name}")

# ─────────────────────────────────────────────────────────
# Summary of what the raw JSON contains
# ─────────────────────────────────────────────────────────
def inspect_map_json(map_name: str):
    raw = RAW / f"{map_name}.json"
    if not raw.exists():
        err(f"No raw JSON for {map_name}. Download it first.")
        return

    with open(raw) as f:
        data = json.load(f)

    from collections import Counter
    cats: Counter = Counter()
    modules = list(data.keys())

    for mod_val in data.values():
        if not isinstance(mod_val, dict):
            continue
        for item in mod_val.get("N_Data", []):
            cats[item.get("entity_category", "?")] += 1

    print()
    divider(f"Inspect: {map_name}")
    info(f"Modules found: {len(modules)}")
    print()
    print(f"  {'Category':<30} {'Count':>6}")
    divider()
    for cat, n in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"  {cat:<30} {c(str(n), 'cyan'):>6}")
    divider()
    info(f"Raw file: {raw}  ({raw.stat().st_size/1024:.0f} KB)")

# ─────────────────────────────────────────────────────────
# Clear cache
# ─────────────────────────────────────────────────────────
def clear_cache(what: str = "all"):
    targets = {
        "json":    [RAW],
        "pngs":    [MODULES],
        "icons":   [ICONS],
        "loot":    [LOOT],
        "all":     [RAW, MODULES, ICONS, LOOT],
    }
    folders = targets.get(what, [])
    for folder in folders:
        if folder.exists():
            shutil.rmtree(folder)
            ok(f"Cleared {folder}")
        else:
            info(f"{folder} did not exist")

# ─────────────────────────────────────────────────────────
# PNG purification
# ─────────────────────────────────────────────────────────
# (PNG_SIG and _is_valid_png are defined earlier, near the download helpers)

def purify_module_pngs(manifest: dict, redownload: bool = False, force_redownload: bool = False) -> dict:
    """
    Walk data/modules/**/*.png, check the PNG signature, and quarantine any
    file that is not a valid PNG (e.g. an HTML error page saved as .png).

    Quarantined files are renamed to *.png.bad.

    Returns a summary dict with counts.
    """
    results = {"scanned": 0, "ok": 0, "quarantined": 0, "errors": 0}

    all_pngs = list(MODULES.glob("**/*.png")) if MODULES.exists() else []
    results["scanned"] = len(all_pngs)

    quarantined_by_map: dict[str, list[str]] = {}

    for p in all_pngs:
        if not _is_valid_png(p):
            map_name = p.parent.name
            mod_key = p.stem
            try:
                bad = p.with_suffix(p.suffix + ".bad")
                if bad.exists():
                    bad.unlink()
                p.rename(bad)
                results["quarantined"] += 1
                quarantined_by_map.setdefault(map_name, []).append(mod_key)
                warn(f"Quarantined: {p.relative_to(ROOT)}")
            except Exception as ex:
                err(f"Could not quarantine {p}: {ex}")
                results["errors"] += 1
        else:
            results["ok"] += 1

    if redownload and quarantined_by_map:
        info("Re-downloading quarantined module PNGs…")
        for map_name, mod_keys in quarantined_by_map.items():
            map_info = manifest.get(map_name)
            if not map_info:
                warn(f"  {map_name} not found in manifest — skipping re-download.")
                continue
            map_folder = MODULES / map_name
            map_folder.mkdir(parents=True, exist_ok=True)
            redownloaded = 0
            for mod_key in mod_keys:
                url = get_module_png_url(map_name, mod_key)
                dest = map_folder / f"{mod_key}.png"
                if download_file(url, dest, force=force_redownload):
                    # Verify the freshly downloaded file
                    if _is_valid_png(dest):
                        redownloaded += 1
                    else:
                        # Still corrupt — quarantine again
                        try:
                            bad2 = dest.with_suffix(dest.suffix + ".bad")
                            if bad2.exists():
                                bad2.unlink()
                            dest.rename(bad2)
                        except Exception:
                            pass
            ok(f"  {map_name}: re-downloaded {redownloaded}/{len(mod_keys)} PNGs")

    return results


# ─────────────────────────────────────────────────────────
# Menus
# ─────────────────────────────────────────────────────────
def menu_download_maps(manifest: dict):
    map_names = list(manifest.keys())
    while True:
        opts = [f"{n}  {c('(JSON + PNGs)', 'dim')}" for n in map_names]
        opts += [
            c("Download ALL maps", "green"),
            c("Back", "grey"),
        ]
        choice = choose(opts, "Select Map to Download")
        if choice == len(map_names) + 2:
            return
        elif choice == len(map_names) + 1:
            # All maps
            force = prompt("Force re-download cached files? (y/n)", "n").lower() == "y"
            for name, info_data in manifest.items():
                download_single_map(name, info_data, force=force)
            pause()
        else:
            name = map_names[choice - 1]
            force = prompt("Force re-download cached files? (y/n)", "n").lower() == "y"
            download_single_map(name, manifest[name], force=force)
            pause()


def menu_loot_assets(manifest: dict):
    while True:
        opts = [
            "Download loot data JSON  (grades / drop rates)",
            "Download loot icons  (PNG per item ID)",
            "Download ALL loot assets",
            c("Back", "grey"),
        ]
        choice = choose(opts, "Loot & Icon Assets")
        if choice == 4:
            return

        force = prompt("Force re-download? (y/n)", "n").lower() == "y"

        if choice in (1, 3):
            divider("Loot JSON")
            n = download_loot_data(force)
            ok(f"{n} loot files downloaded")

        if choice in (2, 3):
            divider("Icons")
            # Collect IDs from everything we have
            ids = set()
            for jf in RAW.glob("*.json"):
                ids |= collect_icon_ids_from_map_json(jf.stem)
            ids |= collect_icon_ids_from_loot_json()
            if not ids:
                warn("No item IDs found yet — download map JSON first.")
            else:
                info(f"Found {len(ids)} unique item IDs")
                done, total = download_icons(ids, force=force)
                ok(f"{done}/{total} icons downloaded")

        pause()


def menu_inspect(manifest: dict):
    while True:
        map_names = list(manifest.keys())
        opts = [n for n in map_names] + [c("Back", "grey")]
        choice = choose(opts, "Inspect Map Data")
        if choice == len(map_names) + 1:
            return
        inspect_map_json(map_names[choice - 1])
        pause()


def menu_clear():
    opts = [
        "Clear all cached JSON files",
        "Clear all module PNG images",
        "Clear loot icons",
        "Clear loot JSON files",
        c("Clear EVERYTHING", "red"),
        c("Back", "grey"),
    ]
    choice = choose(opts, "Clear Cache")
    if choice == 1: clear_cache("json")
    elif choice == 2: clear_cache("pngs")
    elif choice == 3: clear_cache("icons")
    elif choice == 4: clear_cache("loot")
    elif choice == 5:
        confirm = prompt("Type YES to confirm clearing all data", "")
        if confirm == "YES":
            clear_cache("all")
        else:
            warn("Cancelled.")
    pause()


def menu_purify_pngs(manifest: dict):
    """Scan all downloaded module PNGs, quarantine corrupt ones, optionally re-download."""
    print()
    divider("Purify Module PNGs")
    if not MODULES.exists() or not any(MODULES.glob("**/*.png")):
        warn("No module PNGs found. Download map data first.")
        pause()
        return

    total = sum(1 for _ in MODULES.glob("**/*.png"))
    info(f"Found {total} PNG file(s) across all maps.")
    print()
    confirm = prompt("Scan and quarantine corrupt PNGs? (y/n)", "y").lower()
    if confirm != "y":
        warn("Cancelled.")
        pause()
        return

    do_redownload = prompt("Re-download quarantined PNGs immediately? (y/n)", "n").lower() == "y"

    print()
    divider("Scanning…")
    results = purify_module_pngs(manifest, redownload=do_redownload)
    print()
    divider("Summary")
    info(f"Scanned   : {results['scanned']}")
    ok(  f"Valid     : {results['ok']}")
    if results["quarantined"]:
        warn(f"Quarantined: {results['quarantined']}  (renamed to *.png.bad)")
    else:
        ok(  f"Quarantined: 0  — all files are valid PNGs")
    if results["errors"]:
        err( f"Errors    : {results['errors']}")
    pause()



def main():
    # Setup directories
    for d in (DATA, RAW, MODULES, ICONS, LOOT):
        d.mkdir(parents=True, exist_ok=True)

    # Check requests is available
    if not REQUESTS_OK:
        print()
        err("The 'requests' library is not installed.")
        print()
        print("  Run:  pip install requests")
        print()
        input("Press Enter to exit…")
        sys.exit(1)

    banner()

    # Try to load manifest (local cache)
    manifest = load_manifest()
    if manifest is None:
        info("No local manifest found. Fetching from server…")
        manifest = fetch_manifest()
        if manifest is None:
            err("Could not load map manifest. Check your internet connection.")
            pause()
            return

    ok(f"Map manifest loaded  ({len(manifest)} maps)")

    # Main loop
    while True:
        print()
        local_status(manifest)
        opts = [
            "Download map data  (JSON + module PNGs)",
            "Download loot & icon assets",
            "Refresh manifest from server",
            "Inspect downloaded data",
            "Purify module PNGs  (remove corrupt / HTML files)",
            "Clear cache",
            c("Exit", "red"),
        ]
        choice = choose(opts, "Main Menu")

        if choice == 1:
            menu_download_maps(manifest)
        elif choice == 2:
            menu_loot_assets(manifest)
        elif choice == 3:
            manifest = fetch_manifest(force=True)
            if manifest:
                ok("Manifest refreshed.")
            pause()
        elif choice == 4:
            menu_inspect(manifest)
        elif choice == 5:
            menu_purify_pngs(manifest)
        elif choice == 6:
            menu_clear()
        elif choice == 7:
            print()
            ok("Goodbye!")
            print()
            sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        warn("Interrupted.")
        sys.exit(0)
