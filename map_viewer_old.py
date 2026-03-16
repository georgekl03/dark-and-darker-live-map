#!/usr/bin/env python3
"""
Dark and Darker Map Viewer
Run: python map_viewer.py
Opens http://localhost:7373 automatically in your browser.
No extra dependencies - uses only Python standard library.
"""

import json
import math
import threading
import webbrowser
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

ROOT    = Path(__file__).parent
DATA    = ROOT / "data"
RAW     = DATA / "raw"
MODULES = DATA / "modules"
HTML    = ROOT / "viewer_html"
PORT    = 7373

# ── Known layouts (pixel-perfect from the website's Leaflet canvas) ──────────
# Each entry: module_key -> (col, row, span)   span=2 means 2×2 tile
CRYPT_LAYOUT = {
    "TreasureRoom_01":          (0,0,1), "UndergroundAltar":       (1,0,1),
    "SingleLogBridge":          (0,1,1), "SkeletonPit":            (1,1,1),
    "Storeroom":                (2,1,1), "Swamp":                  (3,1,1),
    "TheCage":                  (4,1,1), "TheMiniWheel":           (5,1,1),
    "ThePit":                   (6,1,1), "Tomb_Center":            (7,1,1),
    "LowPyramid":               (0,2,1), "Maze":                   (1,2,1),
    "MimicRoom":                (2,2,1), "OldTomb":                (3,2,1),
    "OssuaryEdge":              (4,2,1), "Prison_01":              (5,2,1),
    "Sanctum":                  (6,2,1), "Sewers":                 (7,2,1),
    "EightToOne_01":            (0,3,1), "EightToOne_02":          (1,3,1),
    "FishingGround":            (2,3,1), "FourWayConnect":         (3,3,1),
    "GuardPost":                (4,3,1), "Hallways":               (5,3,1),
    "HBridge":                  (6,3,1), "HighPriestOssuary":      (7,3,1),
    "Crypt_Dungeon":            (0,4,1), "Crypt_FourRooms":        (1,4,1),
    "Crypt_GreatWalkway":       (2,4,1), "Crypt_LargeRoomPit":     (3,4,1),
    "Crypt_Ramparts":           (4,4,1), "Crypt_Vault":            (5,4,1),
    "DarkMagicLibrary_Center":  (6,4,1), "DeathHall":              (7,4,1),
    "Connector_Trap_02":        (0,5,1), "CorridorCrypt":          (1,5,1),
    "CorridorofDarkPriests":    (2,5,1), "CrossRoad":              (3,5,1),
    "Crypt_AltarRoomAB":        (4,5,1), "Crypt_Atrium":           (5,5,1),
    "Crypt_Chapel":             (6,5,1), "Crypt_DarkRitualRoom_01":(7,5,1),
    "CenterTower":              (0,6,2),                            # 2×2!
    "Cemetery_03":              (2,6,1), "Cistern":                (3,6,1),
    "CliffBridge":              (4,6,1), "ComplexHall":            (5,6,1),
    "Connector_01":             (6,6,1), "Connector_Trap_01":      (7,6,1),
    "AdmirerRoom":              (2,7,1), "Armory":                 (3,7,1),
    "Barracks":                 (4,7,1), "Catacomb":               (5,7,1),
    "Cemetery_01":              (6,7,1), "Cemetery_02":            (7,7,1),
}

KNOWN_LAYOUTS = {"Crypt": CRYPT_LAYOUT}


