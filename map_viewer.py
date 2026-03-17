#!/usr/bin/env python3
"""
Dark and Darker – Map Viewer (Desktop)
Run:  python map_viewer.py
Requires: pip install Pillow

Optional (for map scanner + minimap tracker):
  pip install Pillow mss numpy
"""

import json, math, re, sys, threading, time, tkinter as tk
from tkinter import ttk
from pathlib import Path
from collections import Counter
try:
    from PIL import Image, ImageDraw, ImageTk
except ImportError:
    print("[!] Pillow not installed.  Run:  pip install Pillow")
    sys.exit(1)

# Optional: ImageGrab for screenshots (works on Windows/macOS)
try:
    from PIL import ImageGrab
    _HAVE_IMAGEGRAB = True
except ImportError:
    _HAVE_IMAGEGRAB = False

# Optional: numpy for fast template matching
try:
    import numpy as np
    _HAVE_NUMPY = True
except ImportError:
    _HAVE_NUMPY = False

# Optional: mss for cross-platform screen capture
try:
    import mss as _mss_mod
    _HAVE_MSS = True
except ImportError:
    _HAVE_MSS = False

# Optional: cursor_detect for robust minimap cursor tracking
try:
    import cursor_detect as _cd
    _HAVE_CURSOR_DETECT = True
except ImportError:
    _HAVE_CURSOR_DETECT = False

# Optional: MapScannerV2 (new robust scanner)
try:
    from map_scanner_v2 import MapScannerV2 as _MapScannerV2
    _HAVE_SCANNER_V2 = True
except ImportError:
    _HAVE_SCANNER_V2 = False

ROOT    = Path(__file__).parent
DATA    = ROOT / "data"
RAW     = DATA / "raw"
MODULES = DATA / "modules"
ICONS   = DATA / "icons"

# ── Palette (modern dark-gray / gold — no purple) ─────────
BG      = "#141414"
PANEL   = "#1c1c1c"
PANEL2  = "#242424"
BORDER  = "#303030"
BDR2    = "#424242"
ACCENT  = "#c8a84b"
TEXT    = "#e0ddd8"
DIM     = "#808080"
DIM2    = "#525252"
TILEBG  = "#1a1a1a"
BTN_BG  = "#2a2a2a"
BTN_HOV = "#3c3c3c"

# ── Category config with subgroups ────────────────────────
CATS = {
    # Chests – High Tier
    "chest_legendary": dict(label="Legendary",    color="#FFD700", group="Chests",    sub="High Tier",   r=9,  ring=True,  pri=10),
    "chest_hoard":     dict(label="Hoard",         color="#FF8C00", group="Chests",    sub="High Tier",   r=9,  ring=True,  pri=9),
    # Chests – Low Tier
    "chest_rare":      dict(label="Rare",          color="#C060FF", group="Chests",    sub="Low Tier",    r=7,  ring=True,  pri=8),
    "chest_uncommon":  dict(label="Uncommon",      color="#00CC44", group="Chests",    sub="Low Tier",    r=7,  ring=False, pri=7),
    "chest_common":    dict(label="Common",        color="#AAAAAA", group="Chests",    sub="Low Tier",    r=6,  ring=False, pri=5),
    # Exits
    "exit":            dict(label="Exit / Portal", color="#00FF88", group="Exits",     sub=None,          r=9,  ring=True,  pri=9),
    # Monsters – Bosses
    "sub_boss":        dict(label="Boss Spawn",    color="#FF3333", group="Monsters",  sub="Bosses",      r=10, ring=True,  pri=10),
    # Monsters – Mobs
    "monster":         dict(label="Mob Spawn",     color="#884422", group="Monsters",  sub="Mobs",        r=4,  ring=False, pri=1),
    # Resources
    "resource":        dict(label="Resource / Ore",color="#00CED1", group="Resources", sub=None,          r=7,  ring=True,  pri=8),
    # Shrines
    "shrine":          dict(label="Shrine",        color="#FF69B4", group="Shrines",   sub=None,          r=7,  ring=True,  pri=8),
    # Loot
    "loot_valuable":   dict(label="Valuables",     color="#FFD060", group="Loot",      sub="Valuables",   r=6,  ring=False, pri=6),
    "loot_equipment":  dict(label="Equipment",     color="#50C850", group="Loot",      sub="Equipment",   r=6,  ring=False, pri=5),
    "loot_trinket":    dict(label="Trinket",       color="#A050E0", group="Loot",      sub="Trinkets",    r=5,  ring=False, pri=4),
    "loot_consumable": dict(label="Consumable",    color="#80C040", group="Loot",      sub="Consumables", r=5,  ring=False, pri=3),
    "loot_ground":     dict(label="Ground Loot",   color="#607080", group="Loot",      sub="Ground",      r=4,  ring=False, pri=2),
    # Hazards
    "trap":            dict(label="Trap",          color="#FF6030", group="Hazards",   sub="Traps",       r=5,  ring=False, pri=4),
    "hazard_zone":     dict(label="Hazard Zone",   color="#FF2020", group="Hazards",   sub="Zones",       r=7,  ring=True,  pri=6),
    # Interactive
    "gate":            dict(label="Gate",          color="#C09850", group="Interact",  sub=None,          r=5,  ring=False, pri=3),
    "lever":           dict(label="Lever",         color="#D0A060", group="Interact",  sub=None,          r=4,  ring=False, pri=3),
    "door":            dict(label="Door",          color="#A07050", group="Interact",  sub=None,          r=4,  ring=False, pri=2),
}

# group → list of (subgroup_label_or_None, [cat_keys])
GROUPS_CONFIG = [
    ("Chests",    [("High Tier", ["chest_legendary", "chest_hoard"]),
                   ("Low Tier",  ["chest_rare", "chest_uncommon", "chest_common"])]),
    ("Exits",     [(None,         ["exit"])]),
    ("Monsters",  [("Bosses",    ["sub_boss"]),
                   ("Mobs",      ["monster"])]),
    ("Resources", [(None,         ["resource"])]),
    ("Shrines",   [(None,         ["shrine"])]),
    ("Loot",      [("Valuables",  ["loot_valuable"]),
                   ("Equipment",  ["loot_equipment"]),
                   ("Trinkets",   ["loot_trinket"]),
                   ("Consumables",["loot_consumable"]),
                   ("Ground",     ["loot_ground"])]),
    ("Hazards",   [("Traps",     ["trap"]),
                   ("Zones",     ["hazard_zone"])]),
    ("Interact",  [(None,         ["gate", "lever", "door"])]),
]

DEFAULT_VISIBLE = {k for k, v in CATS.items() if v["group"] != "Monsters"}

# ── Known-broken modules ──────────────────────────────────
BROKEN_MODULES = {
    "Cave":    {"CaveMaze_Center_02"},
    "Crypt":   {"Crypt_BlindfallPit", "Crypt_LightlessChamber_01",
                "Crypt_LightlessTomb_01", "Crypt_MadCorridors", "Crypt_TorchboundVault"},
    "IceCave": {"IceCave_Altar", "IceCave_Turnnel_01"},
}

# ── Grid layouts ──────────────────────────────────────────
# Cave: 25 valid modules → 5×5
CAVE_LAYOUT = {
    "CaveMaze":              (0,0,1), "CaveTown":              (1,0,1), "CaveTown_02":           (2,0,1), "Cave_Altar_02":         (3,0,1), "Cave_Altar_Center":     (4,0,1),
    "Cave_AntsNest":         (0,1,1), "Cave_BanditCamp_Center":(1,1,1), "Cave_MoleTunnel":       (2,1,1), "Cave_PitHall":          (3,1,1), "Cave_Rooms":            (4,1,1),
    "Cave_SpiderNest_02":    (0,2,1), "Cave_Tomb_Center":      (1,2,1), "Cave_Valley":           (2,2,1), "Cave_Valley_02":        (3,2,1), "CavernLake_02":         (4,2,1),
    "CavernLake_03":         (0,3,1), "GoblinCaveCorner_01":   (1,3,1), "GoblinJail_Center":     (2,3,1), "GoblinJail_Center_02":  (3,3,1), "GoblinMineCenter_01":   (4,3,1),
    "HideoutCave_02":        (0,4,1), "HideoutCave_Center":    (1,4,1), "SpiderCave_02":         (2,4,1), "StoneGrave_Center":     (3,4,1), "StoneGrave_Center_02":  (4,4,1),
}

# IceAbyss: 22 valid modules → 5×5 (3 empty slots at end)
ICEABYSS_LAYOUT = {
    "IceAbyss_AbyssTooth":       (0,0,1), "IceAbyss_CharnelHouse":     (1,0,1), "IceAbyss_DeathPit":         (2,0,1), "IceAbyss_DeepFlow":         (3,0,1), "IceAbyss_DemonicDen":       (4,0,1),
    "IceAbyss_DividingHill":     (0,1,1), "IceAbyss_FalseThrone":      (1,1,1), "IceAbyss_FrostMaw":         (2,1,1), "IceAbyss_FrostwovenPillars":(3,1,1), "IceAbyss_FrozenHold":       (4,1,1),
    "IceAbyss_Glacivia":         (0,2,1), "IceAbyss_GrandHollow":      (1,2,1), "IceAbyss_HoundVale":        (2,2,1), "IceAbyss_IceMaze":          (3,2,1), "IceAbyss_ImpRitualRooms":   (4,2,1),
    "IceAbyss_Monoliths":        (0,3,1), "IceAbyss_Pillars":          (1,3,1), "IceAbyss_ReversedPyramid":  (2,3,1), "IceAbyss_ShallowValley":    (3,3,1), "IceAbyss_SinkingPillars":   (4,3,1),
    "IceAbyss_StaircaseHill":    (0,4,1), "IceAbyss_WyvernLair":       (1,4,1),
}

# IceCave: 25 valid modules (Altar + Tunnel removed) → 5×5
ICECAVE_LAYOUT = {
    "IceCave_Barricade":  (0,0,1), "IceCave_Bridge":       (1,0,1), "IceCave_Cabin_Raft":   (2,0,1), "IceCave_CavePathway":  (3,0,1), "IceCave_CaveSwamp":    (4,0,1),
    "IceCave_CrossRoad":  (0,1,1), "IceCave_DualCaverns":  (1,1,1), "IceCave_FourRoute":    (2,1,1), "IceCave_FrozenLake":   (3,1,1), "IceCave_Guardpost":    (4,1,1),
    "IceCave_Hive_03":    (0,2,1), "IceCave_Hut_01":       (1,2,1), "IceCave_Hut_02":       (2,2,1), "IceCave_Hut_03":       (3,2,1), "IceCave_IcicleCave":   (4,2,1),
    "IceCave_Maze":       (0,3,1), "IceCave_MountainPass": (1,3,1), "IceCave_NarrowBridge": (2,3,1), "IceCave_Path":         (3,3,1), "IceCave_Pyramid":      (4,3,1),
    "IceCave_Quarry":     (0,4,1), "IceCave_Sanctum":      (1,4,1), "IceCave_Spiral":       (2,4,1), "IceCave_Watchtower":   (3,4,1), "IceCave_WolfCave":     (4,4,1),
}

