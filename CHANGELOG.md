# Changelog

Major updates and bug fixes for FlareSim+, newest first. Version numbers match the release zips made with `scripts/package_release.sh --version <x.y.z>`. Pull request numbers link to the full details.

## Unreleased

### Added
- Ghost Render: **Smooth** draws each ghost as a filled mesh, so large ghosts come out clean instead of grainy. New nodes start on Smooth; old scripts keep Points. (#43)
- Viewer Quality: a lower Quality for the Viewer only; renders always use full Quality. (#46)
- `FLARESIM_TRACE_LOG`: set it to a file path and both nodes log each update, compute (and whether Nuke cancelled it) and first row served, for tracking down Viewer refresh problems.

### Changed
- Smooth renders much faster, about 7x with many Auto Detect lights. (#44, #45)
- A render Nuke cancels (a knob changed mid-render) now stops within a GPU batch or two instead of finishing the frame, so changing knobs at High no longer stalls the UI for seconds.

### Fixed
- The Viewer could stay black for a frame after a parameter change until the frame changed: a compute Nuke cancelled part way was kept as the finished result.

## 1.2.0 (2026-10-13)

### Added
- 15 ready-made front anamorphic lenses in `lenses/anamorphic/` (Summilux-C 40/100, Summilux-M 50, Nikkor 35/85 at 1.33x, 1.8x and 2x squeeze), plus `lenses/make_anamorphic.py` to turn any spherical lens into an anamorphic one. (#41)

### Fixed
- Linux: knobs on the FlareSimPlus tab vanished after switching to another tab and back. (#40)
- Linux: extra debug logging for panel styling that is missing the first time the panel opens (cosmetic, still being tracked). (#41)

## 1.1.0 (2026-10-13)

### Changed
- Node classes renamed to `FlareSimPlus` and `FlareSimPlus3D` (plugin and Python files renamed to match), so FlareSim+ installs next to an original FlareSim. (#39)

### Added
- Help > FlareSim+ > Convert FlareSim Nodes, and `FlareSimPlus_Convert.py` to convert old script files without Nuke. (#39)

## 1.0.0 (2026-10-11)

First FlareSim+ release, built on Peter Mercell's fork of FlareSim.

### Added
- Auto Detect is the default source mode, with Source Cap, Cluster Radius and a sources view. (#5)
- Lens Browser: its own window with a live, draggable flare preview, look presets, thumbnails of every lens, Delete Look, Import Lens, and remembered preview exposure. (#6, #8, #9, #12, #13, #14)
- Lens Browser button on the node. (#7)
- Lens Elements tab: lens side view, per-surface controls, Shift+click ghost picking, and ghost light paths that react to surface edits. (#10, #11)
- Matte input to occlude lights behind foreground objects. (#15)
- Simpler panel: Show Advanced checkbox, Quality menu (Low/Medium/High/Ultra/Custom), Clip To with overscan. (#19)
- Camera input: focal length and film back drive the flare; Lens Browser suggests lenses near the camera focal length. (#20, #27)
- FlareSim+ panel header with a live lens card. (#21 to #25)
- Linux rollout tools: lens path resolver, missing-lens error on the node, GPU check, Help > FlareSim+ > Self Test. (#26)
- Error log, debug logging and Save Debug Report under Help > FlareSim+. (#29)
- Haze (veiling glare), starburst, and Output Layers (flare, haze, starburst AOVs). (#33, #34)
- Lens Dirt input with Dirt Softness and Dirt Scatter, and a dirt layer. (#36, #37)
- Build guides for Windows, Linux and macOS. (#26, #38)

### Changed
- Manual XY: the placed point is the light, with Light Colour and optional Colour From Plate. (#17)
- Lens Browser Apply to Node keeps settings made on the node. (#18)
- One CMake build for every platform; release zips instead of committed binaries. (#1)

### Fixed
- Windows build of the CUDA kernels, and CUDA architectures kept across CMake re-runs. (#2, #3)
- Viewer going black after a render. (#4)
- Tint and Light Colour pickers did nothing; they now open on top and update live. (#16)
- Docked panel growing very tall after loading a lens. (#28 to #32)
- Panel styling crash; camera now found through Dot and NoOp nodes. (#27)
- Black flare layer when viewed alone. (#34)
- Empty Surfaces, About and Node tabs. (#35)