def auto_layout(keys: list) -> dict:
    cols = max(1, math.ceil(math.sqrt(len(keys))))
    return {k: (i % cols, i // cols, 1) for i, k in enumerate(keys)}


def get_layout(map_name: str, manifest: dict) -> dict:
    if map_name in KNOWN_LAYOUTS:
        return KNOWN_LAYOUTS[map_name]
    return auto_layout(manifest.get(map_name, {}).get("moduleKeys", []))


PNG_SIG = b"\x89PNG\r\n\x1a\n"

def _is_valid_png(path: Path) -> bool:
    """Return True only if *path* starts with the 8-byte PNG signature."""
    try:
        with open(path, "rb") as f:
            return f.read(8) == PNG_SIG
    except Exception:
        return False


# ── Data API ──────────────────────────────────────────────
def load_manifest() -> dict:
    p = DATA / "map_manifest.json"
    return json.loads(p.read_text()) if p.exists() else {}


def api_maps(manifest: dict) -> dict:
    out = {}
    for name, info in manifest.items():
        png_dir   = MODULES / name
        png_count = len(list(png_dir.glob("*.png"))) if png_dir.exists() else 0
        out[name] = {
            "has_json":         (RAW / f"{name}.json").exists(),
            "png_count":        png_count,
            "module_count":     len(info.get("moduleKeys", [])),
            "is_predetermined": info.get("is_predetermined", False),
            "localized":        info.get("moduleLocalizedStrings", {}),
        }
    return out


def api_map_data(map_name: str, mode: str = "N") -> dict:
    raw_path = RAW / f"{map_name}.json"
    if not raw_path.exists():
        return {"error": f"No data for '{map_name}'. Run dad_downloader.py first."}

    manifest  = load_manifest()
    layout    = get_layout(map_name, manifest)
    localized = manifest.get(map_name, {}).get("moduleLocalizedStrings", {})
    data_key  = f"{mode}_Data"
    raw       = json.loads(raw_path.read_text())

    used   = set((v[0], v[1]) for v in layout.values())
    oc, or_ = 0, -2  # overflow col / row
    modules = {}

    for mod_key, mod_val in raw.items():
        if not isinstance(mod_val, dict):
            continue

        if mod_key.endswith("_Arena"):
            continue

        if mod_key in layout:
            col, row, span = layout[mod_key]
        else:
            while (oc, or_) in used:
                oc += 1
                if oc > 10: oc = 0; or_ -= 1
            col, row, span = oc, or_, 1
            used.add((oc, or_))
            oc += 1

        items_raw = mod_val.get(data_key) or mod_val.get("N_Data", [])

        # Per-module bounding box for coordinate scaling
        xs = [i["object_location"]["X"] for i in items_raw if "object_location" in i]
        ys = [i["object_location"]["Y"] for i in items_raw if "object_location" in i]
        if xs:
            xmin, xmax = min(xs), max(xs)
            ymin, ymax = min(ys), max(ys)
            # Pad 8%
            xp = (xmax - xmin) * 0.08 or 150
            yp = (ymax - ymin) * 0.08 or 150
            bbox = {"xmin": xmin-xp, "xmax": xmax+xp,
                    "ymin": ymin-yp, "ymax": ymax+yp}
        else:
            bbox = {"xmin": -1600, "xmax": 1600, "ymin": -1600, "ymax": 1600}

        items = [
            {
                "id":   i.get("object_name", ""),
                "cat":  i.get("entity_category", "unknown"),
                "type": i.get("type", ""),
                "x":    i["object_location"]["X"],
                "y":    i["object_location"]["Y"],
                "name": i.get("LocalizedString", ""),
            }
            for i in items_raw if "object_location" in i
        ]

        modules[mod_key] = {
            "col":     col,
            "row":     row,
            "span":    span,
            "label":   localized.get(mod_key, mod_val.get("Module_LocalizedString", mod_key)),
            "has_png": _is_valid_png(MODULES / map_name / f"{mod_key}.png"),
            "bbox":    bbox,
            "items":   items,
        }

    return {"modules": modules, "map": map_name, "mode": mode}


# ── HTTP handler ──────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    manifest = {}

    def log_message(self, *a): pass

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        path  = p.path
        query = dict(urllib.parse.parse_qsl(p.query))
        try:
            if path in ("/", "/index.html"):
                self._file(HTML / "index.html", "text/html; charset=utf-8")
            elif path == "/viewer.js":
                self._file(HTML / "viewer.js", "application/javascript; charset=utf-8")
            elif path.startswith("/tile/"):
                parts = path.strip("/").split("/")
                if len(parts) == 3:
                    img = MODULES / parts[1] / parts[2]
                    if img.exists() and _is_valid_png(img):
                        self._binary(img, "image/png")
                    else:
                        self.send_error(404)
                else:
                    self.send_error(404)
            elif path == "/api/maps":
                self._json(api_maps(self.manifest))
            elif path == "/api/mapdata":
                self._json(api_map_data(query.get("map","Crypt"), query.get("mode","N")))
            else:
                self.send_error(404)
        except Exception as e:
            self.send_error(500, str(e))

    def _json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers(); self.wfile.write(body)

    def _file(self, p: Path, ct: str):
        if not p.exists(): self.send_error(404); return
        d = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(d)))
        self.end_headers(); self.wfile.write(d)

    def _binary(self, p: Path, ct: str):
        d = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(d)))
        self.send_header("Cache-Control", "max-age=86400")
        self.end_headers(); self.wfile.write(d)


# ── Write frontend ────────────────────────────────────────
def write_frontend():
    HTML.mkdir(parents=True, exist_ok=True)
    (HTML / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (HTML / "viewer.js").write_text(VIEWER_JS, encoding="utf-8")


# ── Boot ──────────────────────────────────────────────────
def main():
    manifest = load_manifest()
    if not manifest:
        print("[!] data/map_manifest.json not found.")
        print("    Run dad_downloader.py first (option 3 fetches the manifest).\n")
    Handler.manifest = manifest
    write_frontend()
    server = HTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"\n  ⚔  Dark and Darker Map Viewer")
    print(f"  ──────────────────────────────")
    print(f"  {url}")
    print(f"  Ctrl+C to stop\n")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")


# ─────────────────────────────────────────────────────────────────────────────
INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Dark and Darker – Map Viewer</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0f0d0b;--panel:#16151a;--panel2:#1e1d24;
  --border:#2c2b36;--border2:#3a3948;
  --accent:#c8a84b;--accent2:#e8c870;
  --text:#dddad4;--dim:#78767a;--dim2:#464450;
}
html,body{height:100%;overflow:hidden}
body{background:var(--bg);color:var(--text);
     font-family:'Segoe UI',system-ui,sans-serif;font-size:13px;
     display:flex;height:100vh}
::-webkit-scrollbar{width:5px}
::-webkit-scrollbar-thumb{background:var(--border2);border-radius:3px}

/* Sidebar */
#sb{width:255px;min-width:255px;background:var(--panel);
    border-right:1px solid var(--border);
    display:flex;flex-direction:column;overflow:hidden;user-select:none}
