# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

FlareSim+ is a physically based lens flare plugin for Foundry Nuke: rays are traced through real lens prescriptions (`.lens` files) on the GPU, and the result is composited over the plate. It is an artist-focused fork of LocalStarlight/flaresim_nuke (via petermercell/flaresim_nuke).

## Build and test

The Nuke nodes need the proprietary Nuke NDK and CUDA, so they cannot be built here or in CI. Everything else is CPU-only:

```bash
cmake -S . -B build -DFLARESIM_BUILD_PLUGINS=OFF   # preview library + tests only
cmake --build build -j
ctest --test-dir build --output-on-failure
ctest --test-dir build -R ghost_overrides          # one test (names: optics_smoke, lens_path,
                                                   # haze_starburst, ghost_overrides, ghost_mesh, preview_render)
python3 -m py_compile nuke/*.py lenses/*.py        # CI's only Python check (no linter)
```

Full plugin build (machine with Nuke + CUDA): `cmake -S . -B build -DNUKE_VERSION=15.1v10 && cmake --build build --config Release -j && cmake --install build --prefix ~/.nuke/plugins/FlareSim`. Note `cmake --install` also overwrites the installed `looks/` and `lenses/` folders.

- Multi-version builds: `scripts/build_linux.sh`, `scripts/build_docker.sh` (ASWF images, one per Nuke major), `scripts/build_windows.ps1`; output in `dist/nuke<N>/`. Zip with `scripts/package_release.sh --version <x.y.z> --nuke-versions "15"` into `release_packages/`.
- Side-by-side edition: `-DFLARESIM_EDITION=Beta` (or `--edition Beta` on the scripts) renames node classes to `FlareSimPlusBeta*` via `src/edition.h` and generates `FlareSimPlusBeta_*.py` with `scripts/make_edition.py`, so it loads next to the regular build.
- CI (`.github/workflows/ci.yml`) runs the CPU tests on Linux and MSVC, compiles the `.cu` kernels standalone with nvcc (GCC and MSVC hosts), and py_compiles the Python. CUDA code must compile with MSVC as host compiler.
- Platform docs: `docs/BUILD_LINUX.md`, `docs/BUILD_WINDOWS.md`, `docs/BUILD_MACOS.md`, `docs/FACILITY_INSTALL.md`. Nuke 14 on Linux needs the old GCC ABI (set automatically from `NUKE_VERSION`). CUDA runtime is linked statically (driver 525+, sm_70+).

## Architecture

Three consumers share one optics core (`FLARESIM_CORE_SOURCES` in `CMakeLists.txt`: `lens.cpp`, `trace.cpp`, `ghost.cpp`, `lens_path.cpp`, `extras.cpp`, `ghost_streaks.cpp`):

1. **Nuke nodes** `src/FlareSim.cpp` (2D: Auto Detect bright pixels, or a placed Manual XY light; optional `cam` input) and `src/FlareSim3D.cpp` (driven by Camera + Axis). They are separate, largely parallel implementations, so a node-level feature usually has to be added to both. Shared node pieces live in headers: `node_ui.h` (Quality presets, Clip To/overscan, Show Advanced list), `extras_knobs.h` (haze/starburst/rays/ring knobs), `layers.h` (output AOVs), `occlusion.h`, `ghost_overrides.h`.
2. **GPU backend**: `ghost_cuda.cu` (ghost tracing, point splat and the "Smooth" mesh render) and `blur_cuda.cu` on Linux/Windows; `src/metal/` on macOS. The Metal port is behind (no haze, starburst, dirt, layers, anamorphic layout) and is parked. Haze, starburst, rays, ring and lens dirt are CPU-side in `extras.cpp`.
3. **Preview library** `flaresim_preview` (`src/preview.cpp`, C API in `src/preview.h`): CPU-only renderer loaded by the Python Lens Browser through ctypes. `FspParams` and the other structs must stay byte-for-byte in sync with the ctypes `Structure`s in `nuke/FlareSimPlus_LensBrowser.py`; bump `FSP_API_VERSION` on any ABI change. The preview traces fewer wavelengths than the node, so its brightness/colour for some ghosts differs from a Nuke render.

