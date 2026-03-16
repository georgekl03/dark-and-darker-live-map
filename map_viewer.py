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
BG     = "#0f0d0b"
PANEL  = "#16151a"
PANEL2 = "#1e1d24"
BORDER = "#2c2b36"
BDR2   = "#3a3948"
ACCENT = "#c8a84b"
TEXT   = "#dddad4"
DIM    = "#78767a"
DIM2   = "#464450"
TILEBG = "#131320"

# ── Category config ───────────────────────────────────────
CATS = {
    "chest_legendary": dict(label="Legendary Chest", color="#FFD700", group="Chests",    r=9,  ring=True,  pri=10),
    "chest_hoard":     dict(label="Hoard Chest",     color="#FF8C00", group="Chests",    r=9,  ring=True,  pri=9),
    "chest_rare":      dict(label="Rare Chest",      color="#C060FF", group="Chests",    r=7,  ring=True,  pri=8),
    "chest_uncommon":  dict(label="Uncommon Chest",  color="#00CC44", group="Chests",    r=7,  ring=False, pri=7),
    "chest_common":    dict(label="Common Chest",    color="#AAAAAA", group="Chests",    r=6,  ring=False, pri=5),
    "resource":        dict(label="Resource / Ore",  color="#00CED1", group="Resources", r=7,  ring=True,  pri=8),
    "shrine":          dict(label="Shrine",          color="#FF69B4", group="Shrines",   r=7,  ring=True,  pri=8),
    "exit":            dict(label="Exit",            color="#00FF88", group="Exits",     r=9,  ring=True,  pri=9),
    "sub_boss":        dict(label="Boss Spawn",      color="#FF3333", group="Bosses",    r=10, ring=True,  pri=10),
    "loot_valuable":   dict(label="Valuable Loot",   color="#FFD060", group="Loot",      r=6,  ring=False, pri=6),
    "loot_equipment":  dict(label="Equipment",       color="#50C850", group="Loot",      r=6,  ring=False, pri=5),
    "loot_trinket":    dict(label="Trinket",         color="#A050E0", group="Loot",      r=5,  ring=False, pri=4),
    "loot_consumable": dict(label="Consumable",      color="#80C040", group="Loot",      r=5,  ring=False, pri=3),
    "loot_ground":     dict(label="Ground Loot",     color="#607080", group="Loot",      r=4,  ring=False, pri=2),
    "trap":            dict(label="Trap",            color="#FF6030", group="Hazards",   r=5,  ring=False, pri=4),
    "hazard_zone":     dict(label="Hazard Zone",     color="#FF2020", group="Hazards",   r=7,  ring=True,  pri=6),
    "gate":            dict(label="Gate",            color="#C09850", group="Interact",  r=5,  ring=False, pri=3),
    "lever":           dict(label="Lever",           color="#D0A060", group="Interact",  r=4,  ring=False, pri=3),
    "door":            dict(label="Door",            color="#A07050", group="Interact",  r=4,  ring=False, pri=2),
    "monster":         dict(label="Monster Spawn",   color="#884422", group="Monsters",  r=4,  ring=False, pri=1),
}
GROUPS_ORDER = ["Chests","Exits","Bosses","Resources","Shrines","Loot","Hazards","Interact","Monsters"]
DEFAULT_VISIBLE = {k for k,v in CATS.items() if v["group"] != "Monsters"}

# ── Crypt layout (extracted from website's Leaflet HTML) ─
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
KNOWN_LAYOUTS = {"Crypt": CRYPT_LAYOUT}