#sb-hd{padding:13px 12px 10px;border-bottom:1px solid var(--border);background:var(--panel2)}
#sb-hd h1{font-size:14px;font-weight:700;color:var(--accent);letter-spacing:.04em}
#sb-hd p{font-size:10px;color:var(--dim);margin-top:2px}
#sb-bd{flex:1;overflow-y:auto;padding:10px 10px 16px}
.lbl{font-size:9px;text-transform:uppercase;letter-spacing:.1em;color:var(--dim);
     padding:10px 2px 5px;border-bottom:1px solid var(--border);margin-bottom:6px}
.lbl:first-child{padding-top:2px}

/* Controls */
select{width:100%;background:var(--bg);color:var(--text);
       border:1px solid var(--border2);border-radius:5px;
       padding:7px 28px 7px 9px;font-size:12px;cursor:pointer;
       appearance:none;outline:none;
       background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%23666'/%3E%3C/svg%3E");
       background-repeat:no-repeat;background-position:right 9px center;
       transition:border-color .15s}
select:focus{border-color:var(--accent)}
.pills{display:flex;gap:5px;margin-top:1px}
.pill{flex:1;padding:5px 4px;font-size:11px;font-weight:600;
      border:1px solid var(--border2);border-radius:5px;
      background:var(--bg);color:var(--dim);cursor:pointer;text-align:center;
      transition:all .15s}
.pill.on{background:var(--accent);color:#111;border-color:var(--accent)}
.pill:hover:not(.on){color:var(--text)}
.ck{display:flex;align-items:center;gap:8px;padding:3px 2px;cursor:pointer;border-radius:4px}
.ck:hover{background:#ffffff08}
.ck input{accent-color:var(--accent);cursor:pointer}
.ck span{font-size:12px}

/* Filters */
#f-area{margin-top:2px}
.fg{margin-bottom:2px}
.fg-hd{display:flex;align-items:center;gap:5px;padding:5px 4px 4px;
       cursor:pointer;border-radius:4px;font-size:11px;font-weight:600;
       color:var(--dim);transition:color .1s}
.fg-hd:hover{color:var(--text)}
.fg-hd .arr{font-size:9px;transition:transform .15s;display:inline-block}
.fg-hd.open .arr{transform:rotate(90deg)}
.fg-rows{display:none;padding-left:4px}
.fg-hd.open+.fg-rows{display:flex;flex-direction:column;gap:1px}
.fi{display:flex;align-items:center;gap:7px;padding:3px 5px;
    border-radius:4px;cursor:pointer;transition:background .1s}
.fi:hover{background:#ffffff0a}
.fi.off{opacity:.28}
.dot{width:9px;height:9px;border-radius:50%;flex-shrink:0;box-shadow:0 0 5px currentColor}
.fn{font-size:11px;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fc{font-size:10px;color:var(--dim2);min-width:26px;text-align:right}
.fg-all{font-size:10px;margin-left:auto;padding:1px 7px;
        background:none;border:1px solid var(--border2);
        border-radius:3px;color:var(--dim);cursor:pointer}
.fg-all:hover{color:var(--text)}
#btn-tall{width:100%;padding:5px;font-size:11px;
          background:var(--panel2);border:1px solid var(--border);
          border-radius:5px;color:var(--dim);cursor:pointer;
          margin-bottom:8px;transition:all .15s}
#btn-tall:hover{color:var(--text);border-color:var(--border2)}

/* Status */
#sbar{padding:7px 12px;border-top:1px solid var(--border);font-size:10px;
      color:var(--dim);background:var(--panel2);white-space:nowrap;
      overflow:hidden;text-overflow:ellipsis}

/* Viewport */
#vp{flex:1;overflow:hidden;position:relative;background:var(--bg);cursor:grab}
#vp.gb{cursor:grabbing}
#mc{position:absolute;top:0;left:0;transform-origin:0 0}

/* Tiles */
.tw{position:absolute;cursor:pointer}
.ti{position:absolute;top:0;left:0;width:100%;height:100%;
    display:block;opacity:.9;image-rendering:auto}
.tp{position:absolute;top:0;left:0;width:100%;height:100%;
    background:#131320;border:1px solid #1e1e30;
    display:flex;align-items:center;justify-content:center}
.tp span{font-size:9px;color:#333345;text-align:center;padding:6px;
         word-break:break-all;line-height:1.4}
.tl{position:absolute;bottom:0;left:0;right:0;font-size:9px;
    color:var(--lbl-color,rgba(255,255,255,.65));text-align:center;pointer-events:none;
    text-shadow:0 0 3px #000,0 0 3px #000;padding:2px 3px;
    background:linear-gradient(transparent,rgba(0,0,0,.65))}
.ov{position:absolute;top:0;left:0;overflow:visible;pointer-events:none}

/* Markers */
.mk{cursor:pointer;pointer-events:all;transition:transform .08s}
.mk:hover{transform:scale(1.5)}

/* HUD */
#hud{position:absolute;bottom:12px;right:12px;display:flex;flex-direction:column;gap:5px;z-index:50}
.hb{width:32px;height:32px;background:#111118cc;border:1px solid var(--border);
    border-radius:6px;color:var(--text);cursor:pointer;font-size:15px;
    display:flex;align-items:center;justify-content:center;transition:background .1s}
.hb:hover{background:#222234cc}
#zlbl{position:absolute;bottom:14px;left:50%;transform:translateX(-50%);
      font-size:10px;color:var(--dim);z-index:50;
      background:var(--panel);padding:2px 7px;border-radius:3px;
      border:1px solid var(--border);pointer-events:none}

/* Tooltip */
#tt{position:fixed;background:#0e0d14ee;border:1px solid var(--border2);
    padding:8px 11px;border-radius:6px;font-size:11px;color:var(--text);
    pointer-events:none;z-index:9999;max-width:220px;display:none;
    line-height:1.6;box-shadow:0 4px 20px #000a}
#tt .tn{font-weight:700;color:var(--accent2)}
#tt .tc{font-size:10px;color:var(--dim);margin-top:1px}
#tt .tp2{font-size:9px;color:var(--dim2);margin-top:4px}

/* No-data */
#nd{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
    text-align:center;color:var(--dim);pointer-events:none;display:none}
#nd h2{font-size:18px;color:var(--dim2);margin-bottom:8px}
#nd p{font-size:12px;max-width:300px;line-height:1.6}
code{background:var(--panel2);padding:1px 5px;border-radius:3px;
     font-size:11px;color:var(--accent)}

/* Module list */
#mod-list{display:flex;flex-direction:column;gap:1px;max-height:140px;overflow-y:auto;margin-bottom:4px}
.ml-item{padding:4px 6px;font-size:11px;border-radius:4px;cursor:pointer;
          color:var(--text);transition:background .1s;white-space:nowrap;
          overflow:hidden;text-overflow:ellipsis}
.ml-item:hover{background:#ffffff0a;color:var(--accent)}

/* Settings panel */
#sb{position:relative}
#sp{position:absolute;inset:0;background:var(--panel);z-index:20;
    display:none;flex-direction:column;overflow-y:auto;
    padding:10px 10px 16px;border-top:1px solid var(--border)}
#sp-hd{display:flex;align-items:center;justify-content:space-between;margin-bottom:4px}
#sp-hd span{font-size:12px;font-weight:700;color:var(--accent)}
#sp-cls{background:none;border:none;color:var(--dim);cursor:pointer;font-size:18px;padding:0 4px;line-height:1}
#sp-cls:hover{color:var(--text)}
.sp-row{display:flex;flex-direction:column;gap:4px;padding:4px 2px}
.sp-row label{font-size:11px;color:var(--text)}
#sld-ms{width:100%;accent-color:var(--accent)}
#btn-gear{background:none;border:none;color:var(--dim);cursor:pointer;
          font-size:16px;padding:2px 4px;border-radius:4px;margin-left:auto;flex-shrink:0}
