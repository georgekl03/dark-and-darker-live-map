#!/usr/bin/env python3
"""
Dark and Darker – Map Viewer (Desktop)
Run:  python map_viewer.py
Requires: pip install Pillow
"""

import json, math, sys, tkinter as tk
from tkinter import ttk
from pathlib import Path
from collections import Counter
try:
    from PIL import Image, ImageDraw, ImageTk
except ImportError:
    print("[!] Pillow not installed.  Run:  pip install Pillow")
    sys.exit(1)

ROOT    = Path(__file__).parent
DATA    = ROOT / "data"
RAW     = DATA / "raw"
MODULES = DATA / "modules"

# ── Palette ───────────────────────────────────────────────
BG      = "#0f0d0b"
PANEL   = "#16151a"
PANEL2  = "#1e1d24"
BORDER  = "#2c2b36"
BDR2    = "#3a3948"
ACCENT  = "#c8a84b"
TEXT    = "#dddad4"
DIM     = "#78767a"
DIM2    = "#464450"
TILEBG  = "#131320"
BTN_BG  = "#252430"
BTN_HOV = "#353445"

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


def render_tile(map_name, mod_key, mod, tile_px, visible, mscale):
    span = mod["span"]
    W = H = span * tile_px
    img  = get_tile_img(map_name, mod_key, W).copy()
    draw = ImageDraw.Draw(img)
    bb   = mod["bbox"]
    xr   = bb["xmax"] - bb["xmin"] or 1
    yr   = bb["ymax"] - bb["ymin"] or 1
    for item in mod["items"]:
        cat = item["cat"]
        cfg = CATS.get(cat)
        if not cfg or cat not in visible:
            continue
        px2 = ((item["x"] - bb["xmin"]) / xr) * W
        py2 = ((bb["ymax"] - item["y"]) / yr) * H
        r   = max(2, int(cfg["r"] * mscale))
        rc  = hex_rgb(cfg["color"])
        if cfg["ring"]:
            rr = r + max(2, int(4 * mscale))
            draw.ellipse([px2-rr, py2-rr, px2+rr, py2+rr],
                         outline=(*rc, 90), width=max(1, int(1.5*mscale)))
        draw.ellipse([px2-r, py2-r, px2+r, py2+r],
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
        self._build()
        self._populate_maps()
        self.map_var.set("")
        self.status.set("Select a map to load.")

    # ── Build UI ──────────────────────────────────────────
    def _build(self):
        pw = tk.PanedWindow(self, orient="horizontal", bg=BG,
                            sashwidth=4, sashrelief="flat", sashpad=0)
        pw.pack(fill="both", expand=True)

        left = tk.Frame(pw, bg=PANEL, width=280)
        left.pack_propagate(False)
        pw.add(left, minsize=220)
        self._build_sidebar(left)

        right = tk.Frame(pw, bg=BG)
        pw.add(right, minsize=600)
        rpw = tk.PanedWindow(right, orient="horizontal", bg=BG,
                             sashwidth=4, sashrelief="flat", sashpad=0)
        rpw.pack(fill="both", expand=True)

        focus_f = tk.Frame(rpw, bg=PANEL2, width=320)
        focus_f.pack_propagate(False)
        rpw.add(focus_f, minsize=200)
        self._build_focus(focus_f)

        map_f = tk.Frame(rpw, bg=BG)
        rpw.add(map_f, minsize=400)
        self._build_map(map_f)

    # ── Sidebar ───────────────────────────────────────────
    def _build_sidebar(self, p):
        hdr = tk.Frame(p, bg=PANEL2, pady=8)
        hdr.pack(fill="x")
        tk.Label(hdr, text="\u2694  D&D Map Viewer", bg=PANEL2, fg=ACCENT,
                 font=("Segoe UI", 12, "bold"), padx=12).pack(anchor="w")
        tk.Label(hdr, text="darkanddarkertracker.com  \u2022  local data",
                 bg=PANEL2, fg=DIM, font=("Segoe UI", 9), padx=12).pack(anchor="w")
        tk.Frame(p, bg=BORDER, height=1).pack(fill="x")

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
                                   height=10, exportselection=False)
        self.mod_list.pack(fill="x", padx=8, pady=4)
        self.mod_list.bind("<<ListboxSelect>>", self._on_mod_select)

        # Loot Filters
        sec("Loot Filters")
        # Column header row
        hrow = tk.Frame(b, bg=PANEL)
        hrow.pack(fill="x", padx=8, pady=(2, 0))
        tk.Label(hrow, text="", bg=PANEL, width=20, anchor="w").pack(side="left")
        tk.Label(hrow, text="Map", bg=PANEL, fg=DIM,
                 font=("Segoe UI", 8), width=4).pack(side="left")
        tk.Label(hrow, text="Focus", bg=PANEL, fg=DIM,
                 font=("Segoe UI", 8), width=5).pack(side="left")

        for gname, subgroups in GROUPS_CONFIG:
            all_cats_in_group = [ck for _, cks in subgroups for ck in cks]
            if not all_cats_in_group:
                continue
            self._build_filter_group(b, gname, subgroups, all_cats_in_group)

        self.after(100, self._sf.bind_all_children)

        # Bottom bar: status + settings gear
        tk.Frame(p, bg=BORDER, height=1).pack(fill="x")
        bot = tk.Frame(p, bg=PANEL2)
        bot.pack(fill="x")
        self.status = tk.StringVar(value="Ready")
        tk.Label(bot, textvariable=self.status, bg=PANEL2, fg=DIM,
                 font=("Segoe UI", 9), padx=10, pady=5,
                 anchor="w").pack(side="left", fill="x", expand=True)
        tk.Button(bot, text="\u2699", bg=PANEL2, fg=ACCENT, bd=0,
                  font=("Segoe UI", 13), padx=8, pady=3,
                  activebackground=BDR2, activeforeground=ACCENT,
                  relief="flat", cursor="hand2",
                  command=self._open_settings).pack(side="right")

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

                flat_btn(sub_row, "ALL",  make_sub_all(cat_keys),
                         fg=DIM, bg=PANEL, padx=3, pady=1,
                         font=("Segoe UI", 7)).pack(side="right", padx=1)
                flat_btn(sub_row, "NONE", make_sub_none(cat_keys),
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

                # Focus checkbox (rightmost)
                tk.Checkbutton(row, variable=vf, bg=PANEL, fg=DIM,
                               selectcolor=PANEL2, activebackground=PANEL,
                               bd=0, highlightthickness=0,
                               command=self._filter_changed).pack(side="right")
                # Map checkbox
                tk.Checkbutton(row, variable=vm, bg=PANEL, fg=DIM,
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
        self.bind("<f>", lambda e: self._fit())
        self.bind("<F>", lambda e: self._fit())
        self.bind("<plus>",  lambda e: self._zoom_c(1.2))
        self.bind("<minus>", lambda e: self._zoom_c(0.83))

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
        win.resizable(False, False)
        win.geometry("400x360")
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

        btn_row = tk.Frame(body, bg=PANEL)
        btn_row.pack(fill="x")
        flat_btn(btn_row, "Reset to Defaults", self._reset,
                 fg=DIM, bg=PANEL2, padx=10, pady=4).pack(side="left")
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
            # Highlight on big map WITHOUT re-centering it
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
        save_settings(self.cfg)
        self._draw_map()
        if self.focus_key:
            self.after(FOCUS_RENDER_DELAY_MS, self._draw_focus)

    def _toggle_labels(self):
        self.cfg["show_labels"] = self.lblvar.get()
        save_settings(self.cfg)
        self._draw_map()

    def _reset(self):
        self.cfg = {**DEFAULTS}
        for a, v in self._spins.items():
            v.set(DEFAULTS[a])
        if hasattr(self, "lblvar"):
            self.lblvar.set(DEFAULTS["show_labels"])
        self.visible_map   = set(DEFAULT_VISIBLE)
        self.visible_focus = set(DEFAULT_VISIBLE)
        for c, v in self._fvars_map.items():   v.set(c in self.visible_map)
        for c, v in self._fvars_focus.items(): v.set(c in self.visible_focus)
        _img_cache.clear()
        save_settings(self.cfg)
        self._draw_map()
        self._draw_focus()

    # ── Rendering ─────────────────────────────────────────
    def _tile_px(self):
        return max(40, int(self.cfg.get("tile_px", 180)))

    def _mscale(self):
        return float(self.cfg.get("marker_scale", 1.0))

    def _draw_map(self, *_):
        c = self.mc
        c.delete("all")
        self._tkimgs.clear()
        if not self.modules:
            c.create_text(c.winfo_width() // 2, c.winfo_height() // 2,
                          text="No map data loaded.\nRun dad_downloader.py first.",
                          fill=DIM, font=("Segoe UI", 13), justify="center")
            return
        tp       = self._tile_px()
        ms       = self._mscale()
        show_lbl = self.cfg.get("show_labels", True)
        for mk, mod in self.modules.items():
            span = mod["span"]
            W    = span * tp
            wx   = mod["col"] * tp
            wy   = mod["row"] * tp
            img  = render_tile(self._cur_map, mk, mod, tp, self.visible_map, ms)
            sw   = max(1, int(W * self._zoom))
            sh   = sw
            rmethod = Image.NEAREST if self._zoom < 0.4 else Image.BILINEAR
            imgs = img.resize((sw, sh), rmethod)
            if mk == self.focus_key:
                d = ImageDraw.Draw(imgs)
                d.rectangle([0, 0, sw-1, sh-1], outline=(200, 168, 75, 255), width=3)
            ti = ImageTk.PhotoImage(imgs)
            self._tkimgs.append(ti)
            cx = wx * self._zoom + self._panx
            cy = wy * self._zoom + self._pany
            c.create_image(cx, cy, anchor="nw", image=ti, tags=(f"T:{mk}",))
            if show_lbl and sw > 40:
                fs = max(7, min(11, int(8 * self._zoom)))
                c.create_text(cx + sw // 2, cy + sh - max(2, int(8 * self._zoom)),
                              text=mod.get("label", mk), fill="white",
                              font=("Segoe UI", fs), anchor="s")
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
        self._panx = self._drag[2] + dx
        self._pany = self._drag[3] + dy
        self._draw_map()

    def _drag_end(self, e):
        self._drag = None

    def _wheel(self, e):
        f = 1.1 if (getattr(e, "delta", 0) > 0 or e.num == 4) else 0.91
        self._zoom_at(f, e.x, e.y)

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
        save_settings(self.cfg)
        super().destroy()


if __name__ == "__main__":
    App().mainloop()
