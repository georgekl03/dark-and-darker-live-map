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
6. [Map Scanner V2 Debug Tool (`map_scanner_v2.py`)](#map-scanner-v2-debug-tool-map_scanner_v2py)
7. [Minimap Tracker Debug Tool (`minimap_tracker.py`)](#minimap-tracker-debug-tool-minimap_trackerpy)
8. [Map Calibration Tool (`ui/calibration_app.py`)](#map-calibration-tool-uicalibration_apppy)
9. [CLI Detection Pipeline (`detect_cli.py`)](#cli-detection-pipeline-detect_clipy)
10. [Data Downloader (`dad_downloader.py`)](#data-downloader-dad_downloaderpy)
11. [Troubleshooting](#troubleshooting)
12. [File / Folder Structure](#file--folder-structure)

---

## Requirements

| Package        | Required | Notes                                        |
|----------------|----------|----------------------------------------------|
| Python         | ✅ 3.10+ | <https://www.python.org/downloads/>          |
| Pillow         | ✅       | `pip install Pillow`                         |
| requests       | ✅       | `pip install requests`                       |
| numpy          | optional | Faster image operations                      |
| mss            | optional | Fastest screen capture (recommended)         |
| opencv-python  | optional | Improved contour-based bbox detection        |

Install everything at once:
```bash
pip install Pillow requests numpy mss opencv-python
```

---

## Installation

```bash
git clone <repo-url>
cd dark-and-darker-live-map
pip install Pillow requests numpy mss opencv-python
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
- **Direction** – from the triangular cursor outline around the green dot.

The tracker automatically loads calibrated settings from
`data/minimap_settings.json` when that file exists (created by
`minimap_tracker.py`). Use the **Minimap Tracker Debug Tool** to calibrate
and save your settings before starting tracking.

| Setting | Default | Description                   |
|---------|---------|-------------------------------|
| Left    | 1700    | Screen X of minimap left edge |
| Top     | 860     | Screen Y of minimap top edge  |
| Width   | 220     | Capture width in pixels       |
| Height  | 220     | Capture height in pixels      |

> **Tip:** Run `minimap_tracker.py` once to calibrate the settings for your
> screen resolution and save them.  The main viewer picks them up automatically.

---

## Map Scanner V2 Debug Tool (`map_scanner_v2.py`)

A second-generation scanner that works with **random layouts and variable grid
sizes** by leveraging the faint 10×10 micro-grid present inside every dungeon
module.  Unlike the original scanner it makes no assumptions about the number
of modules or their positions.

### How V2 works

| Stage | What happens |
|-------|--------------|
| 1 – Map bbox | Slide a window over the centre of the screen looking for a large dark rectangle.  A region whose average brightness is below the threshold is accepted as the map area. |
| 2 – Micro-grid | Compute Sobel edge magnitudes on the cropped map.  Sum edges along rows and columns to get two 1-D profiles.  Apply FFT to find the dominant periodic spacing.  Both the micro-cell period (~cell/10 px) and module period (~cell px) are searched; the interpretation that gives the cleanest integer grid count in [2, 10] is kept. |
| 3 – Module grid | Divide the map crop into `n_cols × n_rows` equal tiles, where `n_cols = round(width / module_step_x)`. |
| 4 – Edge matching | Resize each tile to 64 × 64, compute edge magnitudes, and compare against every known module template (all four 90° rotations) using normalised edge MSE.  Raw pixel MSE is avoided because it is sensitive to brightness / contrast differences between the game and template PNGs. |
| 5 – Unique assignment | Sort all (tile, module, score) triples by score and greedily assign: each module key is used at most once and each tile receives at most one module.  Tiles with no candidate below `MATCH_THRESHOLD` are marked unknown. |

### Running the debug tool

```bash
# Interactive GUI – load a screenshot and step through the pipeline
python map_scanner_v2.py

# Load a saved screenshot directly
python map_scanner_v2.py  screenshot.png
```

The GUI toolbar provides:

| Button | Action |
|--------|--------|
| 📂 Load Image | Open a PNG / JPEG screenshot for offline analysis |
| 📷 Screenshot | Capture the primary monitor immediately |
| 🚀 Run Pipeline | Run all five stages on the current image |
| 💾 Save Debug | Save one PNG per pipeline stage to `data/debug/` |

The **View** dropdown switches the canvas between the six pipeline stages:

| View | Shows |
|------|-------|
| original | Raw loaded / captured image |
| bbox | Detected map bounding box (green) and all rejected candidates (yellow) |
| edges | Sobel edge magnitude image of the detected map crop |
| microgrid | Map crop with micro-cell lines (faint) and module-boundary lines (bright) |
| grid | Map crop with module tile boundaries and row/col labels |
| matches | Map crop with tiles coloured by match quality; click a tile to see top-K candidates in the info panel |

### Using V2 inside the main viewer

When a map is loaded in `map_viewer.py`, click the **Scan Map (V2)** toolbar
button (or press the configured keybind if you add one).  The scanner will:

1. Take a screenshot.
2. Detect the map area automatically.
3. Infer the module grid size (3×3, 4×4, … 6×6, etc.).
4. Classify each tile and apply the discovered layout.

The original **Scan Map** button is still present and unchanged.

### Dependencies

| Package | Role |
|---------|------|
| `Pillow` | Required – image loading, rendering, PIL fallback for edge detection |
| `numpy` | Strongly recommended – Sobel edges, FFT period detection (much faster) |
| `mss` | Optional – preferred backend for screenshots |

### Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| "Could not detect map area" | Map too bright, or scaled outside the search range | Open the map fully in-game; if it is very small try lowering `MAP_DARK_THRESH` at the top of `map_scanner_v2.py` |
| Grid shows wrong number of columns | Module boundary lines too faint for FFT to find | Ensure the map has no strong UI overlay; increase game brightness or contrast |
| "No module templates found" | `data/modules/<map>/` is empty | Run `dad_downloader.py` to download map tile PNGs |
| Module matching is poor | Edge NMSE threshold too strict | Raise `MATCH_THRESHOLD` at the top of `map_scanner_v2.py` (default 0.40) |
| Screenshots fail | Neither `mss` nor `Pillow[ImageGrab]` installed | `pip install mss` or `pip install "Pillow[ImageGrab]"` |
| Very slow on large maps | Running without numpy | `pip install numpy` |

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

A full calibration and visualisation tool for the player cursor tracker.
Run it to configure all detection settings, see overlays live, and save your
calibration so `map_viewer.py` automatically picks it up.

```bash
python minimap_tracker.py
```

---

### Toolbar actions

| Button        | Action                                                         |
|---------------|----------------------------------------------------------------|
| Load Image    | Load a static PNG/JPG minimap screenshot for offline analysis  |
| Grab Region   | Capture the configured screen region once                      |
| Start Live    | Start continuous capture (~8 Hz) with live overlays            |
| Stop Live     | Stop continuous capture                                        |
| Analyse       | Re-run detection on the current image with updated parameters  |

**View selector** (top toolbar) switches the canvas between:
- **Composite** – all enabled overlay layers drawn on the minimap image
- **Green Mask** – only the HSV green-dot detection mask (tinted green)
- **Outline Mask** – only the dark-pixel cursor outline mask (tinted red)

---

### Debug overlays (Composite view)

| Layer              | Colour     | Description                                                      |
|--------------------|------------|------------------------------------------------------------------|
| Green blob         | Green tint | Pixels matched by the HSV green-dot threshold                    |
| Outline mask       | Red tint   | Dark pixels in the local window used as cursor outline           |
| Pivot crosshair    | Green ring | Green dot centroid (player position) + cross-hair                |
| R1 / R2 circles   | Yellow/Cyan| The two sampling circles used for direction detection            |
| Circle hit points  | Orange/Cyan| Outline mask intersections on each sampling circle               |
| Cluster midpoints  | Purple     | Mid-angle of each left/right edge cluster                        |
| Raw bisector       | Purple     | Angle midway between the two edge clusters (pre-disambiguation)  |
| Heading arrow      | Red        | Confirmed forward heading direction                              |
| Tip ring           | Magenta    | Cursor tip found by raycast                                      |
| Tracking point     | White dot  | Stable tracking point at configured distance from pivot          |
| Debug text         | White      | Pivot coords, heading, confidence values, cluster count          |

---

### Settings reference

#### Capture Region
These four spinboxes define which part of the screen is captured.
The minimap is always bottom-right; adjust to match your resolution.

| Setting    | Default | Description                          |
|------------|---------|--------------------------------------|
| Left       | 1700    | Screen X (pixels) of minimap left edge |
| Top        | 860     | Screen Y (pixels) of minimap top edge  |
| Width      | 220     | Capture width in pixels                |
| Height     | 220     | Capture height in pixels               |

---

#### Green Dot Detection
The green dot at the player's feet is the primary anchor.  It has a bright
centre fading to darker edges and is not a perfect circle, so HSV thresholding
is used instead of a simple colour match.

All HSV values use OpenCV convention: **H 0–179, S/V 0–255**.

| Parameter              | Default | What it controls                                                 |
|------------------------|---------|------------------------------------------------------------------|
| Hue min / max          | 40–90   | Hue band for green (lower → more yellow-green; higher → blue-green) |
| Saturation min / max   | 80–255  | Reject grey/white pixels (low S) and over-saturated noise (high S) |
| Value (brightness) min | 60      | Include dim dot edges; lower if the dot edges are very dark     |
| Value (brightness) max | 255     | Upper brightness bound (rarely needs changing)                  |
| Morph kernel           | 2       | Morphological-close kernel half-size to join blob fragments     |
| Min area (px²)         | 4       | Discard specks smaller than this                                |
| Max area (px²)         | 400     | Discard false blobs larger than this                            |

**Tip:** Enable the *Green Mask* view mode to see exactly which pixels are matched.

---

#### Cursor Outline Mask
Extracts the black outline of the triangular cursor in a local window around
the pivot.  These dark pixels are what the circle sampling detects.

| Parameter       | Default | What it controls                                                 |
|-----------------|---------|------------------------------------------------------------------|
| Dark threshold  | 60      | Pixels with grey value < this are marked as "black outline"     |
| Local window    | 30      | Half-size of the processing area around the pivot (px)          |
| Morph kernel    | 2       | Close small gaps in the outline mask                            |

**Tip:** Enable the *Outline Mask* view mode to see the extracted cursor outline.
Raise *Dark threshold* if the outline looks broken; lower it if map floor
features are leaking into the mask.

---

#### Direction Detection
Two circles (R1 inner, R2 outer) are sampled around the pivot.  Each circle
produces intersection hits with the outline mask.  The hits cluster into two
groups (left and right edges of the cursor triangle); the angle between them
bisects toward the forward heading.

| Parameter           | Default | What it controls                                                 |
|---------------------|---------|------------------------------------------------------------------|
| R1 – inner radius   | 10      | Inner circle radius; should sit inside the cursor body          |
| R2 – outer radius   | 18      | Outer circle radius; should be near or past the cursor outline  |
| Samples per circle  | 90      | 360 ÷ N = angular step between sample points                    |
| Cluster gap (deg)   | 20      | Angular gap that separates the left and right edge clusters     |
| Min hits per cluster| 2       | Clusters with fewer hits than this are discarded                |

**Tip:** With *Show R1/R2 circles* and *Show hits* enabled, the yellow/cyan
dots should appear on the left and right sides of the cursor triangle.  If the
dots are scattered, reduce *Cluster gap* or increase *Samples*.

---

#### Tip Detection
Optional raycast from the pivot along the detected heading to find the cursor
tip and a stable tracking point.

| Parameter           | Default | What it controls                                         |
|---------------------|---------|----------------------------------------------------------|
| Enable tip raycast  | ✓       | Toggle the entire tip-detection step                     |
| Max raycast dist    | 35      | Maximum distance (px) the raycast searches from pivot   |
| Tracking pt dist    | 3       | Stable tracking point: distance from pivot along heading |

---

#### Temporal Smoothing
EMA (Exponential Moving Average) filter applied to pivot position and heading
each frame to reduce jitter.

| Parameter           | Default | What it controls                                                 |
|---------------------|---------|------------------------------------------------------------------|
| Heading alpha       | 0.4     | Weight for the newest heading sample (1.0 = no smoothing)       |
| Pivot alpha         | 0.5     | Weight for the newest position sample (1.0 = no smoothing)      |
| Max heading delta   | 60      | Maximum allowed heading change per frame (degrees)              |

---

### Save / Load settings

Click **Save Settings** to write all current parameters to
`data/minimap_settings.json`.  `map_viewer.py` loads this file automatically
when *Track* is clicked, so no manual config copying is needed.

Click **Load Settings** to reload the last saved configuration.

---

### Calibration workflow

1. Open the game with the minimap visible in the bottom-right corner.
2. Set **Capture Region** spinboxes to your minimap's screen position.
   Click **Grab Region** to verify the correct area is captured.
3. Switch to **Green Mask** view.  Adjust **Hue / Saturation / Value** ranges
   until *only* the green dot is highlighted.
4. Switch to **Outline Mask** view.  Adjust **Dark threshold** and
   **Local window** until the cursor outline is clearly visible and walls
   are not leaking into the mask.
5. Switch to **Composite** view.  Ensure *Show circles* and *Show hits* are on.
   Adjust **R1** and **R2** so the yellow/cyan hit dots appear on the left and
   right edges of the triangle cursor.
6. Check the **Heading arrow** (red) points in the direction the character faces.
   If it points the wrong way, check *Show bisector* and *Show clusters* to
   diagnose which cluster is dominant.
7. Click **Start Live** and walk around in game.  The overlay should update
   in real-time and the heading arrow should follow the cursor.
8. Click **Save Settings** when satisfied.  The main **map_viewer.py** will
   now use these calibrated parameters automatically.

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



## Map Calibration Tool (`ui/calibration_app.py`)

An interactive desktop application to debug and tune map detection settings with live visual feedback.

### Running the calibration tool
```bash
python ui/calibration_app.py
```
Or, to load an image immediately:
```bash
python ui/calibration_app.py path/to/screenshot.png
```

### Layout
The tool has three panels:
- **Left sidebar** – Settings panels for every detection parameter, with sliders, checkboxes and help text explaining each setting.
- **Centre canvas** – Image display with zoom (mouse wheel) and pan (drag). Shows detection overlays.
- **Right panel** – Stage thumbnails (click to jump to that step) and a debug log.

### Detection modes
| Mode | Description |
|------|-------------|
| Outer Square (Edge+Contour) | Uses Canny edge detection and contour analysis to find the bright outer map border. **Recommended.** |
| Outer Square (Dark Box) | Legacy centred-square dark-area heuristic from V1/V2. Use as fallback. |
| Micro-Grid First | Scans candidate windows for strongest micro-grid periodicity signal; no bbox needed first. |
| Manual Crop | Draw a rectangle on the image to define the map region; skip bbox detection. |

### Stage visualization
After clicking **▶ Run Detection**, eight overlay stages appear in the thumbnail panel:
1. **Original** – raw screenshot
2. **Preprocessed** – gamma/CLAHE/autocontrast/unsharp applied to map crop
3. **Edge Map** – Sobel edge magnitude
4. **BBox Overlay** – full image with detected bbox highlighted
5. **Map Crop** – cropped map region alone
6. **Micro-Grid Overlay** – detected micro-grid lines drawn on map crop (cyan)
7. **Module Grid** – module-level tile boundaries (yellow)

### Settings reference

#### BBox – Outer Border Detection
| Setting | Default | Description |
|---------|---------|-------------|
| `dark_thresh` | 65 | Max mean brightness to accept as map. Raise if "all candidates above threshold". |
| `bbox_method` | mean | Scoring metric: mean/median/trimmed_mean/edge. Use `edge` for varied lighting. |
| `search_margin` | 0.05 | Fraction of screen to ignore at each edge. |
| `min_frac` / `max_frac` | 0.20 / 0.90 | Map size range as fraction of inner region. |
| `prefer_darkest` | off | Always pick darkest patch regardless of threshold. |
| `use_edge_contour` | on | Edge+contour method for bright outer border (recommended). |
| `canny_low` / `canny_high` | 30 / 100 | Canny edge thresholds. Lower if border not found. |
| `min_border_brightness` | 80 | Min brightness for outer border to qualify. |
| `contour_min_area_frac` | 0.05 | Min contour area as fraction of image. |
| `contour_max_area_frac` | 0.85 | Max contour area as fraction of image. |
| `contour_min_solidity` | 0.70 | How filled the contour must be (convex hull ratio). |

#### BBox – Refinement
| Setting | Default | Description |
|---------|---------|-------------|
| `bbox_refine` | on | Edge-snap the seed bbox outward to nearest strong edges. |
| `bbox_refine_band_pct` | 0.12 | Band width near each edge for refinement sampling. |
| `bbox_refine_max_expand_pct` | 0.10 | Max expansion per side as fraction of bbox size. |

#### Micro-Grid Detection
| Setting | Default | Description |
|---------|---------|-------------|
| `min_micro` / `max_micro` | 2 / 30 | Search range for micro-cell period in pixels. |
| `min_module` / `max_module` | 15 / 500 | Search range for module period in pixels. |
| `micro_cells` | 10 | Sub-cells per module (always 10 in D&D). |
| `force_micro_period` | 0 | Force exact micro-cell period (0 = auto). |
| `force_module_period` | 0 | Force exact module period (0 = auto). |
| `min_grid_size` / `max_grid_size` | 2 / 10 | Allowed module count range per axis. |

#### Preprocessing
| Setting | Default | Description |
|---------|---------|-------------|
| `gamma` | 1.0 | Gamma correction. < 1.0 brightens (helps faint grids). |
| `clahe` | off | CLAHE contrast enhancement — dramatically improves faint micro-grid visibility. |
| `autocontrast` | off | Simple autocontrast stretch. |
| `unsharp` | off | Unsharp mask sharpening for blurry grid lines. |

### Saving and loading presets

After tuning settings for your screen/resolution, save them as a preset:
1. Click **💾 Save Preset** and enter a name (e.g. `dark-dungeon-1080p`).
2. The preset is saved to `data/debug/presets/presets.json`.
3. Load it on next run via **📂 Load Preset**.

Presets are automatically loaded by `detect_cli.py` using `--preset {name}`.

### Manual crop workflow
1. Select **Manual Crop** mode from the mode dropdown.
2. Click **Draw Crop Rectangle** in the Manual Crop section.
3. Click and drag on the image to draw the map region.
4. Click **▶ Run Detection** — bbox stage is skipped; detection runs on your drawn crop.
5. This is useful when automatic bbox detection fails or when testing micro-grid detection on a known-good crop.

---

## CLI Detection Pipeline (`detect_cli.py`)

Run detection on an image from the command line and emit debug artifacts for headless testing.

### Basic usage
```bash
python detect_cli.py screenshot.png
```

### Options
```
positional arguments:
  image_path            Path to input image (PNG or JPG)

optional arguments:
  --mode {edge_contour,dark_box,microgrid_first}
                        Detection mode (default: edge_contour)
  --output-dir DIR      Output directory (default: data/debug/cli_output)
  --dark-thresh N       Max brightness threshold for dark-box mode (default: 65)
  --canny-low N         Canny lower threshold (default: 30)
  --canny-high N        Canny upper threshold (default: 100)
  --min-border-brightness N
                        Min brightness for outer border region (default: 80)
  --gamma F             Gamma correction (default: 1.0)
  --clahe               Enable CLAHE contrast enhancement
  --autocontrast        Enable autocontrast
  --unsharp             Enable unsharp mask
  --force-micro-period N  Force micro-cell period in pixels (0 = auto)
  --force-module-period N Force module period in pixels (0 = auto)
  --min-grid-size N     Minimum module count per axis (default: 2)
  --max-grid-size N     Maximum module count per axis (default: 10)
  --preset NAME         Load settings from data/debug/presets/presets.json
  --save-preset NAME    Save used settings as a new preset after running
  --no-images           Skip saving debug images, only save report.json
  --verbose             Print detailed detection log to stdout
```

### Outputs
All files are written to `--output-dir` (default: `data/debug/cli_output/`):

| File | Description |
|------|-------------|
| `report.json` | Full detection report with bbox, microgrid metrics, logs and timings |
| `01_original.png` | Original input image |
| `02_preprocessed.png` | Preprocessed map crop |
| `03_edges.png` | Edge magnitude image |
| `04_bbox_overlay.png` | Image with detected bbox drawn |
| `05_map_crop.png` | Isolated map crop |
| `06_microgrid_overlay.png` | Map crop with micro-grid lines |

### Example: saving a preset from CLI
```bash
python detect_cli.py screenshot.png --clahe --gamma 0.7 --save-preset dark-dungeon
```

Then reuse it:
```bash
python detect_cli.py new_screenshot.png --preset dark-dungeon
```

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
├── map_viewer.py              Main map viewer application
├── map_scanner.py             Standalone scanner debug tool
├── minimap_tracker.py         Minimap calibration & debug tool
├── cursor_detect.py           Shared cursor detection module
├── dad_downloader.py          Data download and management CLI
├── README.md                  This file
└── data/
    ├── map_manifest.json      Map list (auto-downloaded)
    ├── minimap_settings.json  Cursor tracking calibration (saved by minimap_tracker.py)
    ├── raw/                   Per-map JSON data files
    │   └── <MapName>.json
    ├── modules/               Module PNG tiles
    │   └── <MapName>/
    │       └── <ModuleKey>.png
    ├── loot/                  Loot spawn data JSONs
    └── icons/                 Item icon PNGs
```