#btn-gear:hover{color:var(--accent)}

/* Focus modal */
#fm{position:fixed;inset:0;z-index:500;display:none;
    align-items:center;justify-content:center;background:#0009}
#fm-box{background:var(--panel);border:1px solid var(--border2);
        border-radius:8px;max-width:min(90vw,800px);max-height:90vh;
        display:flex;flex-direction:column;overflow:hidden;min-width:320px}
#fm-hd{display:flex;align-items:center;padding:10px 14px;
       border-bottom:1px solid var(--border);background:var(--panel2);gap:8px}
#fm-title{font-weight:700;color:var(--accent);flex:1;font-size:13px;
          white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#fm-cls{background:none;border:none;color:var(--dim);cursor:pointer;
        font-size:18px;padding:0 4px;line-height:1}
#fm-cls:hover{color:var(--text)}
#fm-body{flex:1;overflow:hidden;display:flex;align-items:center;
         justify-content:center;min-height:280px;background:var(--bg)}
#fm-info{padding:6px 14px;font-size:10px;color:var(--dim);
         border-top:1px solid var(--border);background:var(--panel2)}
.fm-tw{position:relative;flex-shrink:0}
.fm-tw img{display:block;width:100%;height:100%}
.fm-tw .ov{position:absolute;inset:0;width:100%;height:100%;overflow:visible}
</style>
</head>
<body>
<div id="sb">
  <div id="sb-hd" style="display:flex;align-items:center;gap:4px">
    <div style="flex:1">
      <h1>⚔&nbsp; D&amp;D Map Viewer</h1>
      <p>darkanddarkertracker.com  •  local data</p>
    </div>
    <button id="btn-gear" title="Settings">⚙</button>
  </div>
  <div id="sp">
    <div id="sp-hd">
      <span>Settings</span>
      <button id="sp-cls">✕</button>
    </div>
    <div class="lbl" style="padding-top:4px">Marker Scale</div>
    <div class="sp-row">
      <label>Size: <span id="sld-ms-val">1.0</span>×</label>
      <input type="range" id="sld-ms" min="0.5" max="3" step="0.1" value="1">
    </div>
    <div class="lbl" style="margin-top:8px">Focus View</div>
    <label class="ck"><input type="checkbox" id="cb-mk-focus" checked><span>Markers in focus view</span></label>
    <div class="lbl" style="margin-top:8px">Label Style</div>
    <label class="ck"><input type="checkbox" id="cb-lbl-dark"><span>Dark text labels</span></label>
  </div>
  <div id="sb-bd">
    <div class="lbl">Map</div>
    <select id="msel"></select>

    <div class="lbl" style="margin-top:10px">Mode</div>
    <div class="pills">
      <button class="pill on" data-mode="N">Normal</button>
      <button class="pill"    data-mode="HR">High Roller</button>
    </div>

    <div class="lbl" style="margin-top:10px">Display</div>
    <label class="ck"><input type="checkbox" id="cb-lbl" checked><span>Module names</span></label>
    <label class="ck" style="margin-top:2px"><input type="checkbox" id="cb-mk" checked><span>Loot markers</span></label>

    <div class="lbl" style="margin-top:10px">Modules</div>
    <div id="mod-list"></div>

    <div class="lbl" style="margin-top:10px">Loot Filters</div>
    <button id="btn-tall">Toggle All On / Off</button>
    <div id="f-area"></div>
  </div>
  <div id="sbar">Loading…</div>
