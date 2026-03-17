# Dark & Darker Live Map

An interactive loot-and-module map tool for **Dark and Darker**.
Displays every map module, all loot spawn locations with category filters, a
focus view for individual modules, optional minimap-based player tracking, and
a standalone screen-scanner/debug toolset.

---

## Table of Contents

1. [Requirements](#requirements)
2. [Installation](#installation)
3. [First Run – Download Map Data](#first-run--download-map-data)
4. [Map Viewer (`map_viewer.py`)](#map-viewer-map_viewerpy)
   - [Layout overview](#layout-overview)
   - [Selecting a map and mode](#selecting-a-map-and-mode)
   - [Module list and focus view](#module-list-and-focus-view)
   - [Loot Filters](#loot-filters)
   - [Map canvas controls](#map-canvas-controls)
   - [Settings window](#settings-window)
   - [Keybinds](#keybinds)
   - [Scan Map feature](#scan-map-feature)
   - [Player tracking](#player-tracking)
5. [Map Scanner Debug Tool (`map_scanner.py`)](#map-scanner-debug-tool-map_scannerpy)
6. [Minimap Tracker Debug Tool (`minimap_tracker.py`)](#minimap-tracker-debug-tool-minimap_trackerpy)
7. [Data Downloader (`dad_downloader.py`)](#data-downloader-dad_downloaderpy)
8. [Troubleshooting](#troubleshooting)
9. [File / Folder Structure](#file--folder-structure)

---

## Requirements

| Package    | Required | Notes                                  |
|------------|----------|----------------------------------------|
| Python     | ✅ 3.10+ | <https://www.python.org/downloads/>    |
| Pillow     | ✅       | `pip install Pillow`                   |
| requests   | ✅       | `pip install requests`                 |
| numpy      | optional | Faster image operations                |
| mss        | optional | Fastest screen capture (recommended)   |

Install everything at once:
```bash
pip install Pillow requests numpy mss
```

---

## Installation

```bash
git clone <repo-url>
cd dark-and-darker-live-map
pip install Pillow requests numpy mss
```

---

## First Run – Download Map Data

Before launching the map viewer you must download the game data:

```bash
python dad_downloader.py
```

Follow the interactive menu:
- **Option 1 – Download all** downloads the manifest, all map JSONs, and all
  valid module PNG tiles.  This is the recommended first step.
- **Option 2 – Download loot data** grabs loot spawn information.
- **Option 3 – Download icons** fetches small icons for loot items.
- **Option 4 – Purify PNGs** scans existing PNG files and removes any that are
  not valid (e.g. HTML error pages saved as `.png`).  This now happens
  automatically during download too.
- **Option 5 – Status** shows what is already cached locally.

> **Note:** The downloader validates every PNG against the 8-byte PNG signature
> after downloading.  Files that fail validation (e.g. server error pages) are
> discarded immediately and do **not** accumulate in the cache.

---

## Map Viewer (`map_viewer.py`)

Launch with:
```bash
python map_viewer.py
```

### Layout overview

```
+----------------------------+--------------------------------------+
|  ⚔  D&D Map Viewer         |                                      |
+----------------------------+  Full-height Map Canvas              |
|  Focus View  (top-left)    |  • Pan: left-click drag              |
|  Shows selected module     |  • Zoom: mouse wheel                 |
|  at high detail            |  • Hover: tooltip with item name     |
+-----------+----------------+                                      |
|  Sidebar  (bottom-left)    |                                      |
|  • Map & mode select       |                                      |
|  • Module list             |                                      |
|  • Loot Filters            |                                      |
|  • Status bar + ⚙ settings |                                      |
+----------------------------+--------------------------------------+
```

### Selecting a map and mode

1. Choose a map from the **Map** dropdown in the sidebar.
2. Toggle **Normal** / **High Roller** with the Mode radio buttons.
   The map reloads automatically.

### Module list and focus view

- The **Modules** list shows every module for the current map.
- Click any module to load it into the **Focus View** (top-left panel).
- The focus view uses a larger tile size and higher marker scale so you can see
  fine detail for that room.

### Loot Filters

The filter section is grouped by category (Chest, Weapon, Armour, etc.).
Two checkboxes per item control visibility independently:

| Column  | Controls                  |
|---------|---------------------------|
| Focus   | Visibility in Focus View  |
| Map     | Visibility in Map Canvas  |

The **Focus** column is on the left (matching the left-panel focus view) and
**Map** is on the right (matching the right-panel map canvas).

Buttons:
- **ALL** / **NONE** at the top of the section toggle *every* category at once.
- Each category group also has its own **ALL** / **NONE** buttons.

### Map canvas controls

| Action                        | Control                  |
|-------------------------------|--------------------------|
| Pan                           | Left-click drag          |
| Zoom in / out                 | Mouse wheel or `+`/`-`   |
| Fit whole map to window       | `F`                      |
| Scan screen for map           | `Shift+M` (configurable) |
| Item tooltip                  | Hover over a marker      |

### Settings window

Open via the **⚙** gear button in the bottom-left corner.

| Setting              | Description                                    |
|----------------------|------------------------------------------------|
| Map tile size        | Size in pixels of each module tile on the map  |
| Focus tile size      | Tile size in the focus view                    |
| Marker scale (map)   | Size multiplier for loot markers on the map    |
| Marker scale (focus) | Size multiplier for markers in the focus view  |
| Show module names    | Draw module key labels on the map              |
| Keybinds             | Configurable keyboard shortcuts (see below)    |

Click **Save Keybinds** to apply keybind changes.  **Reset to Defaults**
restores all settings including keybinds.

Settings are saved to `data/settings.json` automatically when the window closes.

### Keybinds

Default keybinds:

| Action    | Default key |
|-----------|-------------|
| Scan Map  | `Shift+M`   |
| Fit View  | `f`         |
| Zoom In   | `plus`      |
| Zoom Out  | `minus`     |

To change a keybind:
1. Open ⚙ Settings.
2. Type the new key name in the text field next to the action (e.g. `Shift+M`,
   `f`, `plus`, `ctrl+r`).
3. Leave the field **blank** to disable the keybind.
4. Click **Save Keybinds**.

### Scan Map feature

Press `Shift+M` (or your configured keybind) while the dungeon in-game map is
**visible on screen**.  The scanner will:

1. Take a screenshot of your primary monitor.
2. Automatically locate the dark square of the dungeon map, regardless of
   screen resolution or UI scale.
3. Extract each module cell from the detected region.
4. Template-match against all known module PNGs (0°, 90°, 180°, 270°).
5. Apply matched positions to the map viewer layout.

**Requirements:** `mss` or `Pillow[ImageGrab]` must be installed.
**Note:** Open your in-game map fully before scanning.

### Player tracking

Click **Track** in the map toolbar to start/stop live player tracking.
The tracker captures a small screen region (the minimap) on a background thread
(~8 Hz) and detects:
- **Position** – from the green dot at the player's feet.
- **Direction** – from the pale arrow cursor around the green dot.

Configure the minimap region in **`minimap_tracker.py`** (see [Minimap Tracker Debug Tool](#minimap-tracker-debug-tool-minimap_trackerpy)
for calibration steps), then set the same values in the `_region` defaults at the top of your tracker config.

| Setting         | Default | Description                  |
|-----------------|---------|------------------------------|
| Left            | 1700    | Screen X of minimap left edge|
| Top             | 860     | Screen Y of minimap top edge |
| Width           | 200     | Capture width in pixels      |
| Height          | 200     | Capture height in pixels     |

Use `minimap_tracker.py` to calibrate these values visually.

---

## Map Scanner Debug Tool (`map_scanner.py`)

A standalone tool for developing and testing the screen scanner.

```bash
python map_scanner.py
```

### Features

- **Load Image** – load any PNG/JPG screenshot to analyse offline.
- **Screenshot** – capture your live screen.
- **Detect Map Area** – runs the dark-region detector and draws a green box
  around the detected area.  Also shows in the Analysis Log:
  - the exact pixel coordinates found
  - suggestions if detection fails
- **Show Grid** – overlays the expected module grid on the detected area.
- **Match Modules** – runs template matching for all modules and shows:
  - each module's error score and best rotation
  - green outlines for accepted matches (`error < 0.35`)
  - red outlines for rejected matches
- **View selector** – switch between Original / Map Area / Grid / Matches views.
- **Map selector** – choose any downloaded map to use as the template source.
- **Analysis Log** – detailed text output for every step.

### Passing in test images

Load any screenshot taken while the map was open.  The tool works completely
offline (no game required) which makes it ideal for:
- Testing new maps after a game update.
- Calibrating the detection threshold.
- Debugging module mismatches.

---

## Minimap Tracker Debug Tool (`minimap_tracker.py`)

A standalone tool for developing and testing the player-position tracker.

```bash
python minimap_tracker.py
```

### Features

- **Load Image** – load a static minimap screenshot for offline analysis.
- **Grab Region** – capture the configured screen region once.
- **Start Live / Stop Live** – toggle continuous capture at ~8 Hz with live overlay.
- **Analyse** – reprocess the current static image with updated parameters.

### Debug overlays

When a player is detected, the image shows:
- 🟢 **Green circle** around the detected green dot (player position).
- 🟡 **Yellow ring** at the sampling radius used for direction detection.
- 🔴 **Red arrow** indicating the estimated facing direction.

### Detection parameters

| Parameter        | Default | Description                                         |
|------------------|---------|-----------------------------------------------------|
| Min G value      | 120     | Minimum green channel brightness for "green" pixels |
| G / R ratio      | 1.8     | Green must be ≥ this × red                         |
| G / B ratio      | 1.8     | Green must be ≥ this × blue                        |
| Dot radius (px)  | 4       | Visual size of the position circle overlay          |
| Ring radius (px) | 15      | Radius of direction-sampling ring                   |
| Sample points    | 36      | Angular resolution (36 = 10° steps)                 |
| Bright threshold | 160     | Minimum brightness on ring to count as cursor       |

### Calibration workflow

1. Set the **Capture Region** spinboxes to match your minimap position on screen.
2. Click **Grab Region** to capture the current frame.
3. Adjust **Min G value** until only the green dot is highlighted (not the floor).
4. Adjust **Ring radius** until the yellow ring sits inside the cursor arrow.
5. Adjust **Bright threshold** until the red arrow points the correct direction.
6. Click **Start Live** to confirm tracking works in real-time.

### Why green-dot detection is robust

The game minimap contains:
- **Dark grey/brown** floor tiles
- **Black** boundary walls
- **Pale/white** player cursor arrow with black outline
- **Green** dot at the exact player foot position

The green dot is spectrally unique on the minimap – no other element has a
dominant green channel.  Filtering by colour isolates it reliably even when
the cursor overlaps a wall boundary.  For direction, the pale cursor tip is
the next most distinctive feature: it is significantly brighter than the
floor and uses a different greyscale from the wall borders.

---

## Data Downloader (`dad_downloader.py`)

```bash
python dad_downloader.py
```

Interactive menu options:

| Option | Action                                               |
|--------|------------------------------------------------------|
| 1      | Download everything (manifest + all maps + PNGs)     |
| 2      | Download loot data JSON                              |
| 3      | Download loot item icons                             |
| 4      | Purify PNGs (scan and remove invalid files)          |
| 5      | Show download status                                 |
| 0      | Exit                                                 |

**PNG validation:** The downloader validates every PNG immediately after
download.  If the server returns an HTML error page instead of a real image
(common for map modules that do not exist – e.g. some Abyss variants and
placeholder entries) the file is discarded and not saved to disk.  This keeps
`data/modules/` clean without any manual purification step.

---

## Troubleshooting

### "No data for \<map\>. Run dad_downloader.py first."
You have not downloaded data for that map yet.  Run:
```bash
python dad_downloader.py
```
and choose option 1 or select the specific map.

### "[Scan] Could not detect dungeon map on screen."
- Make sure your **in-game map is fully open** when you press the scan keybind.
- The detector looks for a large dark rectangle in the centre of the screen.
  If your UI hides the map or the background is not dark enough, it may fail.
- Try using the **Map Scanner debug tool** (`python map_scanner.py`) to see
  exactly what the detector found.
- Adjust `_MAP_DARK_THRESHOLD` in `map_scanner.py` if your game brightness is
  set very high.

### Player tracking shows wrong position
- Open `minimap_tracker.py` and load a captured minimap screenshot.
- Verify the green dot circle appears on the correct position.
- Adjust **Min G value** and the G/R, G/B ratios until the green dot is cleanly
  isolated.
- If direction is wrong, increase or decrease **Ring radius** so the yellow ring
  sits just inside the cursor arrow tip.

### Modules appear as blank grey squares on the map
The PNG for that module is missing or invalid.  Run:
```bash
python dad_downloader.py
```
and choose **Option 4 (Purify PNGs)** followed by **Option 1 (Download)** with
force-refresh enabled.

### Loot markers are not showing
Check that the relevant categories are enabled in **Loot Filters**.  Use the
global **ALL** button at the top of the filter section to re-enable everything.

### Settings are not saved between sessions
Settings are stored in `data/settings.json`.  Make sure the `data/` folder is
writable.  If the file is corrupted, delete it and restart the app to regenerate
defaults.

### App window is blank / crashes on startup
Confirm Pillow is installed:
```bash
python -c "from PIL import Image; print('OK')"
```
If that fails: `pip install Pillow`

---

## File / Folder Structure

```
dark-and-darker-live-map/
├── map_viewer.py          Main map viewer application
├── map_scanner.py         Standalone scanner debug tool
├── minimap_tracker.py     Standalone minimap tracker debug tool
├── dad_downloader.py      Data download and management CLI
├── README.md              This file
└── data/
    ├── map_manifest.json  Map list (auto-downloaded)
    ├── raw/               Per-map JSON data files
    │   └── <MapName>.json
    ├── modules/           Module PNG tiles
    │   └── <MapName>/
    │       └── <ModuleKey>.png
    ├── loot/              Loot spawn data JSONs
    └── icons/             Item icon PNGs
```