# Inferno: 26 valid modules → 6 cols × 5 rows (4 empty)
INFERNO_LAYOUT = {
    "ConectorDoubleBridge":  (0,0,1), "DarkRitualRoom_04":     (1,0,1), "DeathAltar":            (2,0,1), "DemonDen":              (3,0,1), "InfernoCastleConner":   (4,0,1), "InfernoColumns":        (5,0,1),
    "InfernoGate":           (0,1,1), "InfernoMouth":          (1,1,1), "InfernoRiver":          (2,1,1), "InfernoRooms":          (3,1,1), "Inferno_Batroost":      (4,1,1), "Inferno_Bloodyfalls":   (5,1,1),
    "Inferno_DeathPlatforms":(0,2,1), "Inferno_Doomcage":      (1,2,1), "Inferno_DownStair":     (2,2,1), "Inferno_Drain":         (3,2,1), "Inferno_Hellcrossbridge":(4,2,1),"Inferno_Hellwind":      (5,2,1),
    "Inferno_Judgementroad": (0,3,1), "Inferno_LavaCorner":    (1,3,1), "Inferno_LavaStairway":  (2,3,1), "Inferno_Obelisk":       (3,3,1), "Inferno_Painfulsteps":  (4,3,1), "Skull":                 (5,3,1),
    "SpiderCave_01":         (0,4,1), "ThroneRoom_02":         (1,4,1),
}

# Crypt layout (extracted from website)
CRYPT_LAYOUT = {
    "TreasureRoom_01":(0,0,1),"UndergroundAltar":(1,0,1),
    "SingleLogBridge":(0,1,1),"SkeletonPit":(1,1,1),"Storeroom":(2,1,1),
    "Swamp":(3,1,1),"TheCage":(4,1,1),"TheMiniWheel":(5,1,1),
    "ThePit":(6,1,1),"Tomb_Center":(7,1,1),
    "LowPyramid":(0,2,1),"Maze":(1,2,1),"MimicRoom":(2,2,1),
    "OldTomb":(3,2,1),"OssuaryEdge":(4,2,1),"Prison_01":(5,2,1),
    "Sanctum":(6,2,1),"Sewers":(7,2,1),
    "EightToOne_01":(0,3,1),"EightToOne_02":(1,3,1),"FishingGround":(2,3,1),
    "FourWayConnect":(3,3,1),"GuardPost":(4,3,1),"Hallways":(5,3,1),
    "HBridge":(6,3,1),"HighPriestOssuary":(7,3,1),
    "Crypt_Dungeon":(0,4,1),"Crypt_FourRooms":(1,4,1),
    "Crypt_GreatWalkway":(2,4,1),"Crypt_LargeRoomPit":(3,4,1),
    "Crypt_Ramparts":(4,4,1),"Crypt_Vault":(5,4,1),
    "DarkMagicLibrary_Center":(6,4,1),"DeathHall":(7,4,1),
    "Connector_Trap_02":(0,5,1),"CorridorCrypt":(1,5,1),
    "CorridorofDarkPriests":(2,5,1),"CrossRoad":(3,5,1),
    "Crypt_AltarRoomAB":(4,5,1),"Crypt_Atrium":(5,5,1),
    "Crypt_Chapel":(6,5,1),"Crypt_DarkRitualRoom_01":(7,5,1),
    "CenterTower":(0,6,2),
    "Cemetery_03":(2,6,1),"Cistern":(3,6,1),"CliffBridge":(4,6,1),
    "ComplexHall":(5,6,1),"Connector_01":(6,6,1),"Connector_Trap_01":(7,6,1),
    "AdmirerRoom":(2,7,1),"Armory":(3,7,1),"Barracks":(4,7,1),
    "Catacomb":(5,7,1),"Cemetery_01":(6,7,1),"Cemetery_02":(7,7,1),
}

KNOWN_LAYOUTS = {
    "Crypt":    CRYPT_LAYOUT,
    "Cave":     CAVE_LAYOUT,
    "IceAbyss": ICEABYSS_LAYOUT,
    "IceCave":  ICECAVE_LAYOUT,
    "Inferno":  INFERNO_LAYOUT,
}