</div>

<div id="vp">
  <div id="mc"></div>
  <div id="nd"><h2>No Map Data</h2><p>Run <code>dad_downloader.py</code> and download map JSON files, then reload this page.</p></div>
  <div id="hud">
    <button class="hb" id="bzi" title="Zoom in (+)">+</button>
    <button class="hb" id="bzo" title="Zoom out (−)">−</button>
    <button class="hb" id="bft" title="Fit (F)">⊡</button>
  </div>
  <div id="zlbl">100%</div>
</div>
<div id="tt"></div>
<div id="fm">
  <div id="fm-bg"></div>
  <div id="fm-box">
    <div id="fm-hd">
      <span id="fm-title">Module</span>
      <button id="fm-cls">✕</button>
    </div>
    <div id="fm-body"></div>
    <div id="fm-info"></div>
  </div>
</div>
<script src="/viewer.js"></script>
</body>
</html>
"""

# ─────────────────────────────────────────────────────────────────────────────
VIEWER_JS = r"""
'use strict';

const CATS = {
  chest_legendary: {label:'Legendary Chest', color:'#FFD700', group:'Chests',    r:7, ring:true,  pri:10},
  chest_hoard:     {label:'Hoard Chest',     color:'#FF8C00', group:'Chests',    r:7, ring:true,  pri:9},
  chest_rare:      {label:'Rare Chest',      color:'#C060FF', group:'Chests',    r:6, ring:true,  pri:8},
  chest_uncommon:  {label:'Uncommon Chest',  color:'#00CC44', group:'Chests',    r:6, ring:false, pri:7},
  chest_common:    {label:'Common Chest',    color:'#AAAAAA', group:'Chests',    r:5, ring:false, pri:5},
  resource:        {label:'Resource / Ore',  color:'#00CED1', group:'Resources', r:6, ring:true,  pri:8},
  shrine:          {label:'Shrine',          color:'#FF69B4', group:'Shrines',   r:6, ring:true,  pri:8},
  exit:            {label:'Exit',            color:'#00FF88', group:'Exits',     r:7, ring:true,  pri:9},
  sub_boss:        {label:'Boss Spawn',      color:'#FF3333', group:'Bosses',    r:8, ring:true,  pri:10},
  loot_valuable:   {label:'Valuable Loot',   color:'#FFD060', group:'Loot',      r:5, ring:false, pri:6},
  loot_equipment:  {label:'Equipment',       color:'#50C850', group:'Loot',      r:5, ring:false, pri:5},
  loot_trinket:    {label:'Trinket',         color:'#A050E0', group:'Loot',      r:4, ring:false, pri:4},
  loot_consumable: {label:'Consumable',      color:'#80C040', group:'Loot',      r:4, ring:false, pri:3},
  loot_ground:     {label:'Ground Loot',     color:'#607080', group:'Loot',      r:3, ring:false, pri:2},
  trap:            {label:'Trap',            color:'#FF6030', group:'Hazards',   r:4, ring:false, pri:4},
  hazard_zone:     {label:'Hazard Zone',     color:'#FF2020', group:'Hazards',   r:6, ring:true,  pri:6},
  gate:            {label:'Gate',            color:'#C09850', group:'Interact',  r:4, ring:false, pri:3},
  lever:           {label:'Lever',           color:'#D0A060', group:'Interact',  r:3, ring:false, pri:3},
  door:            {label:'Door',            color:'#A07050', group:'Interact',  r:3, ring:false, pri:2},
  monster:         {label:'Monster Spawn',   color:'#884422', group:'Monsters',  r:4, ring:false, pri:1},
};
const GROUPS = ['Chests','Exits','Bosses','Resources','Shrines','Loot','Hazards','Interact','Monsters'];

const S = {
  map:null, mode:'N', modules:{},
  visible: new Set(Object.keys(CATS).filter(k=>CATS[k].group!=='Monsters')),
  showLbls:true, showMks:true,
  focusMarkers:true, markerScale:1.0, focusKey:null,
  zoom:1, panX:0, panY:0,
  drag:false, dx:0, dy:0, px:0, py:0,
};
const TILE = 200;
let _dragMoved = false;

const vp   = id('vp'), mc = id('mc'), tt = id('tt');
const sbar = id('sbar'), msel = id('msel'), nd = id('nd'), zlbl = id('zlbl');
const fArea= id('f-area');

function id(x){ return document.getElementById(x); }

async function init(){
  setS('Connecting…');
  let maps;
  try { maps = await fj('/api/maps'); }
  catch(e){ setS('Cannot reach server. Is map_viewer.py running?'); return; }

  msel.innerHTML='';
  let first=null;
  for(const [n,inf] of Object.entries(maps)){
    const o=document.createElement('option');
    o.value=n;
    o.textContent=`${inf.has_json?'✓':'✗'}  ${n}  (${inf.module_count})`;
    if(!inf.has_json) o.style.color='#555';
    msel.appendChild(o);
    if(inf.has_json&&!first) first=n;
  }
  buildFilters();
  bindAll();
  if(first){ msel.value=first; await loadMap(first); }
  else{ nd.style.display='block'; setS('No map data. Run dad_downloader.py first.'); }
}

