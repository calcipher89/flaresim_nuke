# Changelog

Major updates and bug fixes for FlareSim+, newest first. Version numbers match the release zips made with `scripts/package_release.sh --version <x.y.z>`, and each release has a git tag (`v1.0.0`, `v1.1.0`, ...) on the commit it was built from. Pull request numbers link to the full details.

## Unreleased

### Added
- Ghost Overrides: change one ghost on its own (gain, tint, scale, offset, or turn it off) on top of its two surfaces' settings, so matching one ghost no longer moves every other ghost off the same surfaces. Pick the ghost in the Lens Browser's Lens Elements tab, or type it on the node's Surfaces tab (`3-12 gain=2.5 color=1,0.6,0.9`). Saved in looks; older versions ignore it.
- Haze Shape and Haze Tint: haze that is bright near the light with a long faint tail (same total light), and a colour for the veil. Starburst Tint, Fringe (colour fringing, 0 to 2) and Softness. All under Show Advanced and saved in looks; a look saved before them puts them back to their defaults, and older versions ignore them.
- Finer controls for matching anamorphic references (all saved in looks; older versions ignore them):
  - Ghost Overrides `streak=`: a thin horizontal line through one ghost, carrying a share of its light, as an anamorphic lens draws through small focused ghosts. `streak_color=` tints it.
  - Ghost Overrides `blur=`: one ghost's own Ghost Blur, for soft rims; and `scale=x,y` to scale a ghost across and up separately.
  - Haze Aspect (oval haze), Starburst Reach (long thin spikes, small core).
  - Rays (random rays fanning out from the light) and Ring (a soft rainbow ring or arc) under Starburst.
  - The Lens Browser's Pick a Ghost panel has Scale Up, Blur, Streak and Streak Length.
- Resolution Independent (off by default): the flare looks the same at any plate size, as it does at Reference Width (default 1920). Fixes placed lights' ghosts dimming at 4K and Auto Detect haze, starburst and dirt brightening at 4K. Offsets are then in Reference Width pixels. Saved in looks.
- Six Hawk-style 2x anamorphic lenses with matching looks, tuned against Hawk test footage (no Vantage prescription is public): Hawk V-Lite style 35/55/110mm (long blue streak, small blue mirrored ghost) and Hawk APL style 30/50/100mm (pale streak, oval ghosts, starburst on the 50). Streak length, mirrored ghost, veil and colours measured from light-on minus light-off frames; irises set to the footage's stops. `lenses/anamorphic/Hawk_*.lens`, `looks/Hawk_*.json`. (#49)
- `docs/FACILITY_INSTALL.md`: installing FlareSim+ for a whole facility, including split layouts with Python and plugin files in different folders. (#48)

### Fixed
- Anamorphic lenses: the flare landed at half the distance from the frame centre and ghosts stayed round, because the flare was laid out squeezed on a desqueezed plate. The squeeze is now worked out from the lens (or a `squeeze:` line in the .lens file) and the flare is laid out desqueezed, so it sits on the light and ghosts come out as wide ovals. Spherical lenses are unchanged. Not yet on the macOS Metal nodes. (#49)
- Lens Browser preview was blank in split facility installs: it now finds the preview library, lenses and looks next to the loaded plugin too, or from `FLARESIM_PREVIEW_LIB`. (#48)

## 1.5.0 (2026-10-14)

### Changed
- A render Nuke cancels (a knob changed mid-render) now stops within a GPU batch or two instead of finishing the frame, so changing knobs at High no longer stalls the UI for seconds. (#47)

### Added
- `FLARESIM_TRACE_LOG`: set it to a file path and both nodes log each update, compute (and whether Nuke cancelled it) and first row served, for tracking down Viewer refresh problems. (#47)

### Fixed
- The Viewer could stay black for a frame after a parameter change until the frame changed: a compute Nuke cancelled part way was kept as the finished result. (#47)

## 1.4.0 (2026-10-14)

### Added
- Viewer Quality: a lower Quality for the Viewer only; renders always use full Quality. (#46)

## 1.3.0 (2026-10-13)

### Added
- Ghost Render: **Smooth** draws each ghost as a filled mesh, so large ghosts come out clean instead of grainy. New nodes start on Smooth; old scripts keep Points. (#43)
- This changelog. (#42)

### Changed
- Smooth renders much faster, about 7x with many Auto Detect lights. (#44, #45)

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
- Build guides for a native Linux build (no Docker) and for macOS. (#38)

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
- Build guides for Windows and Linux. (#26)

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