def auto_layout(keys):
    """Square-grid layout — skips _Arena keys to avoid gaps."""
    valid = [k for k in keys if not k.endswith("_Arena")]
    cols  = max(1, math.ceil(math.sqrt(len(valid))))
    return {k: (i % cols, i // cols, 1) for i, k in enumerate(valid)}


def get_layout(name, manifest):
    if name in KNOWN_LAYOUTS:
        return KNOWN_LAYOUTS[name]
    return auto_layout(manifest.get(name, {}).get("moduleKeys", []))


# Delay (ms) before re-rendering focus canvas on first selection
FOCUS_RENDER_DELAY_MS = 50

# ── Settings ──────────────────────────────────────────────
SETTINGS_PATH = DATA / "settings.json"
DEFAULTS = {
    "tile_px": 180, "focus_tile_px": 500,
    "marker_scale": 1.0, "focus_marker_scale": 1.8,
    "show_labels": True, "mode": "N", "last_map": "",
    "visible_cats_map":   list(DEFAULT_VISIBLE),
    "visible_cats_focus": list(DEFAULT_VISIBLE),
    "keybinds": {
        "scan_map": "Shift+M",
        "fit":      "f",
        "zoom_in":  "plus",
        "zoom_out": "minus",
    },
}


def load_settings():
    if SETTINGS_PATH.exists():
        try:
            saved  = json.loads(SETTINGS_PATH.read_text())
            merged = {**DEFAULTS, **saved}
            # Back-compat: old single "visible_cats" key
            if "visible_cats" in saved and "visible_cats_map" not in saved:
                merged["visible_cats_map"]   = saved["visible_cats"]
                merged["visible_cats_focus"] = saved["visible_cats"]
            return merged
        except Exception:
            pass
    return DEFAULTS.copy()


def save_settings(s):
    DATA.mkdir(exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(s, indent=2))


# ── Data loading ──────────────────────────────────────────
def load_manifest():
    p = DATA / "map_manifest.json"
    return json.loads(p.read_text()) if p.exists() else {}


def load_map(map_name, mode, manifest):
    rp = RAW / f"{map_name}.json"
    if not rp.exists():
        return {}
    layout       = get_layout(map_name, manifest)
    localized    = manifest.get(map_name, {}).get("moduleLocalizedStrings", {})
    allowed_keys = set(manifest.get(map_name, {}).get("moduleKeys", []))
    broken       = BROKEN_MODULES.get(map_name, set())
    data_key     = f"{mode}_Data"
    raw          = json.loads(rp.read_text())

    used    = set((v[0], v[1]) for v in layout.values())
    max_row = max((v[1] for v in layout.values()), default=-1) if layout else -1
    oc = 0; ory = max_row + 1
    out = {}
    for mk, mv in raw.items():
        if not isinstance(mv, dict):
            continue
        if mk.endswith("_Arena"):
            continue
        if mk in broken:
            continue
        if allowed_keys and mk not in allowed_keys:
            continue
        if mk in layout:
            col, row, span = layout[mk]
        else:
            while (oc, ory) in used:
                oc += 1
                if oc > 12:
                    oc = 0; ory += 1
            col, row, span = oc, ory, 1
            used.add((oc, ory)); oc += 1

        items_raw = mv.get(data_key) or mv.get("N_Data", [])
        xs = [i["object_location"]["X"] for i in items_raw if "object_location" in i]
        ys = [i["object_location"]["Y"] for i in items_raw if "object_location" in i]
        if xs:
            xp   = (max(xs) - min(xs)) * 0.08 or 150
            yp   = (max(ys) - min(ys)) * 0.08 or 150
            bbox = {"xmin": min(xs)-xp, "xmax": max(xs)+xp,
                    "ymin": min(ys)-yp, "ymax": max(ys)+yp}
        else:
            bbox = {"xmin":-1600, "xmax":1600, "ymin":-1600, "ymax":1600}

        items = [{"id":   i.get("object_name", ""),
                  "cat":  i.get("entity_category", "?"),
                  "name": i.get("LocalizedString", ""),
                  "x":    i["object_location"]["X"],
                  "y":    i["object_location"]["Y"]}
                 for i in items_raw if "object_location" in i]

        out[mk] = {
            "col": col, "row": row, "span": span,
            "label": localized.get(mk, mv.get("Module_LocalizedString", mk) or mk),
            "bbox": bbox, "items": items,
            "has_png": _is_png_file(MODULES / map_name / f"{mk}.png"),
        }
    return out


# ── Image helpers ─────────────────────────────────────────
_img_cache: dict = {}
PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _is_png_file(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(8) == PNG_SIG
    except Exception:
        return False


def hex_rgb(h):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def get_tile_img(map_name, mod_key, px):
    k = f"{map_name}/{mod_key}/{px}"
    if k in _img_cache:
        return _img_cache[k]
    p   = MODULES / map_name / f"{mod_key}.png"
    img = None
    if p.exists():
        try:
            if not _is_png_file(p):
                raise ValueError("Invalid PNG signature.")
            im = Image.open(p)
            im.load()
            img = im.convert("RGBA").resize((px, px), Image.LANCZOS)
        except Exception:
            try:
                bad = p.with_suffix(p.suffix + ".bad")
                if not bad.exists():
                    p.rename(bad)
            except Exception:
                pass
            img = None
    if img is None:
        img = Image.new("RGBA", (px, px), (19, 19, 32, 255))
        d   = ImageDraw.Draw(img)
        d.rectangle([0, 0, px-1, px-1], outline=(44, 43, 54), width=1)
    _img_cache[k] = img
    return img


# ── Per-item icon system ──────────────────────────────────
_icon_index: dict = {}   # lower_stem -> actual_stem (built on first use)


def _build_icon_index():
    """Build case-insensitive index of available icons."""
    global _icon_index
    if _icon_index:
        return
    if ICONS.is_dir():
        for p in ICONS.iterdir():
            if p.suffix.lower() == ".png":
                _icon_index[p.stem.lower()] = p.stem


def _find_icon_stem(object_name: str):
    """Return the icon file stem for *object_name* using multiple fallback patterns.
    Returns None when no icon can be found."""
    _build_icon_index()
    if not object_name:
        return None

    # Build ordered candidate list
    cands = [object_name]

    # Strip known variant suffixes (underwater / difficulty variants)
    for sfx in ("_UnderSea", "_Hard", "_Nightmare"):
        if object_name.endswith(sfx):
            cands.append(object_name[: -len(sfx)])

    # Id_Monster_XXX_Tier  →  try  Id_Monster_XXX_Common  +  ID_Lootdrop_Drop_XXX
    m = re.match(
        r"Id_Monster_(.+?)_(?:Common|Elite|Hard|Nightmare|Unique)$", object_name, re.I
    )
    if m:
        name = m.group(1)
        cands.append(f"Id_Monster_{name}_Common")
        cands.append(f"ID_Lootdrop_Drop_{name}")

    # Strip trailing _<number>  (e.g. Id_Props_Hoard01_5 → Id_Props_Hoard01)
    base = re.sub(r"_\d+$", "", object_name)
    if base != object_name:
        cands.append(base)
        for sfx in ("_UnderSea", "_Hard"):
            if base.endswith(sfx):
                cands.append(base[: -len(sfx)])

    # BP_XXX_C_N  →  try BP_XXX_C_0 / BP_XXX_C_1 / BP_XXX_0
    m2 = re.match(r"(BP_.+?)_C_\d+$", object_name)
    if m2:
        bp = m2.group(1)
        cands.extend([f"{bp}_C_0", f"{bp}_C_1", f"{bp}_0"])

    # Also try without the _C_ portion entirely
    m3 = re.match(r"(BP_.+?)_C$", object_name)
    if m3:
        cands.append(f"{m3.group(1)}_0")

    for c in cands:
        stem = _icon_index.get(c.lower())
        if stem:
            return stem
    return None


_icon_img_cache: dict = {}   # "{object_name}/{size}" -> Image | None


def get_item_icon(object_name: str, size: int):
    """Return a *size×size* RGBA PIL Image for *object_name*, or None."""
    k = f"{object_name}/{size}"
    if k in _icon_img_cache:
        return _icon_img_cache[k]
    stem = _find_icon_stem(object_name)
    img  = None
    if stem:
        p = ICONS / f"{stem}.png"
        if p.exists():
            try:
                if _is_png_file(p):
                    raw = Image.open(p).convert("RGBA")
                    img = raw.resize((size, size), Image.LANCZOS)
            except Exception:
                img = None
    _icon_img_cache[k] = img
    return img


def render_tile(map_name, mod_key, mod, tile_px, visible, mscale):
    span = mod["span"]
    W = H = span * tile_px
    img  = get_tile_img(map_name, mod_key, W).copy()
    draw = ImageDraw.Draw(img)
    bb   = mod["bbox"]
    xr   = bb["xmax"] - bb["xmin"] or 1
    yr   = bb["ymax"] - bb["ymin"] or 1
    for item in sorted(mod["items"],
                       key=lambda i: CATS.get(i["cat"], {}).get("pri", 0)):
        cat = item["cat"]
        cfg = CATS.get(cat)
        if not cfg or cat not in visible:
            continue
        px2 = ((item["x"] - bb["xmin"]) / xr) * W
        py2 = ((bb["ymax"] - item["y"]) / yr) * H
        r   = max(2, int(cfg["r"] * mscale))
        rc  = hex_rgb(cfg["color"])

        # Icon diameter = 2× marker radius (minimum 12 px for readability)
        icon_d  = max(12, r * 2)
        icon_img = get_item_icon(item["id"], icon_d)

        if icon_img is not None:
            # Optional ring for important categories
            if cfg["ring"]:
                rr = r + max(2, int(3 * mscale))
                draw.ellipse([px2 - rr, py2 - rr, px2 + rr, py2 + rr],
                             outline=(*rc, 110), width=max(1, int(1.5 * mscale)))
            # Paste icon centred on the marker position
            ix = int(px2 - icon_d / 2)
            iy = int(py2 - icon_d / 2)
            img.paste(icon_img, (ix, iy), icon_img)
        else:
            # Fallback: coloured circle
            if cfg["ring"]:
                rr = r + max(2, int(4 * mscale))
                draw.ellipse([px2 - rr, py2 - rr, px2 + rr, py2 + rr],
                             outline=(*rc, 90), width=max(1, int(1.5 * mscale)))
            draw.ellipse([px2 - r, py2 - r, px2 + r, py2 + r],
                         fill=(*rc, 235), outline=(0, 0, 0, 200),
                         width=max(1, int(mscale)))
    return img


# ── Scrollable frame ──────────────────────────────────────
class ScrollFrame(tk.Frame):
    def __init__(self, parent, bg=PANEL, **kw):
        super().__init__(parent, bg=bg, **kw)
        self.cv    = tk.Canvas(self, bg=bg, bd=0, highlightthickness=0)
        self.sb    = tk.Scrollbar(self, orient="vertical", command=self.cv.yview)
        self.inner = tk.Frame(self.cv, bg=bg)
        self.inner.bind("<Configure>",
                        lambda e: self.cv.configure(scrollregion=self.cv.bbox("all")))
        self.cv.create_window((0, 0), window=self.inner, anchor="nw")
        self.cv.configure(yscrollcommand=self.sb.set)
        self.cv.pack(side="left", fill="both", expand=True)
        self.sb.pack(side="right", fill="y")
        for w in (self.cv, self.inner):
            w.bind("<MouseWheel>", self._scroll, add="+")
            w.bind("<Button-4>",   self._scroll, add="+")
            w.bind("<Button-5>",   self._scroll, add="+")

    def _scroll(self, e):
        d = -1 if (getattr(e, "delta", 0) > 0 or e.num == 4) else 1
        self.cv.yview_scroll(d, "units")

    def bind_all_children(self, widget=None):
        """Recursively bind scroll to every widget so anywhere scrolls the panel."""
        if widget is None:
            widget = self.inner
        for child in widget.winfo_children():
            child.bind("<MouseWheel>", self._scroll, add="+")
            child.bind("<Button-4>",   self._scroll, add="+")
            child.bind("<Button-5>",   self._scroll, add="+")
            self.bind_all_children(child)


# ── Flat button helper ────────────────────────────────────
def flat_btn(parent, text, cmd, fg=TEXT, bg=BTN_BG,
             font=("Segoe UI", 9), padx=10, pady=4, width=None):
    kw = dict(text=text, command=cmd, bg=bg, fg=fg, bd=0,
              font=font, padx=padx, pady=pady,
              activebackground=BTN_HOV, activeforeground=TEXT,
              relief="flat", cursor="hand2")
    if width:
        kw["width"] = width
    return tk.Button(parent, **kw)


# ── Main App ──────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Dark and Darker – Map Viewer")
        self.configure(bg=BG)
        self.geometry("1600x900")
        self.minsize(900, 600)
        self.manifest      = load_manifest()
        self.cfg           = load_settings()
        self.modules       = {}
        self.visible_map   = set(self.cfg.get("visible_cats_map",   list(DEFAULT_VISIBLE)))
        self.visible_focus = set(self.cfg.get("visible_cats_focus", list(DEFAULT_VISIBLE)))
        self.focus_key     = None
        self._tkimgs       = []
        self._zoom         = 1.0
        self._panx         = 0.0
        self._pany         = 0.0
        self._drag         = None
        self._tt_win       = None
        self._cur_map      = ""
        self._settings_win = None
        self._fvars_map    = {}
        self._fvars_focus  = {}
        self._cnt_labels   = {}
        self._spins        = {}

        # ── Composite cache (performance) ─────────────────
        self._map_composite: Image.Image | None = None
        self._map_comp_params = None   # (tp, ms, focus_key) when last built
        self._map_comp_zoom   = 0.0
        self._map_comp_scaled: Image.Image | None = None
        self._map_comp_tk     = None
        self._map_comp_id     = None   # canvas item id
        self._map_dirty       = True

        # ── Player tracker ────────────────────────────────
        self._tracker: MinimapTracker | None = None
        self._player_map_pos = None      # (world_x, world_y) of player
        self._player_angle   = 0.0       # degrees, 0=north
        self._track_btn_var  = tk.BooleanVar(value=False)

        # ── Scanner ───────────────────────────────────────
        self._scanner: MapScanner | None = None

        self._build()
        self._populate_maps()
        self.map_var.set("")
        self.status.set("Select a map to load.")
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Build UI ──────────────────────────────────────────
    def _build(self):
        # ── Outer horizontal split: left column | right map ──────────────
        pw = tk.PanedWindow(self, orient="horizontal", bg=BG,
                            sashwidth=4, sashrelief="flat", sashpad=0)
        pw.pack(fill="both", expand=True)

        # ── Left column container ─────────────────────────────────────────
        left_col = tk.Frame(pw, bg=PANEL, width=300)
        left_col.pack_propagate(False)
        pw.add(left_col, minsize=240)

        # App header pinned at top of left column
        hdr = tk.Frame(left_col, bg=PANEL2, pady=7)
        hdr.pack(fill="x")
        tk.Label(hdr, text="\u2694  D&D  Map Viewer", bg=PANEL2, fg=ACCENT,
                 font=("Segoe UI", 11, "bold"), padx=10).pack(anchor="w")
        tk.Frame(left_col, bg=BORDER, height=1).pack(fill="x")

        # Vertical split: focus panel (top) | sidebar (bottom)
        left_pw = tk.PanedWindow(left_col, orient="vertical", bg=BG,
                                 sashwidth=4, sashrelief="flat", sashpad=0)
        left_pw.pack(fill="both", expand=True)

        focus_f = tk.Frame(left_pw, bg=PANEL2)
        left_pw.add(focus_f, minsize=150, height=360)
        self._build_focus(focus_f)

        sidebar_f = tk.Frame(left_pw, bg=PANEL)
        left_pw.add(sidebar_f, minsize=180)
        self._build_sidebar(sidebar_f)

        # ── Right column: map canvas (full height) ────────────────────────
        map_f = tk.Frame(pw, bg=BG)
        pw.add(map_f, minsize=600)
        self._build_map(map_f)

    # ── Sidebar ───────────────────────────────────────────
    def _build_sidebar(self, p):
        self._sf = ScrollFrame(p, bg=PANEL)
        self._sf.pack(fill="both", expand=True)
        b = self._sf.inner

        def sec(txt):
            tk.Frame(b, bg=BORDER, height=1).pack(fill="x", pady=(10, 0))
            tk.Label(b, text=txt.upper(), bg=PANEL, fg=DIM2,
                     font=("Segoe UI", 8), padx=8, pady=3,
                     anchor="w").pack(fill="x")

        # Map
        sec("Map")
        self.map_var   = tk.StringVar()
        self.map_combo = ttk.Combobox(b, textvariable=self.map_var,
                                      state="readonly", font=("Segoe UI", 11))
        self._style_widgets()
        self.map_combo.pack(fill="x", padx=8, pady=4)
        self.map_combo.bind("<<ComboboxSelected>>", lambda e: self._reload_map())

        # Mode
        sec("Mode")
        mrow = tk.Frame(b, bg=PANEL)
        mrow.pack(fill="x", padx=8, pady=4)
        self.mode_var = tk.StringVar(value=self.cfg.get("mode", "N"))
        for val, lbl in [("N", "Normal"), ("HR", "High Roller")]:
            tk.Radiobutton(mrow, text=lbl, variable=self.mode_var, value=val,
                           bg=PANEL, fg=TEXT, selectcolor=PANEL2,
                           activebackground=PANEL, activeforeground=ACCENT,
                           font=("Segoe UI", 10),
                           command=self._reload_map).pack(side="left", padx=(0, 8))

        # Module list
        sec("Modules  (click to focus)")
        self.mod_list = tk.Listbox(b, bg=PANEL2, fg=TEXT, selectbackground=ACCENT,
                                   selectforeground="#111", font=("Segoe UI", 10),
                                   bd=0, highlightthickness=1,
                                   highlightcolor=BDR2, highlightbackground=BORDER,
                                   height=8, exportselection=False)
        self.mod_list.pack(fill="x", padx=8, pady=4)
        self.mod_list.bind("<<ListboxSelect>>", self._on_mod_select)

        # Loot Filters
        sec("Loot Filters")
        # Global ALL / NONE row
        gall_row = tk.Frame(b, bg=PANEL)
        gall_row.pack(fill="x", padx=8, pady=(4, 2))
        tk.Label(gall_row, text="All categories:", bg=PANEL, fg=DIM,
                 font=("Segoe UI", 8)).pack(side="left")
        flat_btn(gall_row, "ALL",  self._all_on,
                 fg=DIM, bg=PANEL2, padx=6, pady=2,
                 font=("Segoe UI", 8)).pack(side="right", padx=1)
        flat_btn(gall_row, "NONE", self._all_off,
                 fg=DIM, bg=PANEL2, padx=6, pady=2,
                 font=("Segoe UI", 8)).pack(side="right", padx=1)

        # Column header row  (Focus left | Map right — mirrors panel positions)
        hrow = tk.Frame(b, bg=PANEL)
        hrow.pack(fill="x", padx=8, pady=(2, 0))
        tk.Label(hrow, text="", bg=PANEL, width=20, anchor="w").pack(side="left")
        # Map is on the right → its checkbox header is on the right
        tk.Label(hrow, text="Map",   bg=PANEL, fg=DIM,
                 font=("Segoe UI", 8), width=4).pack(side="right")
        tk.Label(hrow, text="Focus", bg=PANEL, fg=DIM,
                 font=("Segoe UI", 8), width=5).pack(side="right")

        for gname, subgroups in GROUPS_CONFIG:
            all_cats_in_group = [ck for _, cks in subgroups for ck in cks]
            if not all_cats_in_group:
                continue
            self._build_filter_group(b, gname, subgroups, all_cats_in_group)

        self.after(100, self._sf.bind_all_children)

        # ── Bottom bar: status (full-width) + settings gear ──────────────
        tk.Frame(p, bg=BORDER, height=1).pack(fill="x")
        bot = tk.Frame(p, bg=PANEL2)
        bot.pack(fill="x")
        tk.Button(bot, text="\u2699", bg=PANEL2, fg=ACCENT, bd=0,
                  font=("Segoe UI", 13), padx=8, pady=3,
                  activebackground=BDR2, activeforeground=ACCENT,
                  relief="flat", cursor="hand2",
                  command=self._open_settings).pack(side="right")
        self.status = tk.StringVar(value="Ready")
        # Status label below settings row — wraps and never pushes the gear icon
        tk.Label(bot, textvariable=self.status, bg=PANEL2, fg=DIM,
                 font=("Segoe UI", 9), padx=8, pady=4,
                 anchor="w", justify="left",
                 wraplength=240).pack(side="left", fill="x", expand=True)

    def _build_filter_group(self, parent, gname, subgroups, all_cats_in_group):
        grp_frame = tk.Frame(parent, bg=PANEL)
        grp_frame.pack(fill="x", padx=4, pady=(4, 0))

        hdr_row = tk.Frame(grp_frame, bg=PANEL2)
        hdr_row.pack(fill="x")

        collapsed  = tk.BooleanVar(value=True)   # start collapsed
        body_frame = tk.Frame(grp_frame, bg=PANEL)

        arrow_lbl = tk.Label(hdr_row, text="\u25b6", bg=PANEL2, fg=DIM,
                             font=("Segoe UI", 8), width=2)
        arrow_lbl.pack(side="left", padx=(4, 0))
        tk.Label(hdr_row, text=gname, bg=PANEL2, fg=TEXT,
                 font=("Segoe UI", 9, "bold"), anchor="w").pack(
                     side="left", fill="x", expand=True, padx=2)

        def toggle_group(_e=None):
            if collapsed.get():
                collapsed.set(False)
                arrow_lbl.config(text="\u25bc")
                body_frame.pack(fill="x", padx=4, pady=(0, 2))
            else:
                collapsed.set(True)
                arrow_lbl.config(text="\u25b6")
                body_frame.pack_forget()

        for w in (hdr_row, arrow_lbl):
            w.bind("<Button-1>", toggle_group)

        def make_grp_all(cats):
            def fn():
                for ck in cats:
                    if ck in self._fvars_map:   self._fvars_map[ck].set(True)
                    if ck in self._fvars_focus: self._fvars_focus[ck].set(True)
                self._filter_changed()
            return fn

        def make_grp_none(cats):
            def fn():
                for ck in cats:
                    if ck in self._fvars_map:   self._fvars_map[ck].set(False)
                    if ck in self._fvars_focus: self._fvars_focus[ck].set(False)
                self._filter_changed()
            return fn

        flat_btn(hdr_row, "ALL",  make_grp_all(all_cats_in_group),
                 fg=DIM, bg=PANEL2, padx=4, pady=2,
                 font=("Segoe UI", 8)).pack(side="right", padx=1)
        flat_btn(hdr_row, "NONE", make_grp_none(all_cats_in_group),
                 fg=DIM, bg=PANEL2, padx=4, pady=2,
                 font=("Segoe UI", 8)).pack(side="right", padx=1)

        for sub_label, cat_keys in subgroups:
            has_sub = sub_label is not None
            if has_sub:
                sub_row = tk.Frame(body_frame, bg=PANEL)
                sub_row.pack(fill="x", pady=(4, 0))
                tk.Label(sub_row, text=f"  {sub_label}", bg=PANEL, fg=DIM2,
                         font=("Segoe UI", 8, "italic"),
                         anchor="w").pack(side="left", fill="x", expand=True)

                def make_sub_all(cks):
                    def fn():
                        for ck in cks:
                            if ck in self._fvars_map:   self._fvars_map[ck].set(True)
                            if ck in self._fvars_focus: self._fvars_focus[ck].set(True)
                        self._filter_changed()
                    return fn

                def make_sub_none(cks):
                    def fn():
                        for ck in cks:
                            if ck in self._fvars_map:   self._fvars_map[ck].set(False)
                            if ck in self._fvars_focus: self._fvars_focus[ck].set(False)
                        self._filter_changed()
                    return fn

                # Pack Map first → rightmost; NONE pack second → left of ALL
                flat_btn(sub_row, "NONE", make_sub_none(cat_keys),
                         fg=DIM, bg=PANEL, padx=3, pady=1,
                         font=("Segoe UI", 7)).pack(side="right", padx=(1, 0))
                flat_btn(sub_row, "ALL",  make_sub_all(cat_keys),
                         fg=DIM, bg=PANEL, padx=3, pady=1,
                         font=("Segoe UI", 7)).pack(side="right", padx=1)

            for cat in cat_keys:
                cfg = CATS.get(cat)
                if not cfg:
                    continue
                vm = tk.BooleanVar(value=(cat in self.visible_map))
                vf = tk.BooleanVar(value=(cat in self.visible_focus))
                self._fvars_map[cat]   = vm
                self._fvars_focus[cat] = vf

                row = tk.Frame(body_frame, bg=PANEL)
                row.pack(fill="x", pady=1)

                indent = "    " if has_sub else "  "
                tk.Label(row, bg=cfg["color"], width=2).pack(
                    side="left", padx=(4 if has_sub else 2, 4))
                tk.Label(row, text=f"{indent}{cfg['label']}", bg=PANEL, fg=TEXT,
                         font=("Segoe UI", 9), anchor="w").pack(
                             side="left", fill="x", expand=True)

                cnt = tk.Label(row, text="", bg=PANEL, fg=DIM2, font=("Segoe UI", 8))
                cnt.pack(side="right", padx=(0, 2))
                self._cnt_labels[cat] = cnt

                # Map checkbox → rightmost (Map panel is on the right)
                tk.Checkbutton(row, variable=vm, bg=PANEL, fg=DIM,
                               selectcolor=PANEL2, activebackground=PANEL,
                               bd=0, highlightthickness=0,
                               command=self._filter_changed).pack(side="right")
                # Focus checkbox → left of Map (Focus panel is on the left)
                tk.Checkbutton(row, variable=vf, bg=PANEL, fg=DIM,
                               selectcolor=PANEL2, activebackground=PANEL,
                               bd=0, highlightthickness=0,
                               command=self._filter_changed).pack(side="right")

    def _style_widgets(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure("TCombobox", fieldbackground=PANEL2, background=PANEL2,
                    foreground=TEXT, selectbackground=PANEL2, selectforeground=TEXT,
                    bordercolor=BDR2, arrowcolor=DIM, padding=(4, 4))

    # ── Focus panel ───────────────────────────────────────
    def _build_focus(self, p):
        hdr = tk.Frame(p, bg=PANEL2)
        hdr.pack(fill="x")
        self.focus_title = tk.Label(hdr, text="No module selected", bg=PANEL2,
                                    fg=ACCENT, font=("Segoe UI", 11, "bold"),
                                    padx=10, pady=8, anchor="w")
        self.focus_title.pack(fill="x")
        tk.Frame(p, bg=BORDER, height=1).pack(fill="x")
        self.focus_cv = tk.Canvas(p, bg=TILEBG, bd=0, highlightthickness=0)
        self.focus_cv.pack(fill="both", expand=True)
        self.focus_cv.bind("<Configure>", lambda e: self._draw_focus())
        self.focus_tip = tk.Label(p, text="", bg=PANEL2, fg=DIM,
                                   font=("Segoe UI", 9), padx=8, pady=4,
                                   wraplength=300, justify="left")
        self.focus_tip.pack(fill="x", side="bottom")

    # ── Map panel ─────────────────────────────────────────
    def _build_map(self, p):
        tb = tk.Frame(p, bg=PANEL2, pady=6)
        tb.pack(fill="x")

        btn_data = [
            ("\u2922  Fit",    "Fit to window  (F)", self._fit),
            ("\u2295  Zoom +", "Zoom in  (+)",        lambda: self._zoom_c(1.25)),
            ("\u2296  Zoom \u2212", "Zoom out  (\u2212)", lambda: self._zoom_c(0.80)),
        ]
        for icon, _tip, cmd in btn_data:
            btn = flat_btn(tb, icon, cmd, font=("Segoe UI", 10), padx=12, pady=4,
                           bg=BTN_BG)
            btn.pack(side="left", padx=(6, 0))

        tk.Frame(tb, bg=BDR2, width=1).pack(side="left", fill="y", padx=8)
        self.zlbl = tk.Label(tb, text="100%", bg=PANEL2, fg=DIM,
                             font=("Segoe UI", 9))
        self.zlbl.pack(side="left")

        # ── Scanner & tracker buttons (right side of toolbar) ─
        tk.Frame(tb, bg=BDR2, width=1).pack(side="right", fill="y", padx=6)

        # Track Player toggle
        self._track_lbl_var = tk.StringVar(value="\U0001F9ED  Track OFF")
        self._track_btn = tk.Button(
            tb, textvariable=self._track_lbl_var,
            command=self._toggle_tracker,
            bg=BTN_BG, fg=DIM, bd=0,
            font=("Segoe UI", 9), padx=10, pady=4,
            activebackground=BTN_HOV, activeforeground=TEXT,
            relief="flat", cursor="hand2",
        )
        self._track_btn.pack(side="right", padx=(0, 4))

        # Scan Map (V2) button
        flat_btn(tb, "\U0001F5FA  Scan Map (V2)", self._trigger_scan_v2,
                 font=("Segoe UI", 9), padx=10, pady=4,
                 bg=BTN_BG).pack(side="right", padx=(0, 4))

        # Scan Map button (original)
        flat_btn(tb, "\U0001F5FA  Scan Map", self._trigger_scan,
                 font=("Segoe UI", 9), padx=10, pady=4,
                 bg=BTN_BG).pack(side="right", padx=(0, 4))

        tk.Frame(p, bg=BORDER, height=1).pack(fill="x")

        self.mc = tk.Canvas(p, bg=BG, bd=0, highlightthickness=0, cursor="fleur")
        self.mc.pack(fill="both", expand=True)
        self.mc.bind("<ButtonPress-1>",   self._drag_start)
        self.mc.bind("<B1-Motion>",       self._drag_move)
        self.mc.bind("<ButtonRelease-1>", self._drag_end)
        self.mc.bind("<MouseWheel>",      self._wheel)
        self.mc.bind("<Button-4>",        self._wheel)
        self.mc.bind("<Button-5>",        self._wheel)
        self.mc.bind("<Configure>",       lambda e: self._draw_map())
        self.mc.bind("<Motion>",          self._hover)
        self._rebind_keys()

    def _rebind_keys(self):
        """Apply keybinds from settings.  Call after loading or changing keybinds."""
        kb = self.cfg.get("keybinds", DEFAULTS["keybinds"])

        def _bind_key(key_str, action):
            """Parse a user-friendly key string like 'Shift+M' or 'f' and bind it."""
            if not key_str:
                return
            parts  = key_str.split("+")
            mods   = "-".join(p.capitalize() for p in parts[:-1])
            k      = parts[-1]
            # Map common non-obvious names to tkinter event names
            name_map = {"=": "equal"}
            k = name_map.get(k.lower(), k)
            if mods:
                spec = f"<{mods}-KeyPress-{k}>"
            else:
                spec = f"<KeyPress-{k}>"
            try:
                self.bind(spec, lambda e, a=action: a())
                # Also bind the lowercase version for single letters
                if len(k) == 1 and k.isalpha() and k == k.upper():
                    self.bind(f"<KeyPress-{k.lower()}>", lambda e, a=action: a())
            except tk.TclError:
                pass

        # Unbind previous scan-map keys
        for ev in ("<Shift-KeyPress-M>", "<Shift-KeyPress-m>",
                   "<KeyPress-f>", "<KeyPress-F>",
                   "<KeyPress-plus>", "<KeyPress-minus>"):
            try:
                self.unbind(ev)
            except Exception:
                pass

        _bind_key(kb.get("scan_map", ""), self._trigger_scan)
        _bind_key(kb.get("fit",      ""), self._fit)
        _bind_key(kb.get("zoom_in",  ""), lambda: self._zoom_c(1.2))
        _bind_key(kb.get("zoom_out", ""), lambda: self._zoom_c(0.83))

    # ── Settings popup ────────────────────────────────────
    def _open_settings(self):
        if self._settings_win and self._settings_win.winfo_exists():
            self._settings_win.lift()
            self._settings_win.focus_force()
            return

        win = tk.Toplevel(self)
        self._settings_win = win
        win.title("Settings")
        win.configure(bg=PANEL)
        win.resizable(True, True)
        win.geometry("420x540")
        win.transient(self)

        tk.Label(win, text="\u2699  Settings", bg=PANEL, fg=ACCENT,
                 font=("Segoe UI", 13, "bold"), padx=16, pady=12).pack(anchor="w")
        tk.Frame(win, bg=BORDER, height=1).pack(fill="x")

        body = tk.Frame(win, bg=PANEL, padx=16, pady=12)
        body.pack(fill="both", expand=True)

        for lbl, attr, lo, hi, step in [
            ("Map tile size (px)",       "tile_px",           60,  400, 20),
            ("Focus tile size (px)",     "focus_tile_px",     200, 900, 50),
            ("Marker scale (map)",       "marker_scale",      0.2, 4.0, 0.1),
            ("Marker scale (focus)",     "focus_marker_scale",0.5, 6.0, 0.2),
        ]:
            r = tk.Frame(body, bg=PANEL)
            r.pack(fill="x", pady=5)
            tk.Label(r, text=lbl, bg=PANEL, fg=TEXT, font=("Segoe UI", 10),
                     anchor="w", width=26).pack(side="left")
            v = tk.DoubleVar(value=self.cfg[attr])
            sp = tk.Spinbox(r, from_=lo, to=hi, increment=step, textvariable=v,
                            width=7, bg=PANEL2, fg=TEXT, bd=0,
                            buttonbackground=PANEL2, font=("Segoe UI", 10),
                            command=lambda a=attr, vv=v: self._setting(a, vv))
            sp.pack(side="right")
            sp.bind("<Return>", lambda e, a=attr, vv=v: self._setting(a, vv))
            self._spins[attr] = v

        lr = tk.Frame(body, bg=PANEL)
        lr.pack(fill="x", pady=5)
        self.lblvar = tk.BooleanVar(value=self.cfg["show_labels"])
        tk.Checkbutton(lr, text="Show module names on map", variable=self.lblvar,
                       bg=PANEL, fg=TEXT, selectcolor=PANEL2,
                       activebackground=PANEL, activeforeground=TEXT,
                       font=("Segoe UI", 10),
                       command=self._toggle_labels).pack(anchor="w")

        tk.Frame(body, bg=BORDER, height=1).pack(fill="x", pady=8)

        # ── Keybind section ───────────────────────────────
        tk.Label(body, text="KEYBINDS", bg=PANEL, fg=DIM2,
                 font=("Segoe UI", 8), anchor="w").pack(fill="x", pady=(0, 4))
        tk.Label(body, text="Enter key name (e.g. 'f', 'Shift+M', 'plus').  "
                            "Leave blank to disable.",
                 bg=PANEL, fg=DIM, font=("Segoe UI", 8),
                 wraplength=380, justify="left", anchor="w").pack(fill="x")

        kb = self.cfg.setdefault("keybinds", dict(DEFAULTS["keybinds"]))
        _kb_labels = [
            ("Scan Map",   "scan_map"),
            ("Fit View",   "fit"),
            ("Zoom In",    "zoom_in"),
            ("Zoom Out",   "zoom_out"),
        ]
        _kb_vars: dict[str, tk.StringVar] = {}
        for lbl_txt, kb_key in _kb_labels:
            kr = tk.Frame(body, bg=PANEL)
            kr.pack(fill="x", pady=3)
            tk.Label(kr, text=lbl_txt, bg=PANEL, fg=TEXT,
                     font=("Segoe UI", 10), width=12, anchor="w").pack(side="left")
            kv = tk.StringVar(value=kb.get(kb_key, ""))
            _kb_vars[kb_key] = kv
            ent = tk.Entry(kr, textvariable=kv, bg=PANEL2, fg=TEXT,
                           insertbackground=TEXT, bd=0, font=("Segoe UI", 10),
                           width=16)
            ent.pack(side="left", padx=4)
            flat_btn(kr, "Clear", lambda kk=kb_key, vv=kv: (vv.set(""), None),
                     fg=DIM, bg=PANEL2, padx=6, pady=2,
                     font=("Segoe UI", 8)).pack(side="left", padx=2)

        def _save_keybinds():
            for kk, vv in _kb_vars.items():
                kb[kk] = vv.get().strip()
            self.cfg["keybinds"] = kb
            save_settings(self.cfg)
            self._rebind_keys()

        tk.Frame(body, bg=BORDER, height=1).pack(fill="x", pady=8)

        btn_row = tk.Frame(body, bg=PANEL)
        btn_row.pack(fill="x")
        flat_btn(btn_row, "Reset to Defaults", self._reset,
                 fg=DIM, bg=PANEL2, padx=10, pady=4).pack(side="left")
        flat_btn(btn_row, "Save Keybinds", _save_keybinds,
                 fg=TEXT, bg=BTN_BG, padx=10, pady=4).pack(side="left", padx=4)
        flat_btn(btn_row, "Close", win.destroy,
                 fg="#111", bg=ACCENT, padx=14, pady=4).pack(side="right")

    # ── Data ──────────────────────────────────────────────
    def _populate_maps(self):
        self._map_names = list(self.manifest.keys())
        self.map_combo["values"] = self._map_names

    def _cur_map_name(self):
        val = self.map_var.get().strip()
        return val if val else None

    def _reload_map(self):
        name = self._cur_map_name()
        if not name:
            return
        if not (RAW / f"{name}.json").exists():
            self.status.set(f"No data for {name}. Run dad_downloader.py first.")
            return
        _img_cache.clear()
        _icon_img_cache.clear()
        self.status.set(f"Loading {name}\u2026")
        self.update_idletasks()
        mode = self.mode_var.get()
        self.modules   = load_map(name, mode, self.manifest)
        self._cur_map  = name
        self.cfg["last_map"] = name
        self.cfg["mode"]     = mode
        self.focus_key       = None
        self.focus_title.config(text="No module selected")
        self.focus_tip.config(text="")

        self.mod_list.delete(0, "end")
        self._mk_order = sorted(self.modules,
                                key=lambda k: (self.modules[k]["row"],
                                               self.modules[k]["col"]))
        for mk in self._mk_order:
            self.mod_list.insert("end", self.modules[mk].get("label", mk))

        self._map_dirty = True
        self._map_composite = None
        self._player_map_pos = None
        self._update_counts()
        self._draw_map()
        self._fit()
        self._draw_focus()
        n = len(self.modules)
        self.status.set(f"{name}  \u2022  {n} modules  \u2022  "
                        f"{'Normal' if mode == 'N' else 'High Roller'}")

    def _on_mod_select(self, *_):
        sel = self.mod_list.curselection()
        if not sel or not hasattr(self, "_mk_order"):
            return
        idx = sel[0]
        if idx < len(self._mk_order):
            self.focus_key = self._mk_order[idx]
            # Highlight on big map; mark dirty so the highlight ring is rebuilt
            self._map_dirty = True
            self._draw_map()
            self._draw_focus()
            self.after(FOCUS_RENDER_DELAY_MS, self._draw_focus)

    # ── Filters ───────────────────────────────────────────
    def _filter_changed(self):
        self.visible_map   = {c for c, v in self._fvars_map.items()   if v.get()}
        self.visible_focus = {c for c, v in self._fvars_focus.items() if v.get()}
        self.cfg["visible_cats_map"]   = list(self.visible_map)
        self.cfg["visible_cats_focus"] = list(self.visible_focus)
        save_settings(self.cfg)
        self._map_dirty = True
        self._draw_map()
        self._draw_focus()

    def _all_on(self):
        for v in self._fvars_map.values():   v.set(True)
        for v in self._fvars_focus.values(): v.set(True)
        self._filter_changed()

    def _all_off(self):
        for v in self._fvars_map.values():   v.set(False)
        for v in self._fvars_focus.values(): v.set(False)
        self._filter_changed()

    def _update_counts(self):
        cnt = Counter(i["cat"] for m in self.modules.values() for i in m["items"])
        for cat, lbl in self._cnt_labels.items():
            n = cnt.get(cat, 0)
            lbl.config(text=str(n) if n else "")

    # ── Settings handlers ─────────────────────────────────
    def _setting(self, attr, var):
        try:
            self.cfg[attr] = var.get()
        except Exception:
            return
        _img_cache.clear()
        _icon_img_cache.clear()
        self._map_dirty = True
        save_settings(self.cfg)
        self._draw_map()
        if self.focus_key:
            self.after(FOCUS_RENDER_DELAY_MS, self._draw_focus)

    def _toggle_labels(self):
        self.cfg["show_labels"] = self.lblvar.get()
        save_settings(self.cfg)
        self._draw_map()

    def _reset(self):
        self.cfg = {**DEFAULTS, "keybinds": dict(DEFAULTS["keybinds"])}
        for a, v in self._spins.items():
            v.set(DEFAULTS[a])
        if hasattr(self, "lblvar"):
            self.lblvar.set(DEFAULTS["show_labels"])
        self.visible_map   = set(DEFAULT_VISIBLE)
        self.visible_focus = set(DEFAULT_VISIBLE)
        for c, v in self._fvars_map.items():   v.set(c in self.visible_map)
        for c, v in self._fvars_focus.items(): v.set(c in self.visible_focus)
        _img_cache.clear()
        _icon_img_cache.clear()
        self._map_dirty = True
        save_settings(self.cfg)
        self._rebind_keys()
        self._draw_map()
        self._draw_focus()

    # ── Rendering ─────────────────────────────────────────
    def _tile_px(self):
        return max(40, int(self.cfg.get("tile_px", 180)))

    def _mscale(self):
        return float(self.cfg.get("marker_scale", 1.0))

    # ── Static map composite ──────────────────────────────
    def _rebuild_map_composite(self):
        """Render every module tile into a single static PIL Image.

        This composite is rebuilt only when map content changes (filter/map
        reload/focus change).  Pan and zoom just reposition/resize this one
        image – avoiding per-tile re-render on every mouse drag.
        """
        if not self.modules:
            self._map_composite = None
            return
        tp = self._tile_px()
        ms = self._mscale()
        mc = max(m["col"] + m["span"] for m in self.modules.values())
        mr = max(m["row"] + m["span"] for m in self.modules.values())
        W  = mc * tp
        H  = mr * tp
        composite = Image.new("RGBA", (W, H), (15, 13, 11, 255))
        for mk, mod in self.modules.items():
            span   = mod["span"]
            tile_w = span * tp
            wx     = mod["col"] * tp
            wy     = mod["row"] * tp
            tile   = render_tile(self._cur_map, mk, mod, tp, self.visible_map, ms)
            if mk == self.focus_key:
                d = ImageDraw.Draw(tile)
                d.rectangle([0, 0, tile_w - 1, tile_w - 1],
                             outline=(200, 168, 75, 255), width=3)
            composite.paste(tile, (wx, wy))
        self._map_composite     = composite
        self._map_comp_params   = (tp, ms, self.focus_key)
        self._map_comp_zoom     = 0.0    # force re-scale on next draw
        self._map_comp_scaled   = None
        self._map_comp_tk       = None
        self._map_comp_id       = None
        self._map_dirty         = False

    def _draw_map(self, *_):
        c = self.mc
        if not self.modules:
            c.delete("all")
            self._tkimgs.clear()
            self._map_comp_id = None
            c.create_text(c.winfo_width() // 2, c.winfo_height() // 2,
                          text="No map data loaded.\nRun dad_downloader.py first.",
                          fill=DIM, font=("Segoe UI", 13), justify="center")
            return

        tp = self._tile_px()
        ms = self._mscale()

        # Rebuild composite when content changed
        cur_params = (tp, ms, self.focus_key)
        if self._map_dirty or self._map_composite is None or \
                self._map_comp_params != cur_params:
            self._rebuild_map_composite()

        if self._map_composite is None:
            return

        # Rescale if zoom changed
        if abs(self._map_comp_zoom - self._zoom) > 1e-6 or \
                self._map_comp_scaled is None:
            cw, ch   = self._map_composite.size
            sw       = max(1, int(cw * self._zoom))
            sh       = max(1, int(ch * self._zoom))
            method   = Image.NEAREST if self._zoom < 0.4 else Image.BILINEAR
            self._map_comp_scaled = self._map_composite.resize((sw, sh), method)
            self._map_comp_tk     = ImageTk.PhotoImage(self._map_comp_scaled)
            self._map_comp_zoom   = self._zoom
            self._map_comp_id     = None   # force canvas item recreation

        # (Re)create canvas item when the PhotoImage was replaced
        if self._map_comp_id is None:
            c.delete("all")
            self._tkimgs = [self._map_comp_tk]
            self._map_comp_id = c.create_image(
                self._panx, self._pany, anchor="nw",
                image=self._map_comp_tk, tags=("COMPOSITE",))
        else:
            # Just reposition
            c.coords(self._map_comp_id, self._panx, self._pany)

        # Draw module labels on top (fast text ops)
        c.delete("LABEL")
        show_lbl = self.cfg.get("show_labels", True)
        if show_lbl:
            for mk, mod in self.modules.items():
                span   = mod["span"]
                tile_w = span * tp * self._zoom
                wx     = mod["col"] * tp * self._zoom + self._panx
                wy     = mod["row"] * tp * self._zoom + self._pany
                if tile_w > 40:
                    fs = max(7, min(11, int(8 * self._zoom)))
                    c.create_text(
                        wx + tile_w // 2,
                        wy + tile_w - max(2, int(8 * self._zoom)),
                        text=mod.get("label", mk), fill="white",
                        font=("Segoe UI", fs), anchor="s", tags=("LABEL",))

        # Player overlay
        self._draw_player_overlay()
        self.zlbl.config(text=f"{int(self._zoom * 100)}%")

    def _draw_focus(self, *_):
        c = self.focus_cv
        c.delete("all")
        # Keep last 200 images to avoid memory growth
        self._tkimgs = self._tkimgs[-200:]
        if not self.focus_key or self.focus_key not in self.modules:
            c.create_text(10, 10, text="Select a module from the list \u2192",
                          fill=DIM, font=("Segoe UI", 10), anchor="nw")
            return
        mod  = self.modules[self.focus_key]
        cw   = max(1, c.winfo_width())
        ch   = max(1, c.winfo_height())
        span = mod["span"]
        fp   = max(80, min(cw // span, ch // span,
                           int(self.cfg.get("focus_tile_px", 500))))
        ms   = float(self.cfg.get("focus_marker_scale", 1.8))
        self.focus_title.config(text=mod.get("label", self.focus_key))
        img  = render_tile(self._cur_map, self.focus_key, mod, fp,
                           self.visible_focus, ms)
        W    = span * fp
        H    = span * fp
        scale = min(cw / W, ch / H)
        nw    = max(1, int(W * scale))
        nh    = max(1, int(H * scale))
        imgs  = img.resize((nw, nh), Image.LANCZOS)
        ti    = ImageTk.PhotoImage(imgs)
        self._tkimgs.append(ti)
        c.create_image(cw // 2, ch // 2, anchor="center", image=ti)
        vis = sum(1 for i in mod["items"] if i["cat"] in self.visible_focus)
        tot = len(mod["items"])
        self.focus_tip.config(
            text=f"{self.focus_key}  \u2022  {vis} markers shown  ({tot} total)")

    # ── Pan / zoom ────────────────────────────────────────
    def _fit(self, *_):
        if not self.modules:
            return
        tp = self._tile_px()
        mc = max(m["col"] + m["span"] for m in self.modules.values())
        mr = max(m["row"] + m["span"] for m in self.modules.values())
        ww = mc * tp
        wh = mr * tp
        cw = self.mc.winfo_width()  or 800
        ch = self.mc.winfo_height() or 600
        s  = min((cw - 20) / ww, (ch - 20) / wh, 2.0)
        self._zoom = max(0.05, s)
        self._panx = (cw - ww * self._zoom) / 2
        self._pany = (ch - wh * self._zoom) / 2
        self._draw_map()

    def _zoom_c(self, f):
        cw = self.mc.winfo_width()  or 800
        ch = self.mc.winfo_height() or 600
        self._zoom_at(f, cw / 2, ch / 2)

    def _zoom_at(self, f, cx, cy):
        nz = max(0.04, min(8.0, self._zoom * f))
        self._panx = cx - (cx - self._panx) * (nz / self._zoom)
        self._pany = cy - (cy - self._pany) * (nz / self._zoom)
        self._zoom = nz
        self._draw_map()

    def _drag_start(self, e):
        self._drag = (e.x, e.y, self._panx, self._pany)

    def _drag_move(self, e):
        if not self._drag:
            return
        dx = e.x - self._drag[0]
        dy = e.y - self._drag[1]
        new_panx = self._drag[2] + dx
        new_pany = self._drag[3] + dy
        # Incremental delta for moving canvas items
        ddx = new_panx - self._panx
        ddy = new_pany - self._pany
        self._panx = new_panx
        self._pany = new_pany
        # Move all canvas items together – no re-rendering, no resize
        self.mc.move("all", ddx, ddy)

    def _drag_end(self, e):
        self._drag = None

    def _wheel(self, e):
        f = 1.1 if (getattr(e, "delta", 0) > 0 or e.num == 4) else 0.91
        self._zoom_at(f, e.x, e.y)

    # ── Player position overlay ───────────────────────────
    def _draw_player_overlay(self):
        """Draw player arrow on the map canvas (non-blocking, fast)."""
        c = self.mc
        c.delete("PLAYER")
        if not self._player_map_pos or not self.modules:
            return
        tp = self._tile_px()
        wx_world, wy_world = self._player_map_pos
        # Find which module contains this world position
        cur_mod = None
        for mk, mod in self.modules.items():
            bb   = mod["bbox"]
            xmin, xmax = bb["xmin"], bb["xmax"]
            ymin, ymax = bb["ymin"], bb["ymax"]
            if xmin <= wx_world <= xmax and ymin <= wy_world <= ymax:
                cur_mod = mk
                span   = mod["span"]
                W      = span * tp
                ox     = mod["col"] * tp
                oy     = mod["row"] * tp
                xr     = (xmax - xmin) or 1
                yr     = (ymax - ymin) or 1
                cx     = ox + ((wx_world - xmin) / xr) * W
                cy     = oy + ((ymax - wy_world) / yr) * W
                break
        if cur_mod is None:
            return
        # Convert to canvas coords
        sx = cx * self._zoom + self._panx
        sy = cy * self._zoom + self._pany
        r  = max(6, int(10 * self._zoom))
        # Draw player circle
        c.create_oval(sx - r, sy - r, sx + r, sy + r,
                      fill="#3af", outline="white", width=2, tags=("PLAYER",))
        # Direction triangle
        ang = math.radians(self._player_angle)
        tip_x = sx + math.sin(ang)  * (r + 6)
        tip_y = sy - math.cos(ang)  * (r + 6)
        lx    = sx - math.cos(ang)  * (r * 0.5)
        ly    = sy - math.sin(ang)  * (r * 0.5)
        rx    = sx + math.cos(ang)  * (r * 0.5)
        ry    = sy + math.sin(ang)  * (r * 0.5)
        c.create_polygon(tip_x, tip_y, lx, ly, rx, ry,
                         fill="white", outline="#3af", width=1, tags=("PLAYER",))
        # Highlight the current module in the listbox
        if hasattr(self, "_mk_order") and cur_mod in self._mk_order:
            idx = self._mk_order.index(cur_mod)
            self.mod_list.selection_clear(0, "end")
            self.mod_list.selection_set(idx)
            self.mod_list.see(idx)

    def _update_player_pos(self, world_pos, angle_deg):
        """Called by MinimapTracker to push a new player position (thread-safe)."""
        self._player_map_pos = world_pos
        self._player_angle   = angle_deg
        # Schedule a lightweight redraw of just the player overlay
        self.after_idle(self._redraw_player_only)

    def _redraw_player_only(self):
        self.mc.delete("PLAYER")
        self._draw_player_overlay()

    # ── Map scanner (Shift+M) ─────────────────────────────
    def _trigger_scan(self):
        if not _HAVE_IMAGEGRAB and not _HAVE_MSS:
            self.status.set("[Scan] Screenshot not available – install Pillow[ImageGrab] or mss.")
            return
        map_name = self._cur_map_name()
        if not map_name:
            self.status.set("[Scan] Load a map first, then press Shift+M.")
            return
        self.status.set("\U0001F5FA  Scanning screen\u2026  (Shift+M)")
        self.update_idletasks()
        try:
            scanner = MapScanner(map_name, self.manifest, self.modules)
            result  = scanner.scan()
        except Exception as exc:
            self.status.set(f"[Scan] Error: {exc}")
            return
        if result is None:
            self.status.set("[Scan] Could not detect dungeon map on screen. "
                            "Open your in-game map first.")
            return
        # result = {"layout": {mk: (row, col, rotation)}, "map_name": str}
        self._apply_scan_result(result)

    def _apply_scan_result(self, result):
        """Apply scanner layout to the viewer."""
        layout   = result.get("layout", {})
        new_mods = {}
        for mk, (row, col, rot) in layout.items():
            if mk in self.modules:
                m        = dict(self.modules[mk])
                m["row"] = row
                m["col"] = col
                new_mods[mk] = m
        if not new_mods:
            self.status.set("[Scan] No matching modules found.")
            return
        self.modules   = new_mods
        self._map_dirty = True
        self.mod_list.delete(0, "end")
        self._mk_order = sorted(new_mods,
                                key=lambda k: (new_mods[k]["row"],
                                               new_mods[k]["col"]))
        for mk in self._mk_order:
            self.mod_list.insert("end", new_mods[mk].get("label", mk))
        self._update_counts()
        self._draw_map()
        self._fit()
        self.status.set(f"[Scan] Layout applied  \u2022  {len(new_mods)} modules matched.")

    # ── Map scanner V2 ────────────────────────────────────
    def _trigger_scan_v2(self):
        if not _HAVE_SCANNER_V2:
            self.status.set("[Scan V2] map_scanner_v2.py not found – "
                            "ensure it is in the same directory.")
            return
        if not (_HAVE_IMAGEGRAB or _HAVE_MSS):
            self.status.set("[Scan V2] Screenshot not available – "
                            "install Pillow[ImageGrab] or mss.")
            return
        map_name = self._cur_map_name()
        if not map_name:
            self.status.set("[Scan V2] Load a map first.")
            return
        self.status.set("\U0001F5FA  Scanning screen (V2)\u2026")
        self.update_idletasks()
        try:
            scanner = _MapScannerV2(map_name=map_name)
            result  = scanner.scan_screen()
        except Exception as exc:
            self.status.set(f"[Scan V2] Error: {exc}")
            return
        if not result.get("ok"):
            err = result.get("error", "Unknown error")
            self.status.set(f"[Scan V2] {err}")
            return
        if result.get("warning"):
            self.status.set(f"[Scan V2] Warning: {result['warning']}")
        self._apply_scan_result_v2(result)

    def _apply_scan_result_v2(self, result: dict):
        """Apply a V2 scanner result to the module layout.

        V2 layout structure: {(row, col): {"module": mk, "rot": rot_deg, "score": float}}.
        """
        layout   = result.get("layout", {})
        new_mods = {}
        for (row, col), info in layout.items():
            mk = info["module"]
            if mk in self.modules:
                m        = dict(self.modules[mk])
                m["row"] = row
                m["col"] = col
                m["rot"] = info.get("rot", 0)
                new_mods[mk] = m
        if not new_mods:
            self.status.set("[Scan V2] No modules matched.  "
                            "Check templates exist (run dad_downloader.py).")
            return
        self.modules    = new_mods
        self._map_dirty = True
        self.mod_list.delete(0, "end")
        self._mk_order = sorted(new_mods,
                                key=lambda k: (new_mods[k]["row"],
                                               new_mods[k]["col"]))
        for mk in self._mk_order:
            self.mod_list.insert("end", new_mods[mk].get("label", mk))
        self._update_counts()
        self._draw_map()
        self._fit()
        self.status.set(
            f"[Scan V2] Layout applied  \u2022  {len(new_mods)} modules matched."
        )

    # ── Minimap tracker ───────────────────────────────────
    def _toggle_tracker(self):
        if self._tracker and self._tracker.running:
            self._tracker.stop()
            self._tracker = None
            self._track_lbl_var.set("\U0001F9ED  Track OFF")
            self._track_btn.config(fg=DIM)
            self.mc.delete("PLAYER")
            self.status.set("Player tracking stopped.")
        else:
            if not (_HAVE_IMAGEGRAB or _HAVE_MSS):
                self.status.set("[Track] Install mss or Pillow[ImageGrab] for tracking.")
                return
            # Prefer calibrated settings from the debug tool if available
            if _HAVE_CURSOR_DETECT:
                _mm_settings = _cd.load_minimap_settings()
                mm_cfg = dict(_mm_settings["roi"])
            else:
                mm_cfg = {
                    "left":   self.cfg.get("minimap_left",   1700),
                    "top":    self.cfg.get("minimap_top",    860),
                    "width":  self.cfg.get("minimap_width",  200),
                    "height": self.cfg.get("minimap_height", 200),
                }
                _mm_settings = None
            self._tracker = MinimapTracker(
                minimap_region=mm_cfg,
                callback=self._update_player_pos,
                modules=self.modules,
                mm_settings=_mm_settings,
            )
            self._tracker.start()
            self._track_lbl_var.set("\U0001F9ED  Track ON")
            self._track_btn.config(fg=ACCENT)
            self.status.set("Player tracking active  \u2022  move on minimap to update.")

    def _on_close(self):
        if self._tracker:
            self._tracker.stop()
        save_settings(self.cfg)
        self.destroy()

    # ── Hover tooltip ─────────────────────────────────────
    def _hover(self, e):
        if not self.modules:
            return
        tp     = self._tile_px()
        ms     = self._mscale()
        wx     = (e.x - self._panx) / self._zoom
        wy     = (e.y - self._pany) / self._zoom
        best_d = float("inf")
        best   = None
        for mk, mod in self.modules.items():
            span = mod["span"]
            W    = span * tp
            ox   = mod["col"] * tp
            oy   = mod["row"] * tp
            bb   = mod["bbox"]
            xr   = bb["xmax"] - bb["xmin"] or 1
            yr   = bb["ymax"] - bb["ymin"] or 1
            for item in mod["items"]:
                cfg = CATS.get(item["cat"])
                if not cfg or item["cat"] not in self.visible_map:
                    continue
                px = ox + ((item["x"] - bb["xmin"]) / xr) * W
                py = oy + ((bb["ymax"] - item["y"]) / yr) * W
                hit = max(6, cfg["r"] * ms + 2)
                d   = math.hypot(wx - px, wy - py)
                if d < hit and d < best_d:
                    best_d = d
                    best   = (item, cfg)
        if best:
            item, cfg = best
            nm = item.get("name") or item.get("id", "?")
            self._show_tt(e.x_root + 14, e.y_root + 12, f"{nm}\n{cfg['label']}")
        else:
            self._hide_tt()

    def _show_tt(self, x, y, txt):
        self._hide_tt()
        tw = tk.Toplevel(self)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.configure(bg=BDR2)
        tk.Label(tw, text=txt, bg="#0e0d14", fg=TEXT, font=("Segoe UI", 9),
                 padx=8, pady=5, justify="left").pack()
        self._tt_win = tw

    def _hide_tt(self):
        if self._tt_win:
            self._tt_win.destroy()
            self._tt_win = None

    def destroy(self):
        if self._tracker:
            self._tracker.stop()
        save_settings(self.cfg)
        super().destroy()


# ── Map Scanner ───────────────────────────────────────────
class MapScanner:
    """Capture the screen and match visible dungeon modules using template matching.

    The algorithm:
    1. Take a screenshot of the full screen (or configured region).
    2. Locate the dungeon-map overlay by looking for the characteristic
       dark area surrounded by UI chrome.
    3. For every cell in the module grid, extract a patch and compare it
       (using normalised mean-squared-error) against all four rotations of
       every known module PNG.  The best match wins.
    4. Return a ``layout`` dict: {module_key: (row, col, rotation_deg)}.

    Requires either ``PIL.ImageGrab`` (Windows/macOS) or ``mss``
    (cross-platform).  Works best with ``numpy`` installed.
    """

    # Dungeon-map detection: look for large dark rectangle
    # These heuristics are tuned for 1080p – scale with screen size.
    _MAP_DARK_THRESHOLD  = 50   # max average brightness of map background
    _MATCH_THRESHOLD     = 0.35  # max normalised MSE to accept a match

    def __init__(self, map_name: str, manifest: dict, current_modules: dict):
        self.map_name        = map_name
        self.manifest        = manifest
        self.current_modules = current_modules

    # ── Screenshot helpers ────────────────────────────────
    @staticmethod
    def _grab_screen() -> "Image.Image | None":
        """Return a full-screen RGB screenshot, or None on failure."""
        if _HAVE_MSS:
            try:
                with _mss_mod.mss() as sct:
                    monitor = sct.monitors[1]   # primary monitor
                    raw     = sct.grab(monitor)
                    return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            except Exception:
                pass
        if _HAVE_IMAGEGRAB:
            try:
                return ImageGrab.grab()
            except Exception:
                pass
        return None

    # ── Dungeon-map area detection ────────────────────────
    @staticmethod
    def _find_map_area(screen: "Image.Image") -> "tuple[int,int,int,int] | None":
        """Return (x, y, w, h) of the dungeon-map overlay, or None.

        Scans horizontal strips for a large region that is predominantly dark
        (i.e. the dungeon-map background).  The dungeon map in Dark and Darker
        occupies roughly the center of the screen as a dark square/rectangle.
        """
        sw, sh = screen.size
        # Sample the centre 80 % of the screen to skip edge chrome
        x0, y0 = int(sw * 0.1), int(sh * 0.1)
        x1, y1 = int(sw * 0.9), int(sh * 0.9)
        region  = screen.crop((x0, y0, x1, y1)).convert("L")

        rw, rh = region.size
        # Try square regions from the largest downwards
        best = None
        for size_frac in (0.75, 0.65, 0.55, 0.45):
            side = int(min(rw, rh) * size_frac)
            cx   = rw // 2 - side // 2
            cy   = rh // 2 - side // 2
            patch = region.crop((cx, cy, cx + side, cy + side))
            avg   = sum(patch.getdata()) / (side * side)
            if avg < MapScanner._MAP_DARK_THRESHOLD:
                best = (x0 + cx, y0 + cy, side, side)
                break
        return best

    # ── Module template loading ───────────────────────────
    def _load_module_templates(self, patch_size: int
                               ) -> "dict[str, list[Image.Image]]":
        """Load all module PNGs for the current map at *patch_size* pixels.
        Returns {mod_key: [rot0, rot90, rot180, rot270]}.
        """
        templates: dict = {}
        mod_dir = MODULES / self.map_name
        if not mod_dir.is_dir():
            return templates
        for mk in self.current_modules:
            p = mod_dir / f"{mk}.png"
            if not (p.exists() and _is_png_file(p)):
                continue
            try:
                base = Image.open(p).convert("L").resize(
                    (patch_size, patch_size), Image.LANCZOS)
                rotations = [
                    base,
                    base.rotate(90,  expand=True).resize((patch_size, patch_size), Image.LANCZOS),
                    base.rotate(180, expand=True).resize((patch_size, patch_size), Image.LANCZOS),
                    base.rotate(270, expand=True).resize((patch_size, patch_size), Image.LANCZOS),
                ]
                templates[mk] = rotations
            except Exception:
                pass
        return templates

    # ── Normalised MSE comparison ─────────────────────────
    @staticmethod
    def _nmse(a: "Image.Image", b: "Image.Image") -> float:
        """Normalised mean-squared error between two greyscale images [0..1]."""
        if _HAVE_NUMPY:
            aa = np.asarray(a, dtype=np.float32) / 255.0
            bb = np.asarray(b, dtype=np.float32) / 255.0
            return float(np.mean((aa - bb) ** 2))
        # Pure-PIL fallback (slower)
        da = list(a.getdata())
        db = list(b.getdata())
        n  = len(da)
        if n == 0:
            return 1.0
        return sum((x - y) ** 2 for x, y in zip(da, db)) / (n * 255.0 ** 2)

    # ── Main scan entry point ─────────────────────────────
    def scan(self) -> "dict | None":
        """Capture screen → detect map area → match modules → return layout."""
        screen = self._grab_screen()
        if screen is None:
            return None

        area = self._find_map_area(screen)
        if area is None:
            return None

        ax, ay, aw, ah = area
        map_img = screen.crop((ax, ay, ax + aw, ay + ah)).convert("L")

        # Determine grid dimensions from known module layout
        if not self.current_modules:
            return None
        max_col  = max(m["col"] + m["span"] for m in self.current_modules.values())
        max_row  = max(m["row"] + m["span"] for m in self.current_modules.values())
        patch_px = max(32, min(aw // max(max_col, 1), ah // max(max_row, 1)))

        templates = self._load_module_templates(patch_px)
        if not templates:
            return None

        layout: dict = {}
        for mk, mod in self.current_modules.items():
            if mk not in templates:
                continue
            row, col = mod["row"], mod["col"]
            span     = mod["span"]
            # Extract the corresponding patch from the screenshot
            px0 = int(col * patch_px)
            py0 = int(row * patch_px)
            px1 = px0 + int(span * patch_px)
            py1 = py0 + int(span * patch_px)
            if px1 > map_img.width or py1 > map_img.height:
                continue
            patch = map_img.crop((px0, py0, px1, py1)).resize(
                (patch_px, patch_px), Image.LANCZOS)

            best_err = float("inf")
            best_rot = 0
            for rot_idx, tmpl in enumerate(templates[mk]):
                err = self._nmse(patch, tmpl)
                if err < best_err:
                    best_err = err
                    best_rot = rot_idx * 90

            if best_err < self._MATCH_THRESHOLD:
                layout[mk] = (row, col, best_rot)

        if not layout:
            return None
        return {"layout": layout, "map_name": self.map_name}


# ── Minimap Player Tracker ────────────────────────────────
class MinimapTracker:
    """Lightweight thread that captures a small screen region (the minimap)
    and detects the player's position and facing direction.

    Detection strategy
    ------------------
    * Capture *only* the minimap region (bottom-right, configurable).
      This is the smallest possible screenshot area – minimal CPU/memory cost.
    * If cursor_detect is available (i.e. minimap_tracker.py calibration tool
      has been run and settings saved), use its robust HSV green-dot detection
      and circle-based direction algorithm.
    * Otherwise fall back to the legacy brightness-threshold method.
    * Map minimap position to world coordinates via the known module bboxes.

    The tracker does *not* inject into the game process; it uses only passive
    OS-level screen capture (PIL ImageGrab or mss).  This is safe with Iron
    Shield and any other passive-read anticheat.
    """

    _INTERVAL_S     = 0.12    # ~8 FPS – lightweight
    _BRIGHT_THRESH  = 200     # legacy: pixel brightness to count as "player"
    _MIN_CLUSTER    = 4       # legacy: minimum bright pixels to accept

    def __init__(self, minimap_region: dict, callback, modules: dict,
                 mm_settings: "dict | None" = None):
        """
        Parameters
        ----------
        minimap_region : dict with keys left, top, width, height (screen pixels)
        callback       : callable(world_pos, angle_deg) called on main thread
        modules        : current map modules dict
        mm_settings    : optional full minimap settings dict from cursor_detect
                         (loaded from data/minimap_settings.json). When provided
                         and cursor_detect is importable, enables robust tracking.
        """
        self._region      = minimap_region
        self._callback    = callback
        self._modules     = modules
        self._mm_settings = mm_settings
        self._thread: "threading.Thread | None" = None
        self._stop_evt    = threading.Event()
        self.running      = False
        # EMA state for smoothing
        self._prev_center:  "tuple | None" = None
        self._prev_heading: "float | None" = None
        # Precompute minimap-to-world mapping once
        self._build_world_bbox()

    def _build_world_bbox(self):
        """Find the overall world bounding-box from all module bboxes."""
        if not self._modules:
            self._world_bbox = None
            return
        xmin = min(m["bbox"]["xmin"] for m in self._modules.values())
        xmax = max(m["bbox"]["xmax"] for m in self._modules.values())
        ymin = min(m["bbox"]["ymin"] for m in self._modules.values())
        ymax = max(m["bbox"]["ymax"] for m in self._modules.values())
        self._world_bbox = (xmin, ymin, xmax, ymax)

    # ── Screen capture ────────────────────────────────────
    def _capture_minimap(self) -> "Image.Image | None":
        r = self._region
        box = (r["left"], r["top"],
               r["left"] + r["width"], r["top"] + r["height"])
        if _HAVE_MSS:
            try:
                with _mss_mod.mss() as sct:
                    raw = sct.grab({
                        "left": r["left"], "top": r["top"],
                        "width": r["width"], "height": r["height"],
                    })
                    return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            except Exception:
                pass
        if _HAVE_IMAGEGRAB:
            try:
                return ImageGrab.grab(bbox=box)
            except Exception:
                pass
        return None

    # ── Player detection ──────────────────────────────────
    @staticmethod
    def _find_player(minimap_gray: "Image.Image"
                     ) -> "tuple[float, float, float] | None":
        """Return (rel_x, rel_y, angle_deg) in [0..1] minimap coords, or None.

        Uses a fast brightness threshold on the greyscale minimap.  The player
        arrow is the brightest object; its centroid gives position and the
        principal axis gives facing direction.
        """
        w, h   = minimap_gray.size
        pixels = list(minimap_gray.getdata())
        bright = [(i % w, i // w) for i, v in enumerate(pixels)
                  if v >= MinimapTracker._BRIGHT_THRESH]
        if len(bright) < MinimapTracker._MIN_CLUSTER:
            return None
        # Centroid
        cx = sum(x for x, _ in bright) / len(bright)
        cy = sum(y for _, y in bright) / len(bright)
        # Principal axis via covariance (fast with pure Python)
        mx = cx; my = cy
        sxx = sum((x - mx) ** 2 for x, _ in bright)
        syy = sum((y - my) ** 2 for _, y in bright)
        sxy = sum((x - mx) * (y - my) for x, y in bright)
        angle_rad = 0.5 * math.atan2(2 * sxy, sxx - syy) if (sxx + syy) else 0.0
        return (cx / w, cy / h, math.degrees(angle_rad))

    # ── World position mapping ────────────────────────────
    def _minimap_to_world(self, rel_x: float, rel_y: float
                          ) -> "tuple[float, float] | None":
        """Map minimap-relative [0..1] coords to game-world coords."""
        if not self._world_bbox:
            return None
        xmin, ymin, xmax, ymax = self._world_bbox
        wx = xmin + rel_x * (xmax - xmin)
        # Y is flipped (screen-space vs world-space)
        wy = ymax - rel_y * (ymax - ymin)
        return (wx, wy)

    # ── Thread loop ───────────────────────────────────────
    def _loop(self):
        while not self._stop_evt.is_set():
            t0 = time.monotonic()
            try:
                mm_img = self._capture_minimap()
                if mm_img is not None:
                    if _HAVE_CURSOR_DETECT and self._mm_settings is not None:
                        self._loop_robust(mm_img)
                    else:
                        self._loop_legacy(mm_img)
            except Exception:
                pass
            elapsed = time.monotonic() - t0
            self._stop_evt.wait(max(0.0, self._INTERVAL_S - elapsed))

    def _loop_robust(self, mm_img):
        """Detection using cursor_detect (HSV green dot + circle direction)."""
        s = self._mm_settings
        gd = _cd.find_green_dot(mm_img, s.get("green_dot"))
        if not gd or gd.get("center") is None:
            return
        center = gd["center"]

        dir_res = _cd.find_direction_circles(
            mm_img, center,
            params_outline=s.get("outline"),
            params_dir=s.get("direction"),
        )
        heading = None
        if dir_res:
            heading = dir_res.get("heading")

        sm = s.get("smoothing", _cd.DEFAULT_MINIMAP_SETTINGS["smoothing"])
        center  = _cd.smooth_pos(center, self._prev_center,
                                  alpha=sm.get("pivot_alpha", 0.5))
        heading = _cd.smooth_angle(heading, self._prev_heading,
                                    alpha=sm.get("heading_alpha", 0.4),
                                    max_delta=sm.get("max_heading_delta", 60.0))
        self._prev_center  = center
        self._prev_heading = heading

        if center is not None:
            iw, ih = mm_img.size
            rx = center[0] / max(1, iw)
            ry = center[1] / max(1, ih)
            world_pos = self._minimap_to_world(rx, ry)
            if world_pos:
                self._callback(world_pos, heading or 0.0)

    def _loop_legacy(self, mm_img):
        """Fallback detection: brightness threshold on greyscale image."""
        gray   = mm_img.convert("L")
        result = self._find_player(gray)
        if result is not None:
            rx, ry, ang = result
            world_pos   = self._minimap_to_world(rx, ry)
            if world_pos:
                self._callback(world_pos, ang)

    def start(self):
        self._stop_evt.clear()
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="MinimapTracker")
        self._thread.start()

    def stop(self):
        self._stop_evt.set()
        self.running = False
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None


if __name__ == "__main__":
    App().mainloop()