async function fj(url){
  const r=await fetch(url);
  if(!r.ok) throw new Error('HTTP '+r.status);
  return r.json();
}

async function loadMap(name){
  if(!name) return;
  setS('Loading '+name+'…');
  nd.style.display='none';
  S.map=name;
  let data;
  try{ data=await fj(`/api/mapdata?map=${encodeURIComponent(name)}&mode=${S.mode}`); }
  catch(e){ setS('Error: '+e.message); return; }
  if(data.error){ setS('Error: '+data.error); nd.style.display='block'; return; }
  S.modules=data.modules;
  render(); fit(); refreshCounts(); populateModList();
  setS(`${name}  •  ${Object.keys(S.modules).length} modules  •  ${S.mode==='N'?'Normal':'High Roller'}`);
}

function render(){
  mc.innerHTML='';
  if(!Object.keys(S.modules).length) return;
  let maxC=0, maxR=0;
  for(const m of Object.values(S.modules)){
    maxC=Math.max(maxC, m.col+m.span);
    maxR=Math.max(maxR, m.row+m.span);
  }
  mc.style.width =(maxC*TILE)+'px';
  mc.style.height=(maxR*TILE)+'px';

  for(const [key,mod] of Object.entries(S.modules)){
    const W=mod.span*TILE, H=mod.span*TILE;
    const wrap=document.createElement('div');
    wrap.className='tw';
    wrap.style.cssText=`left:${mod.col*TILE}px;top:${mod.row*TILE}px;width:${W}px;height:${H}px;`;

    if(mod.has_png){
      const img=document.createElement('img');
      img.className='ti'; img.draggable=false;
      img.src=`/tile/${encodeURIComponent(S.map)}/${encodeURIComponent(key)}.png`;
      wrap.appendChild(img);
    } else {
      const ph=document.createElement('div');
      ph.className='tp';
      ph.innerHTML=`<span>${esc(mod.label||key)}</span>`;
      wrap.appendChild(ph);
    }

    if(S.showLbls){
      const l=document.createElement('div');
      l.className='tl'; l.textContent=mod.label||key;
      wrap.appendChild(l);
    }

    if(S.showMks) wrap.appendChild(mkOverlay(mod,W,H,key,S.markerScale));
    wrap.addEventListener('click',e=>{
      if(e.target.closest('.mk')||_dragMoved) return;
      openFocus(key);
    });
    mc.appendChild(wrap);
  }
  applyX();
}

const NS='http://www.w3.org/2000/svg';
function mkOverlay(mod,W,H,key,scale=1.0){
  const svg=document.createElementNS(NS,'svg');
  svg.setAttribute('width',W); svg.setAttribute('height',H);
  svg.setAttribute('overflow','visible'); svg.classList.add('ov');
  const bb=mod.bbox;
  const xr=bb.xmax-bb.xmin, yr=bb.ymax-bb.ymin;

  for(const item of (mod.items||[])){
    const cfg=CATS[item.cat];
    if(!cfg||!S.visible.has(item.cat)) continue;
    const px=((item.x-bb.xmin)/xr)*W;
    const py=((bb.ymax-item.y)/yr)*H;   // flip Y
    const r=cfg.r*scale;

    const g=document.createElementNS(NS,'g');
    g.classList.add('mk');
    g.setAttribute('transform',`translate(${px.toFixed(1)},${py.toFixed(1)})`);

    if(cfg.ring){
      const ring=document.createElementNS(NS,'circle');
      ring.setAttribute('r',(r+4*scale).toFixed(1)); ring.setAttribute('fill','none');
      ring.setAttribute('stroke',cfg.color); ring.setAttribute('stroke-width',(1.2*scale).toFixed(1));
      ring.setAttribute('stroke-opacity','0.4');
      g.appendChild(ring);
    }
    const c=document.createElementNS(NS,'circle');
    c.setAttribute('r',r.toFixed(1)); c.setAttribute('fill',cfg.color);
    c.setAttribute('fill-opacity','0.92');
    c.setAttribute('stroke','#000'); c.setAttribute('stroke-width',Math.max(0.5,scale).toFixed(1));
    g.appendChild(c);

    g.addEventListener('mouseenter', e=>showTT(e,item,cfg));
    g.addEventListener('mouseleave', hideTT);
    svg.appendChild(g);
  }
  return svg;
}

function showTT(e,item,cfg){
  const name=item.name||item.id||'?';
  tt.innerHTML=`<div class="tn">${esc(name)}</div><div class="tc">${esc(cfg.label)}</div><div class="tp2">x:${Math.round(item.x)}  y:${Math.round(item.y)}</div>`;
  tt.style.display='block'; moveTT(e);
}
function hideTT(){ tt.style.display='none'; }
function moveTT(e){
  const mx=e.clientX+14, my=e.clientY+12;
  tt.style.left=(mx+tt.offsetWidth >window.innerWidth ?mx-tt.offsetWidth -22:mx)+'px';
  tt.style.top =(my+tt.offsetHeight>window.innerHeight?my-tt.offsetHeight-20:my)+'px';
}
document.addEventListener('mousemove',e=>{ if(tt.style.display!=='none') moveTT(e); });
function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