def auto_layout(keys):
    cols = max(1, math.ceil(math.sqrt(len(keys))))
    return {k: (i%cols, i//cols, 1) for i,k in enumerate(keys)}

def get_layout(name, manifest):
    if name in KNOWN_LAYOUTS:
        return KNOWN_LAYOUTS[name]
    return auto_layout(manifest.get(name,{}).get("moduleKeys",[]))

# ── Settings ──────────────────────────────────────────────
SETTINGS_PATH = DATA / "settings.json"
DEFAULTS = {
    "tile_px":180, "focus_tile_px":500,
    "marker_scale":1.0, "focus_marker_scale":1.8,
    "show_labels":True, "mode":"N", "last_map":"",
    "visible_cats":list(DEFAULT_VISIBLE),
}

def load_settings():
    if SETTINGS_PATH.exists():
        try: return {**DEFAULTS, **json.loads(SETTINGS_PATH.read_text())}
        except: pass
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
    if not rp.exists(): return {}
    layout    = get_layout(map_name, manifest)
    localized = manifest.get(map_name,{}).get("moduleLocalizedStrings",{})
    data_key  = f"{mode}_Data"
    raw       = json.loads(rp.read_text())
    used = set((v[0],v[1]) for v in layout.values())
    max_row = max((v[1] for v in layout.values()), default=-1) if layout else -1
    oc = 0; ory = max_row + 1
    out = {}
    for mk, mv in raw.items():
        if not isinstance(mv, dict): continue
        if mk in layout:
            col,row,span = layout[mk]
        else:
            while (oc,ory) in used:
                oc += 1
                if oc > 12: oc=0; ory+=1
            col,row,span = oc,ory,1
            used.add((oc,ory)); oc+=1
        items_raw = mv.get(data_key) or mv.get("N_Data",[])
        xs=[i["object_location"]["X"] for i in items_raw if "object_location" in i]
        ys=[i["object_location"]["Y"] for i in items_raw if "object_location" in i]
        if xs:
            xp=(max(xs)-min(xs))*0.08 or 150
            yp=(max(ys)-min(ys))*0.08 or 150
            bbox={"xmin":min(xs)-xp,"xmax":max(xs)+xp,"ymin":min(ys)-yp,"ymax":max(ys)+yp}
        else:
            bbox={"xmin":-1600,"xmax":1600,"ymin":-1600,"ymax":1600}
        items=[{"id":i.get("object_name",""),"cat":i.get("entity_category","?"),
                "name":i.get("LocalizedString",""),
                "x":i["object_location"]["X"],"y":i["object_location"]["Y"]}
               for i in items_raw if "object_location" in i]
        out[mk]={
            "col":col,"row":row,"span":span,
            "label":localized.get(mk, mv.get("Module_LocalizedString",mk) or mk),
            "bbox":bbox,"items":items,
            "has_png":(MODULES/map_name/f"{mk}.png").exists(),
        }
    return out

# ── Image helpers ─────────────────────────────────────────
_img_cache = {}

PNG_SIG = b"\x89PNG\r\n\x1a\n"

def _is_png_file(path: Path) -> bool:
    """Return True only if *path* starts with the 8-byte PNG signature."""
    try:
        with open(path, "rb") as f:
            return f.read(8) == PNG_SIG
    except Exception:
        return False

def hex_rgb(h):
    h=h.lstrip("#")
    return (int(h[0:2],16),int(h[2:4],16),int(h[4:6],16))

def get_tile_img(map_name, mod_key, px):
    k=f"{map_name}/{mod_key}/{px}"
    if k in _img_cache: return _img_cache[k]
    p = MODULES/map_name/f"{mod_key}.png"
    img = None
    if p.exists():
        try:
            # Strategy: signature check → force decode → quarantine on failure → placeholder
            if not _is_png_file(p):
                raise ValueError("Invalid PNG signature.")
            im = Image.open(p)
            im.load()  # force full decode so errors surface here
            img = im.convert("RGBA").resize((px,px),Image.LANCZOS)
        except Exception:
            # Quarantine the bad file so we don't keep re-trying it
            try:
                bad = p.with_suffix(p.suffix + ".bad")
                if not bad.exists():
                    p.rename(bad)
            except Exception:
                pass
            img = None
    if img is None:
        img=Image.new("RGBA",(px,px),(19,19,32,255))
        d=ImageDraw.Draw(img)
        d.rectangle([0,0,px-1,px-1],outline=(44,43,54),width=1)
    _img_cache[k]=img
    return img

def render_tile(map_name, mod_key, mod, tile_px, visible, mscale):
    span=mod["span"]
    W=H=span*tile_px
    img=get_tile_img(map_name,mod_key,W).copy()
    draw=ImageDraw.Draw(img)
    bb=mod["bbox"]
    xr=bb["xmax"]-bb["xmin"] or 1
    yr=bb["ymax"]-bb["ymin"] or 1
    for item in mod["items"]:
        cat=item["cat"]
        cfg=CATS.get(cat)
        if not cfg or cat not in visible: continue
        px2=((item["x"]-bb["xmin"])/xr)*W
        py2=((bb["ymax"]-item["y"])/yr)*H
        r=max(2,int(cfg["r"]*mscale))
        rc=hex_rgb(cfg["color"])
        if cfg["ring"]:
            rr=r+max(2,int(4*mscale))
            draw.ellipse([px2-rr,py2-rr,px2+rr,py2+rr],outline=(*rc,90),width=max(1,int(1.5*mscale)))
        draw.ellipse([px2-r,py2-r,px2+r,py2+r],fill=(*rc,235),outline=(0,0,0,200),width=max(1,int(mscale)))
    return img

# ── Scrollable frame ──────────────────────────────────────
class ScrollFrame(tk.Frame):
    def __init__(self,parent,bg=PANEL,**kw):
        super().__init__(parent,bg=bg,**kw)
        self.cv=tk.Canvas(self,bg=bg,bd=0,highlightthickness=0)
        self.sb=tk.Scrollbar(self,orient="vertical",command=self.cv.yview)
        self.inner=tk.Frame(self.cv,bg=bg)
        self.inner.bind("<Configure>",lambda e:self.cv.configure(scrollregion=self.cv.bbox("all")))
        self.cv.create_window((0,0),window=self.inner,anchor="nw")
        self.cv.configure(yscrollcommand=self.sb.set)
        self.cv.pack(side="left",fill="both",expand=True)
        self.sb.pack(side="right",fill="y")
        for w in (self.cv,self.inner):
            w.bind("<MouseWheel>",self._scroll)
            w.bind("<Button-4>",self._scroll)
            w.bind("<Button-5>",self._scroll)
    def _scroll(self,e):
        d=-1 if(getattr(e,"delta",0)>0 or e.num==4) else 1
        self.cv.yview_scroll(d,"units")

# ── Main App ──────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Dark and Darker – Map Viewer")
        self.configure(bg=BG)
        self.geometry("1600x900")
        self.minsize(900,600)
        self.manifest  = load_manifest()
        self.cfg       = load_settings()
        self.modules   = {}
        self.visible   = set(self.cfg["visible_cats"])
        self.focus_key = None
        self._tkimgs   = []
        self._zoom     = 1.0
        self._panx     = 0.0
        self._pany     = 0.0
        self._drag     = None
        self._tt_win   = None
        self._cur_map  = ""
        self._build()
        self._populate_maps()
        # Do not auto-load a map on startup — wait for the user to select one.
        self.map_var.set("")
        self.status.set("Select a map to load.")

    # ── Build UI ──────────────────────────────────────────
    def _build(self):
        pw=tk.PanedWindow(self,orient="horizontal",bg=BG,sashwidth=4,sashrelief="flat",sashpad=0)
        pw.pack(fill="both",expand=True)

        # Left sidebar
        left=tk.Frame(pw,bg=PANEL,width=265)
        left.pack_propagate(False)
        pw.add(left,minsize=210)
        self._build_sidebar(left)

        # Right: focus panel + map
        right=tk.Frame(pw,bg=BG)
        pw.add(right,minsize=600)
        rpw=tk.PanedWindow(right,orient="horizontal",bg=BG,sashwidth=4,sashrelief="flat",sashpad=0)
        rpw.pack(fill="both",expand=True)

        focus_f=tk.Frame(rpw,bg=PANEL2,width=310)
        focus_f.pack_propagate(False)
        rpw.add(focus_f,minsize=200)
        self._build_focus(focus_f)

        map_f=tk.Frame(rpw,bg=BG)
        rpw.add(map_f,minsize=400)
        self._build_map(map_f)

    def _build_sidebar(self,p):
        # Header
        hdr=tk.Frame(p,bg=PANEL2,pady=8)
        hdr.pack(fill="x")
        tk.Label(hdr,text="⚔  D&D Map Viewer",bg=PANEL2,fg=ACCENT,
                 font=("Segoe UI",12,"bold"),padx=12).pack(anchor="w")
        tk.Label(hdr,text="darkanddarkertracker.com  •  local data",
                 bg=PANEL2,fg=DIM,font=("Segoe UI",9),padx=12).pack(anchor="w")
        tk.Frame(p,bg=BORDER,height=1).pack(fill="x")

        sf=ScrollFrame(p,bg=PANEL)
        sf.pack(fill="both",expand=True)
        b=sf.inner

        def sec(txt):
            tk.Frame(b,bg=BORDER,height=1).pack(fill="x",pady=(10,0))
            tk.Label(b,text=txt.upper(),bg=PANEL,fg=DIM2,
                     font=("Segoe UI",8),padx=8,pady=3,anchor="w").pack(fill="x")

        # Map + Mode
        sec("Map")
        self.map_var=tk.StringVar()
        self.map_combo=ttk.Combobox(b,textvariable=self.map_var,state="readonly",
                                    font=("Segoe UI",11))
        self._style_widgets()
        self.map_combo.pack(fill="x",padx=8,pady=4)
        self.map_combo.bind("<<ComboboxSelected>>",lambda e:self._reload_map())

        sec("Mode")
        mrow=tk.Frame(b,bg=PANEL); mrow.pack(fill="x",padx=8,pady=4)
        self.mode_var=tk.StringVar(value=self.cfg.get("mode","N"))
        for val,lbl in [("N","Normal"),("HR","High Roller")]:
            tk.Radiobutton(mrow,text=lbl,variable=self.mode_var,value=val,
                           bg=PANEL,fg=TEXT,selectcolor=PANEL2,
                           activebackground=PANEL,activeforeground=ACCENT,
                           font=("Segoe UI",10),command=self._reload_map
                           ).pack(side="left",padx=(0,8))

        # Module list
        sec("Modules  (click to focus)")
        self.mod_list=tk.Listbox(b,bg=PANEL2,fg=TEXT,selectbackground=ACCENT,
                                  selectforeground="#111",font=("Segoe UI",10),
                                  bd=0,highlightthickness=1,
                                  highlightcolor=BDR2,highlightbackground=BORDER,
                                  height=10,exportselection=False)
        self.mod_list.pack(fill="x",padx=8,pady=4)
        self.mod_list.bind("<<ListboxSelect>>",self._on_mod_select)

        # Filters
        sec("Loot Filters")
        br=tk.Frame(b,bg=PANEL); br.pack(fill="x",padx=8,pady=(2,6))
        for txt,cmd in [("All On",self._all_on),("All Off",self._all_off)]:
            tk.Button(br,text=txt,bg=PANEL2,fg=TEXT,bd=0,
                      font=("Segoe UI",9),padx=6,pady=2,
                      activebackground=BDR2,activeforeground=TEXT,
                      command=cmd).pack(side="left",padx=(0,4))
        self._fvars={}
        self._cnt_labels={}
        for gname in GROUPS_ORDER:
            cats=[(k,v) for k,v in CATS.items() if v["group"]==gname]
            if not cats: continue
            gf=tk.LabelFrame(b,text=gname,bg=PANEL,fg=DIM2,
                              font=("Segoe UI",8),bd=1,relief="flat",padx=6,pady=4)
            gf.pack(fill="x",padx=8,pady=2)
            for cat,cfg in sorted(cats,key=lambda x:-x[1]["pri"]):
                var=tk.BooleanVar(value=(cat in self.visible))
                self._fvars[cat]=var
                row=tk.Frame(gf,bg=PANEL); row.pack(fill="x",pady=1)
                tk.Label(row,bg=cfg["color"],width=2).pack(side="left",padx=(0,5))
                tk.Checkbutton(row,text=cfg["label"],variable=var,bg=PANEL,fg=TEXT,
                               selectcolor=PANEL2,activebackground=PANEL,
                               activeforeground=TEXT,font=("Segoe UI",10),
                               command=self._filter_changed).pack(side="left")
                cnt=tk.Label(row,text="",bg=PANEL,fg=DIM2,font=("Segoe UI",9))
                cnt.pack(side="right")
                self._cnt_labels[cat]=cnt

        # Settings
        sec("Settings")
        self._spins={}
        for lbl,attr,lo,hi,step in [
            ("Map tile size (px)",       "tile_px",           60,400,20),
            ("Focus tile size (px)",     "focus_tile_px",     200,900,50),
            ("Marker scale (map)",       "marker_scale",      0.2,4.0,0.1),
            ("Marker scale (focus)",     "focus_marker_scale",0.5,6.0,0.2),
        ]:
            r=tk.Frame(b,bg=PANEL); r.pack(fill="x",padx=8,pady=2)
            tk.Label(r,text=lbl,bg=PANEL,fg=TEXT,font=("Segoe UI",10),
                     anchor="w",width=24).pack(side="left")
            v=tk.DoubleVar(value=self.cfg[attr])
            sp=tk.Spinbox(r,from_=lo,to=hi,increment=step,textvariable=v,width=6,
                          bg=PANEL2,fg=TEXT,bd=0,buttonbackground=PANEL2,
                          font=("Segoe UI",10),
                          command=lambda a=attr,vv=v:self._setting(a,vv))
            sp.pack(side="right")
            sp.bind("<Return>",lambda e,a=attr,vv=v:self._setting(a,vv))
            self._spins[attr]=v

        lr=tk.Frame(b,bg=PANEL); lr.pack(fill="x",padx=8,pady=2)
        self.lblvar=tk.BooleanVar(value=self.cfg["show_labels"])
        tk.Checkbutton(lr,text="Show module names",variable=self.lblvar,bg=PANEL,fg=TEXT,
                       selectcolor=PANEL2,activebackground=PANEL,activeforeground=TEXT,
                       font=("Segoe UI",10),command=self._toggle_labels).pack(anchor="w")

        tk.Frame(b,bg=PANEL,height=4).pack()
        tk.Button(b,text="Reset to Defaults",bg=PANEL2,fg=DIM,bd=0,
                  font=("Segoe UI",9),padx=8,pady=3,
                  activebackground=BDR2,activeforeground=TEXT,
                  command=self._reset).pack(padx=8,anchor="w")

        # Status
        tk.Frame(p,bg=BORDER,height=1).pack(fill="x")
        self.status=tk.StringVar(value="Ready")
        tk.Label(p,textvariable=self.status,bg=PANEL2,fg=DIM,
                 font=("Segoe UI",9),padx=10,pady=5,anchor="w"
                 ).pack(fill="x",side="bottom")

    def _style_widgets(self):
        s=ttk.Style(); s.theme_use("clam")
        s.configure("TCombobox",fieldbackground=PANEL2,background=PANEL2,
                    foreground=TEXT,selectbackground=PANEL2,selectforeground=TEXT,
                    bordercolor=BDR2,arrowcolor=DIM,padding=(4,4))

    def _build_focus(self,p):
        hdr=tk.Frame(p,bg=PANEL2); hdr.pack(fill="x")
        self.focus_title=tk.Label(hdr,text="No module selected",bg=PANEL2,fg=ACCENT,
                                   font=("Segoe UI",11,"bold"),padx=10,pady=8,anchor="w")
        self.focus_title.pack(fill="x")
        tk.Frame(p,bg=BORDER,height=1).pack(fill="x")
        self.focus_cv=tk.Canvas(p,bg=TILEBG,bd=0,highlightthickness=0)
        self.focus_cv.pack(fill="both",expand=True)
        self.focus_cv.bind("<Configure>",lambda e:self._draw_focus())
        self.focus_tip=tk.Label(p,text="",bg=PANEL2,fg=DIM,font=("Segoe UI",9),
                                 padx=8,pady=4,wraplength=290,justify="left")
        self.focus_tip.pack(fill="x",side="bottom")

    def _build_map(self,p):
        tb=tk.Frame(p,bg=PANEL2,pady=4); tb.pack(fill="x")
        for txt,cmd in [("Fit  F",self._fit),("Zoom +",lambda:self._zoom_c(1.25)),("Zoom −",lambda:self._zoom_c(0.80))]:
            tk.Button(tb,text=txt,bg=PANEL2,fg=TEXT,bd=0,font=("Segoe UI",9),
                      padx=10,pady=2,activebackground=BDR2,activeforeground=TEXT,
                      command=cmd).pack(side="left",padx=4)
        tk.Frame(tb,bg=BORDER,width=1).pack(side="left",fill="y",padx=4)
        self.zlbl=tk.Label(tb,text="100%",bg=PANEL2,fg=DIM,font=("Segoe UI",9))
        self.zlbl.pack(side="left")
        tk.Frame(p,bg=BORDER,height=1).pack(fill="x")

        self.mc=tk.Canvas(p,bg=BG,bd=0,highlightthickness=0,cursor="fleur")
        self.mc.pack(fill="both",expand=True)
        self.mc.bind("<ButtonPress-1>",   self._drag_start)
        self.mc.bind("<B1-Motion>",       self._drag_move)
        self.mc.bind("<ButtonRelease-1>", self._drag_end)
        self.mc.bind("<MouseWheel>",      self._wheel)
        self.mc.bind("<Button-4>",        self._wheel)
        self.mc.bind("<Button-5>",        self._wheel)
        self.mc.bind("<Configure>",       lambda e:self._draw_map())
        self.mc.bind("<Motion>",          self._hover)
        self.bind("<f>",lambda e:self._fit())
        self.bind("<F>",lambda e:self._fit())
        self.bind("<plus>", lambda e:self._zoom_c(1.2))
        self.bind("<minus>",lambda e:self._zoom_c(0.83))

    # ── Data ──────────────────────────────────────────────
    def _populate_maps(self):
        self._map_names=list(self.manifest.keys())
        # Show plain map names — no ✓/✗ prefix
        self.map_combo["values"]=self._map_names

    def _cur_map_name(self):
        val=self.map_var.get().strip()
        return val if val else None

    def _reload_map(self):
        name=self._cur_map_name()
        if not name: return
        if not (RAW/f"{name}.json").exists():
            self.status.set(f"No data for {name}. Run dad_downloader.py first.")
            return
        _img_cache.clear()
        self.status.set(f"Loading {name}…"); self.update_idletasks()
        mode=self.mode_var.get()
        self.modules=load_map(name,mode,self.manifest)
        self._cur_map=name
        self.cfg["last_map"]=name; self.cfg["mode"]=mode
        self.focus_key=None
        # Module list
        self.mod_list.delete(0,"end")
        self._mk_order=sorted(self.modules,key=lambda k:(self.modules[k]["row"],self.modules[k]["col"]))
        for mk in self._mk_order:
            self.mod_list.insert("end",self.modules[mk].get("label",mk))
        self._update_counts()
        self._draw_map(); self._fit()
        n=len(self.modules)
        self.status.set(f"{name}  •  {n} modules  •  {'Normal' if mode=='N' else 'High Roller'}")

    def _on_mod_select(self,*_):
        sel=self.mod_list.curselection()
        if not sel or not hasattr(self,"_mk_order"): return
        idx=sel[0]
        if idx<len(self._mk_order):
            self.focus_key=self._mk_order[idx]
            self._draw_focus()
            self._centre(self.focus_key)

    # ── Filters ───────────────────────────────────────────
    def _filter_changed(self):
        self.visible={c for c,v in self._fvars.items() if v.get()}
        self.cfg["visible_cats"]=list(self.visible)
        self._draw_map(); self._draw_focus()

    def _all_on(self):
        for v in self._fvars.values(): v.set(True)
        self._filter_changed()

    def _all_off(self):
        for v in self._fvars.values(): v.set(False)
        self._filter_changed()

    def _update_counts(self):
        cnt=Counter(i["cat"] for m in self.modules.values() for i in m["items"])
        for cat,lbl in self._cnt_labels.items():
            n=cnt.get(cat,0); lbl.config(text=str(n) if n else "")

    # ── Settings ──────────────────────────────────────────
    def _setting(self,attr,var):
        try: self.cfg[attr]=var.get()
        except: return
        _img_cache.clear(); self._draw_map(); self._draw_focus()
        save_settings(self.cfg)

    def _toggle_labels(self):
        self.cfg["show_labels"]=self.lblvar.get()
        self._draw_map()

    def _reset(self):
        self.cfg={**DEFAULTS}
        for a,v in self._spins.items(): v.set(DEFAULTS[a])
        self.lblvar.set(DEFAULTS["show_labels"])
        self.visible=set(DEFAULT_VISIBLE)
        for c,v in self._fvars.items(): v.set(c in self.visible)
        _img_cache.clear(); self._draw_map(); self._draw_focus()
        save_settings(self.cfg)

    # ── Rendering ─────────────────────────────────────────
    def _tile_px(self): return max(40,int(self.cfg.get("tile_px",180)))
    def _mscale(self):  return float(self.cfg.get("marker_scale",1.0))

    def _draw_map(self,*_):
        c=self.mc; c.delete("all"); self._tkimgs.clear()
        if not self.modules:
            c.create_text(c.winfo_width()//2,c.winfo_height()//2,
                          text="No map data loaded.\nRun dad_downloader.py first.",
                          fill=DIM,font=("Segoe UI",13),justify="center"); return
        tp=self._tile_px(); ms=self._mscale()
        show_lbl=self.cfg.get("show_labels",True)
        for mk,mod in self.modules.items():
            span=mod["span"]; W=span*tp
            wx=mod["col"]*tp; wy=mod["row"]*tp
            img=render_tile(self._cur_map,mk,mod,tp,self.visible,ms)
            sw=max(1,int(W*self._zoom)); sh=sw
            rmethod=Image.NEAREST if self._zoom<0.4 else Image.BILINEAR
            imgs=img.resize((sw,sh),rmethod)
            if mk==self.focus_key:
                d=ImageDraw.Draw(imgs)
                d.rectangle([0,0,sw-1,sh-1],outline=(200,168,75,255),width=3)
            ti=ImageTk.PhotoImage(imgs); self._tkimgs.append(ti)
            cx=wx*self._zoom+self._panx; cy=wy*self._zoom+self._pany
            c.create_image(cx,cy,anchor="nw",image=ti,tags=(f"T:{mk}",))
            if show_lbl and sw>40:
                fs=max(7,min(11,int(8*self._zoom)))
                c.create_text(cx+sw//2,cy+sh-max(2,int(8*self._zoom)),
                              text=mod.get("label",mk),fill="white",
                              font=("Segoe UI",fs),anchor="s")
        self.zlbl.config(text=f"{int(self._zoom*100)}%")

    def _draw_focus(self,*_):
        c=self.focus_cv; c.delete("all"); self._tkimgs=self._tkimgs[-100:]
        if not self.focus_key or self.focus_key not in self.modules:
            c.create_text(10,10,text="Select a module from the list →",
                          fill=DIM,font=("Segoe UI",10),anchor="nw"); return
        mod=self.modules[self.focus_key]
        cw=max(1,c.winfo_width()); ch=max(1,c.winfo_height())
        span=mod["span"]
        fp=max(80,min(cw//span,ch//span,int(self.cfg.get("focus_tile_px",500))))
        ms=float(self.cfg.get("focus_marker_scale",1.8))
        self.focus_title.config(text=mod.get("label",self.focus_key))
        img=render_tile(self._cur_map,self.focus_key,mod,fp,self.visible,ms)
        W=H=span*fp
        imgs=img.resize((min(W,cw),min(H,ch)),Image.LANCZOS)
        ti=ImageTk.PhotoImage(imgs); self._tkimgs.append(ti)
        c.create_image(cw//2,ch//2,anchor="center",image=ti)
        vis=sum(1 for i in mod["items"] if i["cat"] in self.visible)
        tot=len(mod["items"])
        self.focus_tip.config(text=f"{self.focus_key}  •  {vis} markers shown  ({tot} total items)")

    # ── Pan/zoom ──────────────────────────────────────────
    def _fit(self,*_):
        if not self.modules: return
        tp=self._tile_px()
        mc=max(m["col"]+m["span"] for m in self.modules.values())
        mr=max(m["row"]+m["span"] for m in self.modules.values())
        ww=mc*tp; wh=mr*tp
        cw=self.mc.winfo_width() or 800; ch=self.mc.winfo_height() or 600
        s=min((cw-20)/ww,(ch-20)/wh,2.0)
        self._zoom=max(0.05,s)
        self._panx=(cw-ww*self._zoom)/2; self._pany=(ch-wh*self._zoom)/2
        self._draw_map()

    def _zoom_c(self,f):
        cw=self.mc.winfo_width() or 800; ch=self.mc.winfo_height() or 600
        self._zoom_at(f,cw/2,ch/2)

    def _zoom_at(self,f,cx,cy):
        nz=max(0.04,min(8.0,self._zoom*f))
        self._panx=cx-(cx-self._panx)*(nz/self._zoom)
        self._pany=cy-(cy-self._pany)*(nz/self._zoom)
        self._zoom=nz; self._draw_map()

    def _centre(self,key):
        if key not in self.modules: return
        mod=self.modules[key]; tp=self._tile_px(); span=mod["span"]
        wx=mod["col"]*tp+span*tp/2; wy=mod["row"]*tp+span*tp/2
        cw=self.mc.winfo_width() or 800; ch=self.mc.winfo_height() or 600
        self._panx=cw/2-wx*self._zoom; self._pany=ch/2-wy*self._zoom
        self._draw_map()

    def _drag_start(self,e):
        self._drag=(e.x,e.y,self._panx,self._pany)
    def _drag_move(self,e):
        if not self._drag: return
        dx=e.x-self._drag[0]; dy=e.y-self._drag[1]
        self._panx=self._drag[2]+dx; self._pany=self._drag[3]+dy
        self._draw_map()
    def _drag_end(self,e): self._drag=None

    def _wheel(self,e):
        f=1.1 if(getattr(e,"delta",0)>0 or e.num==4) else 0.91
        self._zoom_at(f,e.x,e.y)

    # ── Hover tooltip ─────────────────────────────────────
    def _hover(self,e):
        if not self.modules: return
        tp=self._tile_px(); ms=self._mscale()
        wx=(e.x-self._panx)/self._zoom; wy=(e.y-self._pany)/self._zoom
        best_d=float("inf"); best=None
        for mk,mod in self.modules.items():
            span=mod["span"]; W=span*tp
            ox=mod["col"]*tp; oy=mod["row"]*tp
            bb=mod["bbox"]
            xr=bb["xmax"]-bb["xmin"] or 1; yr=bb["ymax"]-bb["ymin"] or 1
            for item in mod["items"]:
                cfg=CATS.get(item["cat"])
                if not cfg or item["cat"] not in self.visible: continue
                px=ox+((item["x"]-bb["xmin"])/xr)*W
                py=oy+((bb["ymax"]-item["y"])/yr)*W
                hit=max(6,cfg["r"]*ms+2)
                d=math.hypot(wx-px,wy-py)
                if d<hit and d<best_d: best_d=d; best=(item,cfg)
        if best:
            item,cfg=best
            nm=item.get("name") or item.get("id","?")
            self._show_tt(e.x_root+14,e.y_root+12,f"{nm}\n{cfg['label']}")
        else:
            self._hide_tt()

    def _show_tt(self,x,y,txt):
        self._hide_tt()
        tw=tk.Toplevel(self); tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}"); tw.configure(bg=BDR2)
        tk.Label(tw,text=txt,bg="#0e0d14",fg=TEXT,font=("Segoe UI",9),
                 padx=8,pady=5,justify="left").pack()
        self._tt_win=tw

    def _hide_tt(self):
        if self._tt_win:
            self._tt_win.destroy(); self._tt_win=None

    def destroy(self):
        save_settings(self.cfg); super().destroy()

if __name__=="__main__":
    App().mainloop()
