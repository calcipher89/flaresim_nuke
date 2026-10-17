# FlareSim+ facility install

How to deploy FlareSim+ to a whole studio (shared, read-only install), where it looks for
its files, the environment variables a pipeline TD can set, and quick checks when the
Lens Browser or a node doesn't work. Examples use Linux and tcsh (`setenv`); in bash use
`export NAME=value`.

## 1. Install layout

Unzip a release (`FlareSim_v<ver>_Nuke<nv>_<os>.zip`) and put its folder on the Nuke plugin
path (studio `NUKE_PATH`, or `nuke.pluginAddPath(...)` in a facility `init.py`). It can be
read-only.

Two layouts work:

- **One folder** (as the zip unpacks): everything side by side.
- **Split**, like the original FlareSim: the `.py` files, `icons/`, `lenses/` and `looks/` in
  one folder (e.g. `/software/apps/flareSimPlus/<ver>/nuke/`) and the compiled files in a
  per-version folder (e.g. `.../nuke/15.1/EL9.x86_64/`). Both folders go on the plugin path.

| File / folder | Used for | Found |
|---|---|---|
| `FlareSimPlus.so`, `FlareSimPlus3D.so` | the nodes | on the Nuke plugin path |
| `menu.py`, `FlareSimPlus_*.py`, `icons/` | menus, panel, Lens Browser button | on the Nuke plugin path (also Python `sys.path`) |
| `flaresim_preview.so` | Lens Browser preview and thumbnails (CPU, no CUDA) | next to `FlareSimPlus_LensBrowser.py` or next to the loaded `FlareSimPlus.so`, or `FLARESIM_PREVIEW_LIB` |
| `lenses/` | bundled lens library (1,370+) | next to the `.py` files or the plugin |
| `looks/` | starter looks | next to the `.py` files or the plugin |

Releases before the split-layout support only look next to the `.py` files for the
preview library: set `FLARESIM_PREVIEW_LIB` in the Nuke wrapper for those.

## 2. Environment variables

All are read when Nuke starts.

```
setenv FLARESIM_LENS_PATH  /show/flare/lenses:/studio/flare/lenses
setenv FLARESIM_LOOKS_PATH /studio/flare/looks
```

| Variable | What it does |
|---|---|
| `FLARESIM_LENS_PATH` | Extra lens folders, `:`-separated (`;` on Windows), searched recursively. Shown as **Studio** in the Lens Browser's Library filter. The node also searches them at render time, so **the farm needs the same value**. |
| `FLARESIM_LOOKS_PATH` | Shared look folders. Shown as **Studio** looks. |
| `FLARESIM_PREVIEW_LIB` | Full path to `flaresim_preview.so` when it isn't next to the `.py` files or the plugin. GUI only; the farm doesn't need it. |
| `FLARESIM_DEBUG=1` | Verbose logging (same as Help > FlareSim+ > Debug Logging On or Off). |
| `FLARESIM_PANEL_STYLE=0` | Plain Nuke panel, no header styling (safety switch). |
| `FLARESIM_TRACE_LOG=<file>` | Viewer refresh trace, only for black-frame issues. |

**Lens search order** (node and Lens Browser): the path stored in the script, then
`FLARESIM_LENS_PATH`, then the install's `lenses/`, then `~/.nuke/FlareSim/lenses`; first by
the path under `lenses/`, then by file name. So scripts made on another machine, another OS
or an older personal install still find the same lens in the facility copy.

**Look order:** `~/.nuke/FlareSim/looks` (Mine) wins over `FLARESIM_LOOKS_PATH` (Studio),
which wins over the install's `looks/` (Starter).

## 3. What each artist needs writable

Created automatically in the artist's home; if it's read-only these features stop working:

- `~/.nuke/FlareSim/thumbnails`: thumbnail cache (built in the background on first open)
- `~/.nuke/FlareSim/looks`: Save as Look
- `~/.nuke/FlareSim/lenses`: Import Lens (that machine only; use `FLARESIM_LENS_PATH` for shared lenses)
- `~/.nuke/FlareSim/headers`: panel header images
- `~/.nuke/FlareSim/logs`: error log
- `~/.config/FlareSim/LensBrowser.conf`: window size, exposure, preview light

## 4. Quick checks

**A. Which copy is loaded?** An old personal install still on the path can shadow the
facility one. In Nuke's Script Editor:

```
import FlareSimPlus_LensBrowser as b; print(b.__file__)
```

It should print the facility folder. If it prints a home folder, take that folder off the
path (`~/.nuke/init.py`, NUKE_PATH) and restart.

**B. Preview library:**

```
print(b.preview_library())
```

`(<CDLL ...>, None)` is good. Otherwise the message says what's wrong: not found (with the
folders searched), or a version mismatch (an old `.so` with new `.py` files).

**C. Lens list:**

```
import os
print([(f, os.path.isdir(f)) for f, _ in b.lens_folders()])
print(len(b.scan_lenses(b.lens_folders())))
```

Every folder should say `True`; the count should be 1,370 or more.

**D. Self test:** Help > FlareSim+ > Self Test, or on a farm node
`nuke -t <install>/FlareSimPlus_SelfTest.py`. It checks the GPU and driver, plugin loading,
the lens library, writable folders and a test render.

**E. Debug report:** Help > FlareSim+ > Debug Logging On or Off, repeat the problem, then
Help > FlareSim+ > Save Debug Report (`~/FlareSim_debug_<host>_<time>.txt`). Errors are always
logged to `~/.nuke/FlareSim/logs/FlareSim_<host>.log`.

### Symptom to cause

| Symptom | Likely cause |
|---|---|
| Lens Browser button gives "No module named FlareSimPlus_LensBrowser" | `.py` folder not on the plugin path |
| Window opens with no lenses | `lenses/` missing from the install |
| Lenses listed, grey thumbnails and blank preview | `flaresim_preview.so` missing or not found; check B, or set `FLARESIM_PREVIEW_LIB` |
| Thumbnails rebuild every session | `~/.nuke/FlareSim/thumbnails` not writable |
| Studio lenses missing | `FLARESIM_LENS_PATH` not set in Nuke's environment |
| Node shows a red lens error | lens not in any searched folder; add its folder to `FLARESIM_LENS_PATH` (farm too) |

## 5. Before rolling out

1. Self Test on a workstation and as a farm job.
2. A farm render of a shot with a FlareSim+ node, compared to a GUI render of the same frame
   (command-line renders always use full Quality).
3. The Lens Browser from an artist account that never had a personal install (clean home).
4. An older script with FlareSim+ nodes: the lens is still found.
5. A script with original FlareSim nodes: Help > FlareSim+ > Convert FlareSim Nodes, then render.