Ghosts are identified by their two bounce surfaces (`A-B`, e.g. `3-12`). Per-surface art direction (Surfaces tab, `surf_*` knobs) multiplies into every ghost sharing a surface; the `ghost_overrides` text knob (`3-12 gain=2 color=r,g,b scale=x,y offset=x,y blur= streak=g,l,t,e` or `off`; parser in `ghost_overrides.h`, documented at its top) adjusts single ghosts on top.

**Python side** (`nuke/`, installed next to the compiled plugins; the node's Lens Browser button imports `FlareSimPlus_LensBrowser` via `FSP_MODULE`):
- `menu.py` registers nodes and Help > FlareSim+ menus. New nodes are created with `ghost_render Smooth`, while the knob default stays Points so old scripts render unchanged; follow this pattern when changing defaults.
- `FlareSimPlus_LensBrowser.py`: standalone PyQt window with live preview, Lens Elements tab, Pick a Ghost, Apply to Node (writes only knobs changed in the browser).
- `FlareSimPlus_Looks.py`: looks are JSON (`looks/*.json`: lens path + `knobs` dict). `LOOK_KNOBS` lists what a look saves; shot-specific knobs are excluded. Search order: `~/.nuke/FlareSim/looks`, `FLARESIM_LOOKS_PATH`, bundled `looks/`.
- `FlareSimPlus_Header.py` (styled panel header; `FLARESIM_PANEL_STYLE=0` disables), `FlareSimPlus_Preview.py` (Viewer Quality: GUI-only reduced quality, renders stay full), `FlareSimPlus_Log.py` (`~/.nuke/FlareSim/logs`, Debug Report), `FlareSimPlus_SelfTest.py` (`nuke -t FlareSimPlus_SelfTest.py`), `FlareSimPlus_Convert.py` (convert original `FlareSim` nodes).

**Adding a flare knob** typically touches: the knob struct/`knobs()` in both nodes (or `extras_knobs.h`), the render code (`extras.cpp` or `ghost_cuda.cu`), `preview.h`/`preview.cpp` + the ctypes mirror if the Lens Browser should show it, `LOOK_KNOBS` in `FlareSimPlus_Looks.py`, and a test in `src/*_test.cpp`. Keep defaults such that existing scripts and looks render the same (looks saved before a knob existed fall back to its default; older builds ignore unknown look keys).

**Lens lookup** (`lens_path.cpp`): if a script's lens path is missing, the node searches `FLARESIM_LENS_PATH`, the bundled `lenses/`, and `~/.nuke/FlareSim/lenses`, by relative path under `lenses/` then by file name, and errors rather than rendering nothing. `lenses/` holds 1,370+ prescriptions (`lens_files/`, `anamorphic/`) plus converter scripts; the `.lens` format and anamorphic surface types (`cyl_x`, `cyl_y`, `toric`, `squeeze:`) are documented in `README.md`.

## Conventions

- Production deployment is airgapped Linux studios and a GPU render farm: favour robustness, clear errors naming the machine, and logging over silent fallbacks. A missing GPU must fail the render, not write frames without a flare.
- User data lives in `~/.nuke/FlareSim/` (looks, lenses, logs) and is shared by all editions; environment variables are all `FLARESIM_*` (`FLARESIM_DEBUG`, `FLARESIM_TRACE_LOG`, `FLARESIM_PREVIEW_LIB`, `FLARESIM_LENS_PATH`, `FLARESIM_LOOKS_PATH`).
- `CHANGELOG.md`: add a section per release (newest first, user-facing wording, PR numbers). Releases are tagged `v<x.y.z>` on the commit the zip was built from.