function buildFilters(){
  fArea.innerHTML='';
  for(const gn of GROUPS){
    const cats=Object.entries(CATS).filter(([,c])=>c.group===gn).sort((a,b)=>b[1].pri-a[1].pri);
    if(!cats.length) continue;
    const div=document.createElement('div'); div.className='fg';
    const hd=document.createElement('div'); hd.className='fg-hd open';
    hd.innerHTML=`<span class="arr">▶</span><span style="flex:1">${gn}</span><button class="fg-all" data-g="${gn}">all</button>`;
    hd.addEventListener('click',e=>{ if(e.target.classList.contains('fg-all')){ toggleGroup(gn); return; } hd.classList.toggle('open'); });
    div.appendChild(hd);
    const rows=document.createElement('div'); rows.className='fg-rows';
    for(const [cat,cfg] of cats){
      const r=document.createElement('div'); r.className='fi'; r.id='fi_'+cat; r.dataset.cat=cat;
      r.innerHTML=`<div class="dot" style="background:${cfg.color};color:${cfg.color}"></div><span class="fn">${esc(cfg.label)}</span><span class="fc" id="fc_${cat}">0</span>`;
      r.addEventListener('click',()=>toggleCat(cat));
      rows.appendChild(r);
    }
    div.appendChild(rows); fArea.appendChild(div);
  }
  syncUI();
}

function toggleCat(cat){ S.visible.has(cat)?S.visible.delete(cat):S.visible.add(cat); syncUI(); rebuildOvs(); }
function toggleGroup(gn){
  const cats=Object.entries(CATS).filter(([,c])=>c.group===gn).map(([k])=>k);
  const allOn=cats.every(c=>S.visible.has(c));
  cats.forEach(c=>allOn?S.visible.delete(c):S.visible.add(c));
  syncUI(); rebuildOvs();
}
function syncUI(){ for(const cat of Object.keys(CATS)){ const el=id('fi_'+cat); if(el) el.classList.toggle('off',!S.visible.has(cat)); } }
function refreshCounts(){
  const cnt={};
  for(const m of Object.values(S.modules)) for(const item of (m.items||[])) cnt[item.cat]=(cnt[item.cat]||0)+1;
  for(const [cat,n] of Object.entries(cnt)){ const el=id('fc_'+cat); if(el) el.textContent=n; }
}
function rebuildOvs(){
  if(!S.showMks) return;
  const tiles=mc.querySelectorAll('.tw'), keys=Object.keys(S.modules);
  tiles.forEach((wrap,i)=>{
    const key=keys[i]; if(!key) return;
    const mod=S.modules[key];
    const W=mod.span*TILE, H=mod.span*TILE;
    const old=wrap.querySelector('.ov');
    const svg=mkOverlay(mod,W,H,key,S.markerScale);
    if(old) wrap.replaceChild(svg,old); else wrap.appendChild(svg);
  });
}

function populateModList(){
  const ml=id('mod-list'); if(!ml) return;
  ml.innerHTML='';
  for(const [key,mod] of Object.entries(S.modules)){
    const d=document.createElement('div'); d.className='ml-item';
    d.textContent=mod.label||key; d.title=key;
    d.addEventListener('click',()=>openFocus(key));
    ml.appendChild(d);
  }
}

function openFocus(key){
  const mod=S.modules[key]; if(!mod) return;
  S.focusKey=key;
  id('fm-title').textContent=mod.label||key;
  const span=mod.span||1;
  const FW=Math.min(500*span,580), FH=FW;
  const tw=document.createElement('div');
  tw.className='fm-tw';
  tw.style.width=FW+'px'; tw.style.height=FH+'px';
  if(mod.has_png){
    const img=document.createElement('img');
    img.src=`/tile/${encodeURIComponent(S.map)}/${encodeURIComponent(key)}.png`;
    img.style.cssText='position:absolute;inset:0;width:100%;height:100%';
    img.draggable=false;
    tw.appendChild(img);
  } else {
    const ph=document.createElement('div');
    ph.className='tp'; ph.style.cssText='position:absolute;inset:0';
    ph.innerHTML=`<span>${esc(mod.label||key)}</span>`;
    tw.appendChild(ph);
  }
  if(S.focusMarkers){
    const sc=S.markerScale*2.0;
    const svg=mkOverlay(mod,FW,FH,key,sc);
    svg.style.cssText='position:absolute;inset:0;width:100%;height:100%;overflow:visible;pointer-events:none';
    tw.appendChild(svg);
  }
  const body=id('fm-body'); body.innerHTML=''; body.appendChild(tw);
  const vis=(mod.items||[]).filter(i=>S.visible.has(i.cat)).length;
  const tot=(mod.items||[]).length;
  id('fm-info').textContent=`${key}  •  ${vis} markers shown  (${tot} total items)`;
  id('fm').style.display='flex';
}

function closeFocus(){ id('fm').style.display='none'; S.focusKey=null; }

function toggleSettings(){
  const sp=id('sp');
  sp.style.display=sp.style.display==='flex'?'none':'flex';
}

function applyX(){
  mc.style.transform=`translate(${S.panX}px,${S.panY}px) scale(${S.zoom})`;
  zlbl.textContent=Math.round(S.zoom*100)+'%';
}
function fit(){
  const vw=vp.clientWidth, vh=vp.clientHeight;
  const cw=parseInt(mc.style.width)||800, ch=parseInt(mc.style.height)||600;
  const s=Math.min((vw-32)/cw,(vh-32)/ch,1.5);
  S.zoom=Math.max(0.08,s);
  S.panX=(vw-cw*S.zoom)/2; S.panY=(vh-ch*S.zoom)/2;
  applyX();
}
function zoomAt(f,cx,cy){
  const nz=Math.max(0.05,Math.min(6,S.zoom*f));
  S.panX=cx-(cx-S.panX)*(nz/S.zoom);
  S.panY=cy-(cy-S.panY)*(nz/S.zoom);
  S.zoom=nz; applyX();
}

function bindAll(){
  msel.addEventListener('change',()=>loadMap(msel.value));
  document.querySelectorAll('.pill').forEach(b=>{
    b.addEventListener('click',()=>{
      document.querySelectorAll('.pill').forEach(x=>x.classList.remove('on'));
      b.classList.add('on'); S.mode=b.dataset.mode;
      if(S.map) loadMap(S.map);
    });
  });
  id('cb-lbl').addEventListener('change',e=>{ S.showLbls=e.target.checked; render(); });
  id('cb-mk') .addEventListener('change',e=>{ S.showMks =e.target.checked; render(); });
  id('btn-gear').addEventListener('click', toggleSettings);
  id('sp-cls').addEventListener('click',()=>{ id('sp').style.display='none'; });
  id('sld-ms').addEventListener('input',e=>{
    S.markerScale=parseFloat(e.target.value);
    id('sld-ms-val').textContent=S.markerScale.toFixed(1);
    render();
  });
  id('cb-mk-focus').addEventListener('change',e=>{ S.focusMarkers=e.target.checked; });
  id('cb-lbl-dark').addEventListener('change',e=>{
    document.documentElement.style.setProperty('--lbl-color', e.target.checked?'rgba(0,0,0,.85)':'rgba(255,255,255,.65)');
    render();
  });
  id('fm-cls').addEventListener('click', closeFocus);
  id('fm-bg').addEventListener('click', closeFocus);
  id('btn-tall').addEventListener('click',()=>{
    const allOn=Object.keys(CATS).every(c=>S.visible.has(c));
    allOn?S.visible.clear():Object.keys(CATS).forEach(c=>S.visible.add(c));
    syncUI(); rebuildOvs();
  });
  id('bzi').addEventListener('click',()=>zoomAt(1.25,vp.clientWidth/2,vp.clientHeight/2));
  id('bzo').addEventListener('click',()=>zoomAt(0.80,vp.clientWidth/2,vp.clientHeight/2));
  id('bft').addEventListener('click',fit);
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'){ closeFocus(); return; }
    if(['INPUT','SELECT','TEXTAREA'].includes(document.activeElement.tagName)) return;
    if(e.key==='+'||e.key==='=') zoomAt(1.2, vp.clientWidth/2, vp.clientHeight/2);
    if(e.key==='-')              zoomAt(0.83,vp.clientWidth/2, vp.clientHeight/2);
    if(e.key==='f'||e.key==='F') fit();
  });
  vp.addEventListener('wheel',e=>{
    e.preventDefault();
    const rect=vp.getBoundingClientRect();
    zoomAt(e.deltaY<0?1.1:0.91, e.clientX-rect.left, e.clientY-rect.top);
  },{passive:false});
  vp.addEventListener('mousedown',e=>{
    if(e.target.closest('.mk')) return;
    _dragMoved=false;
    S.drag=true; S.dx=e.clientX; S.dy=e.clientY; S.px=S.panX; S.py=S.panY;
    vp.classList.add('gb');
  });
  window.addEventListener('mousemove',e=>{
    if(!S.drag) return;
    if(Math.abs(e.clientX-S.dx)+Math.abs(e.clientY-S.dy)>5) _dragMoved=true;
    S.panX=S.px+(e.clientX-S.dx); S.panY=S.py+(e.clientY-S.dy); applyX();
  });
  window.addEventListener('mouseup',()=>{ S.drag=false; vp.classList.remove('gb'); });
  let ltd=0,ltc=null;
  vp.addEventListener('touchstart',e=>{
    if(e.touches.length===1){ S.drag=true; S.dx=e.touches[0].clientX; S.dy=e.touches[0].clientY; S.px=S.panX; S.py=S.panY; }
    if(e.touches.length===2){ const a=e.touches[0],b=e.touches[1]; ltd=Math.hypot(a.clientX-b.clientX,a.clientY-b.clientY); ltc={x:(a.clientX+b.clientX)/2,y:(a.clientY+b.clientY)/2}; }
  },{passive:true});
  vp.addEventListener('touchmove',e=>{
    if(e.touches.length===1&&S.drag){ S.panX=S.px+(e.touches[0].clientX-S.dx); S.panY=S.py+(e.touches[0].clientY-S.dy); applyX(); }
    if(e.touches.length===2&&ltc){ const a=e.touches[0],b=e.touches[1]; const nd=Math.hypot(a.clientX-b.clientX,a.clientY-b.clientY); const cx=(a.clientX+b.clientX)/2,cy=(a.clientY+b.clientY)/2; zoomAt(nd/ltd,cx,cy); ltd=nd; }
  },{passive:true});
  vp.addEventListener('touchend',()=>{ S.drag=false; ltc=null; });
}
function setS(m){ sbar.textContent=m; }
init();
"""

if __name__ == "__main__":
    main()
