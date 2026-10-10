# FlareSim for Nuke

A physically-based lens flare simulator for Foundry Nuke — GPU-optimised fork of [LocalStarlight/flaresim_nuke](https://github.com/LocalStarlight/flaresim_nuke).

![FlareSim](FlareSim.png)

I really appreciate Steven (LocalStarlight) for open-sourcing FlareSim. This is my version from the eyes of a compositor — built to be fast and flexible enough to handle everything a client might ask for.

The original FlareSim is a Windows/Nuke 16 plugin built on CUDA 13. This fork adds **Linux** and **macOS** support, works with **Nuke 14–17**, and brings major GPU performance improvements — async CUDA streams, FP16 output, prefix-sum blur kernels, and a per-surface art-direction UI. The core optics (ray tracing, Fresnel, lens files) are unchanged; all the work is in the GPU pipeline, the Nuke integration, and the build system.

---

## What's New

### FlareSim3D — Camera + Axis driven flares

A new node that takes a **Camera** and an **Axis** (light position) as inputs instead of a manual Source XY. The Axis world position is projected through the Camera to derive screen position and source distance automatically — no manual XY tracking needed. Connect a Camera and an Axis, and the flare tracks the 3D source through the shot.

- **Intensity Falloff** — inverse-square-law scaling based on source distance
- **Reference Distance** — distance (scene units) at which the flare has its nominal intensity
- Source behind camera → no flare (physically correct)
- Source off-screen → keeps flaring with its Light Colour

Registered as `Filter/FlareSim3D`. Builds as a separate `.so` / `.dylib`.

### Placed Light (Manual XY and FlareSim3D)

In **Manual XY** the flare comes from a light you place at **Source XY**; in FlareSim3D the light is the Axis. It always flares, whatever the plate looks like there, and keeps flaring when it leaves the frame.

- **Light Colour** — the light's colour (white by default); **Source Intensity** sets its brightness
- **Colour From Plate** — tint the light with the plate's colour at its position (averaged over **Sample Radius**), so the flare picks up the colour and brightness of the light it sits on. Off by default.
- **Edge Blend (px)** — with Colour From Plate, the zone at the frame edge where the plate colour fades to the plain Light Colour as the light leaves the frame
- **Threshold** only applies to Auto Detect. The old Outside Source knobs are hidden; scripts that set them still load.

### Occlusion Matte

A light that goes behind a foreground object stops flaring. Connect a roto, or the object's alpha, to the **matte** input (input 1 on FlareSim, input 3 on FlareSim3D). The node measures how much of a small disc around the light the matte covers and dims the flare by that much, so a light sliding behind an edge fades out instead of popping off.

- **Matte Mode** — **Occlude** (default): white in the matte hides the light. **Mask**: white lets the light through, so only lights inside the white area flare (what the input was meant for in the original FlareSim).
- **Light Size** — diameter in pixels of the light as the matte sees it. Bigger gives a slower fade across an edge. Default 8.
- Manual XY and FlareSim3D measure at the light's position (FlareSim3D uses where the Axis projects through the Camera). Auto Detect dims each detected light at its own spot, before **Max Sources**, so hidden lights don't use up places.
- Lights outside the frame are not affected.
- The flare fades as a whole; a real half-hidden light would also change the flare's shape, which this does not simulate.

### Spectral Jitter

Randomises each ray's wavelength within its spectral bin, smoothing the hard colour boundaries between discrete samples at zero extra ray-trace cost — same number of traces, each one uses a slightly different wavelength.

- **Spectral Jitter** — on/off (on by default)
- **Spectral Jitter Seed** — fixed seed for VFX reproducibility
- **Auto Seed** — derives seed from frame number for temporal variation
- **Jitter Scale** — multiplier on the randomisation range (0.5 = subtle, 1.0 = default, 2.0 = aggressive)

### Extended Spectral Samples

The spectral samples dropdown now goes up to 31: **3 (R/G/B)** · 5 · 7 · 9 · 11 · 15 · 21 · 31. With spectral jitter on, 11 samples already looks excellent. 31 gives ~10 nm bin spacing — essentially continuous spectral coverage.

### Highlight Compression

Luminance-preserving soft-clip applied on the GPU after blur. Prevents hard-clipped highlights and super-white values while maintaining colour hue through the rolloff.

- **Highlight Compression** — on/off toggle
- **Metric** — Value (max RGB), Luminance (Rec.709), or Lightness (cube root)
- **Clip** — maximum output value (asymptotic ceiling, default 2.0)
- **Knee** — transition sharpness (0 = very soft, 1 = hard clip, default 0.5)

Matches AFXToneMap convention — same Clip and Knee values produce almost the same rolloff behaviour.

### Per-Surface Art Direction

Each lens surface now has individual controls in the Surfaces tab: **gain**, **color** (RGB tint), **offset** (pixel shift x/y), and **scale** (pull toward / push away from centre). Both surfaces in a ghost pair combine together. The Lens Browser's **Lens Elements** tab edits the same controls with a lens diagram, a live preview and ghost picking.

### Profiler

Built-in per-frame timing table. Disabled by default — flip `#define FLARESIM_PROFILE 1` in the source to enable.

---

## Performance: 205× faster

Same lens (Angenieux 180mm, 15 surfaces, 66 active pairs), same frame, same machine (RTX A5000). Buffer: 10172×5370, ghost blur radius 35 px, 2 passes.

| Stage | Original | Fork | Speedup |
|-------|----------|------|---------|
| Source detection | 3,238 ms | 8 ms | **425×** |
| Ghost filter | 8 ms | 8 ms | — |
| CUDA ghost kernel | 6,398 ms | 31 ms | **206×** |
| Ghost blur + readback | 9,359 ms | 56 ms | **167×** |
| **TOTAL** | **21,091 ms** | **103 ms** | **205×** |

21 seconds → 0.1 seconds. Interactive.

---

## Platforms

| Platform | GPU Backend | Nuke | Status |
|---|---|---|---|
| **Linux** | CUDA (sm_70+) | 14–17 | Supported — local or Docker (ASWF) builds |
| **Windows** | CUDA (sm_70+) | 14–17 | Supported — Ninja + MSVC (VS 2019 toolset), see [docs/BUILD_WINDOWS.md](docs/BUILD_WINDOWS.md) |
| **macOS** | Metal (Apple Silicon) | 15–17 | Supported |

Pre-built plugins are published as zips on the GitHub Releases page, one per Nuke version and OS. They are no longer committed to the repository.

---

## Repository Layout

| Path | Contents |
|---|---|
| `CMakeLists.txt` | One build for every platform — picks CUDA or Metal automatically |
| `src/` | Nuke nodes, optics core, CUDA kernels and the Lens Browser preview library |
| `src/metal/` | macOS nodes and Metal shaders |
| `nuke/` | `menu.py`, the Lens Browser window and the looks module |
| `lenses/` | 1,370+ real lens prescriptions and the converter scripts |
| `looks/` | Starter looks (lens + flare settings) shown in the Lens Browser |
| `scripts/` | Multi-version build and release packaging scripts |
| `docker/` | ASWF + CUDA build image used by `scripts/build_docker.sh` |
| `examples/` | Example Nuke script |

---

## Building from Source

All platforms use the same root `CMakeLists.txt`:

```bash
cmake -S . -B build -DNUKE_VERSION=15.1v10
cmake --build build --config Release -j
cmake --install build --prefix ~/.nuke/plugins/FlareSim   # plugins + preview library + menu.py + Lens Browser + looks + lenses
```

`NUKE_VERSION` is used to find the default install location (`/usr/local/Nuke<ver>`, `C:/Program Files/Nuke<ver>`, or `/Applications/Nuke<ver>/Nuke<ver>.app/Contents/MacOS`). Point at another install with `-DNDK_ROOT=<nuke>/include -DNUKE_LIB_DIR=<nuke>` (or `-DNUKE_ROOT=` on macOS).

The build handles these automatically:

- **CUDA architectures** are chosen from the installed nvcc (12.8+ adds Blackwell sm_100/sm_120). Override with `-DCMAKE_CUDA_ARCHITECTURES="86;89"`.
- **libstdc++ ABI**: Nuke 14 on Linux needs `_GLIBCXX_USE_CXX11_ABI=0` (per Foundry's NDK guide); it is set from `NUKE_VERSION`.
- **CUDA runtime** is linked statically, so users need only an NVIDIA driver ≥ 525.

### Building every Nuke version at once

| Platform | Command | Output |
|---|---|---|
| Linux (local toolchain) | `scripts/build_linux.sh` | `dist/nuke<N>/` |
| Linux (ASWF Docker, reproducible) | `scripts/build_docker.sh --images` once, then `scripts/build_docker.sh` | `dist/nuke<N>/` |
| Windows (VS 2019 x64 dev prompt) | `.\scripts\build_windows.ps1` | `dist\nuke<N>\` |

Then `scripts/package_release.sh --version 1.0.0` (or `.\scripts\package_release.ps1 -Version 1.0.0`) zips each version into `release_packages/`, ready for a GitHub Release.

### Smoke test (no Nuke or GPU needed)

```bash
cmake -S . -B build-tests -DFLARESIM_BUILD_PLUGINS=OFF
cmake --build build-tests && ctest --test-dir build-tests
```

CI runs this test, compiles the CUDA kernels and checks the Python files on every pull request.

---

## Installation

1. Unzip a release (or run `cmake --install`) so you have `~/.nuke/plugins/FlareSim/` containing `FlareSim`, `FlareSim3D`, `flaresim_preview`, `menu.py`, `FlareSim_LensBrowser.py`, `FlareSim_Looks.py`, `looks/` and `lenses/`.
2. Add this to `~/.nuke/init.py`:
   ```python
   nuke.pluginAddPath('./plugins/FlareSim')
   ```
3. Restart Nuke. The nodes appear under **Filter**. The **Lens Browser** button on each node, and **Window → FlareSim Lens Browser**, open the Lens Browser window on the bundled lens library.

---

## Quick Start

**FlareSim** (2D source):
1. Connect your plate to the input.
2. Click **Lens Browser** on the node to pick a lens with a live preview. The button shows the node's current lens.
3. Set **FOV H** to match your camera.
4. Pick the flare sources. **Source Mode** defaults to **Auto Detect**, where every bright light in the plate becomes a flare source:
   - Set **View** to **Sources Only** to see which lights are picked up, without rendering the flare.
   - Adjust **Threshold** until only the lights you want are marked.
   - Raise **Cluster Radius** so each large light (a headlight, the sun) counts as one source instead of many.
   - Use **Source Cap** to stop one very hot light from overpowering the rest, and **Max Sources** to cap how many are traced.
   - Set **View** back to **Flare** for renders.

   To place a light yourself instead, switch **Source Mode** to **Manual XY**, put **Source XY** where the light is (animate it or link it to a Tracker), and set **Light Colour** and **Source Intensity**.
5. Adjust **Flare Gain** to taste.

**FlareSim3D** (3D source):
1. Connect your plate to input 0, Camera to input 1, Axis (at the light position) to input 2.
2. Click **Lens Browser** to pick a lens.
3. The flare tracks the Axis through the Camera automatically.
4. Enable **Intensity Falloff** and set **Reference Distance** for distance-based dimming.
5. Set **Light Colour** and **Source Intensity**; the flare keeps going when the Axis leaves the frame.
6. If the light passes behind something, connect a matte of it to the **matte** input (see Occlusion Matte).

### Lens Browser

The **Lens Browser** button on a FlareSim or FlareSim3D node (or **Window → FlareSim Lens Browser**) opens a window for picking a lens and building a look before you render:

- **Lens thumbnails**: every lens is shown as a small render of its flare, with the same light and settings, so you can compare them at a glance. The thumbnails sit under the preview: drag the divider between them to go from a single scrolling row (tiles grow to fill it) to a grid with more rows (the **Tile size** slider sets their size). Narrow them with the search box and the type (cine, stills, anamorphic), maker, focal length and speed filters on the left. The window remembers its size and dividers. Click a lens to preview it, double-click to apply it to the node, Page Up / Page Down to step through. Thumbnails are rendered in the background the first time (lenses on screen first) and cached in `~/.nuke/FlareSim/thumbnails`.
- **Preview**: a live render of the selected lens. Drag in the preview to move the light and watch the ghosts follow; the mouse wheel changes exposure. It draws a quick draft while you drag, then refines. **Background...** puts a still of your plate behind it. Exposure, the preview light and the quality are remembered between sessions; **Reset** next to Exposure and **Reset Preview Light** put them back to their defaults.
- **Look** tab:
  - **Flare Look**: Gain, aperture Blades and Rotation, Ghost Blur. These are the node's knobs.
  - **Preview Light and Camera**: light intensity and colour, FOV and preview quality. These only shape the preview; the node keeps its own source and camera settings.
- **Lens Elements** tab, for shaping individual ghosts:
  - A side view of the lens: every surface's curve, the glass between them and the iris. Click a surface (or step with the arrows) to select it; its ghosts are highlighted in the preview. Click it again, click empty space or press Esc to deselect.
  - The side view traces light through the lens from the preview light's angle: a faint image-forming ray, and the ghost paths of the selected surface (or the picked ghost) bouncing between their two surfaces. The paths follow the surface settings so you can see what each change does: the tint colours them, gain sets their strength, a surface turned off drops them (dashed red), and offset and scale move where they land on the sensor (dotted arrows).
  - **Surface** controls for the selected surface: **Makes ghosts** (off drops every ghost off it), **Gain**, **Tint**, **Offset X/Y** (in the node's pixels) and **Scale**. These are the node's Surfaces tab knobs, so the preview shows what the node will render. A ghost bounces off two surfaces, so both surfaces' settings combine (gains and tints multiply, offsets add, scales multiply). Changed surfaces are drawn in cyan, turned-off ones dashed red.
  - **Pick a Ghost**: Shift+click (or right-click) a ghost in the preview. The list shows which surfaces make the light there, brightest first; pick one to highlight that ghost and its two surfaces, then **Edit** either surface. **Highlight in the preview** dims the other ghosts (preview only).
- **Start From a Look**: load a saved look's lens and settings as a starting point, or **Delete Look** to remove one of your own looks.
- **Apply to Node** sets the lens and look on the node the window was opened from (or the selected FlareSim node, or a new one). Ctrl+Z undoes it. Only the settings you changed in the window are written, so Ray Grid, Spectral, Highlight, gain or blur you set on the node are kept; a loaded look sets all of its settings. With the same lens, only the surfaces you changed in the window are written, so tweaks made on the node's Surfaces tab are kept; a new lens or a look sets every surface. **Reload From Node** reads the node's lens, look and surface settings. **Save as Look...** keeps the settings, including changed surfaces, as a look.
- **Import Lens**: copy `.lens` files, or a whole folder of them, into your own lens library (`~/.nuke/FlareSim/lenses`). A folder keeps its name and sub-folders. Imported lenses get thumbnails like the rest; the **Library** filter shows Bundled, Mine (imported) or Studio lenses. A file already in your library is skipped, and a different file with the same name is saved with a number. Lenses in your home folder only exist on your machine: for a team or a render farm, put them in a shared folder listed in `FLARESIM_LENS_PATH` (shown as Studio). **Open .lens File...** previews a file from anywhere without importing it.
- **Lens File knob**: the node's Lens File path is hidden from the panel, since the Lens Browser sets it. It is still saved in the script and can be set from Python, e.g. `node['lens_file'].setValue(path)`.

The preview runs on the CPU from the `flaresim_preview` library installed next to the plugins, so it works without a GPU and doesn't compete with the node. Without that library the window still works, minus the preview.

### Looks

A look is a lens plus the settings that shape its flare: Flare Gain, aperture, spectral, highlight, ghost blur and any per-surface overrides. Source position, threshold and camera are not part of a look, so it works on any shot.

In the **Lens Browser**:
- Pick a look under **Start From a Look**, click **Load Look**, adjust it, then click **Apply to Node**. Ctrl+Z undoes it.
- Click **Save as Look...** to keep the window's lens and settings. Your looks go in `~/.nuke/FlareSim/looks/`. **Reload From Node** first if you want to save a node's tuned settings, including its highlight and spectral knobs.
- Pick one of your own looks and click **Delete Look** to remove it (it asks first). Studio and starter looks can't be deleted from the window; if one of your looks had the same name as one of them, that look shows again after you delete yours.
- Set `FLARESIM_LOOKS_PATH` to one or more shared folders to give a whole team the same looks.

FlareSim ships a few starter looks in `looks/` as starting points.

---

## Anamorphic Support

Anamorphic lenses produce ghosts and flares with a different shape than spherical lenses — horizontal streaks, oval bokeh, asymmetric, rainbow chromatic aberrations. These signatures arise from cylindrical and toric refractive surfaces inside the actual lens, not from a post-render warp. FlareSim now traces rays through real anamorphic geometry on both CPU and GPU.

### What's supported

- **Cylindrical surfaces** — `cyl_x` (axis along X) and `cyl_y` (axis along Y). Used by every common anamorphic afocal attachment design (CinemaScope, Panavision C-series, Cooke Anamorphic/i, Hawk, Iscorama-style adapters)
- **Toric surfaces** — two-radii curvature in orthogonal axes. Solved on GPU via Newton-Raphson on the implicit quartic. Used by some modern anamorphic designs that prefer single-element correctors over Galilean afocal blocks
- **Mixed geometry** — a single `.lens` file can freely combine spherical, cylindrical, and toric surfaces in any order
- **All ghost reflections respect surface curvature type** — a `cyl_y` reflection produces a horizontal streak; a toric reflection produces an asymmetric defocus pattern; both happen automatically based on what the `.lens` file declares
- **FlareSim and FlareSim3D both anamorphic-ready** — they share the same lens / trace / ghost code paths, no per-node anamorphic plumbing

### What you'll see in render

- Horizontal anamorphic streak from `cyl_y` reflections — the iconic streak
- Oval bokeh that comes naturally from a squeezed entrance pupil
- Rainbow color separation when you combine low-Abbe-number glass with uncoated surfaces (the Cooke SF look)
- Different ghost geometry vs the same lens with all spherical surfaces — visibly different on side-by-side renders

### Test lenses included

| File | Purpose |
| --- | --- |
| `lenses/Anamorphic_Test_50mm_2x.lens` | 2× squeeze, all-`cyl_y` afocal block — exercises the cylinder path |
| `lenses/Anamorphic_Test_50mm_2x_toric.lens` | Same lens with one cyl_y promoted to `toric` — exercises the toric Newton solver |

---

## Authoring Anamorphic .lens Files

If you want to build your own anamorphic prescription, here's everything you need to know.

### File format

A line in the `surfaces:` section now accepts up to 8 tokens. Tokens 1–6 are unchanged from the original FlareSim, so every existing `.lens` file still loads bit-identically:

```
radius   thickness   ior    abbe   semi_ap   coating   [type]   [radius_y]
```

Tokens 7 and 8 are optional. Missing → spherical (the legacy default).

| Type token | Meaning | What `radius` means | Needs `radius_y`? |
| --- | --- | --- | --- |
| `sph` (or omitted) | Spherical | R | no |
| `cyl_x` | Cylinder, axis along X — curves in YZ | Ry (vertical-axis curvature) | no |
| `cyl_y` | Cylinder, axis along Y — curves in XZ | Rx (horizontal-axis curvature) | no |
| `toric` | Two orthogonal radii | Rx | yes — supply Ry as the 8th token |

For a horizontal anamorphic squeeze (the standard cinematic look), you almost always want `cyl_y` — the cylinder axis is vertical, so the surface refracts only in the horizontal axis. Vertical rays pass straight through, horizontal rays get squeezed.

### Example — minimal cyl_y front element

```
# Anamorphic 50mm 2x test
name: Test 50mm 2x
focal_length: 50.0

surfaces:
# radius   thickness   ior      abbe   semi_ap   coating   type
  -50.0    8.00        1.5168   64.2   45.0      1         cyl_y    # plano-concave neg cyl
  0        92.00       1.0      0.0    45.0      0                  # flat back, 92 mm air gap
  0        10.00       1.5168   64.2   40.0      1                  # flat front of pos cyl
  -100.0   15.00       1.0      0.0    40.0      0         cyl_y    # plano-convex pos cyl
  ... rest of taking lens (spherical) ...
```

That's a Galilean afocal cylindrical attachment with a 2× horizontal squeeze: |F2|/|F1| = 194/97 = 2.0.

### Designing a squeeze ratio

For an afocal attachment with squeeze ratio S, you need a negative-then-positive cylindrical pair:

- Pick F1 (negative cyl, plano-concave) and F2 (positive cyl, plano-convex) such that |F2|/|F1| = S
- Air gap between them = F1 + F2 (F1 is negative, so gap = F2 − |F1|)
- For a thin plano-cylindrical with refractive index n: F = (n − 1)·R for plano-concave with R<0, or F = −(n − 1)·R for plano-convex with back R<0

Common ratios in the wild:

| Squeeze | Examples |
| --- | --- |
| 1.33× | Iscorama-style adapters, Atlas Mercury, vintage scope add-ons |
| 1.8× | Cooke Anamorphic/i FF, Vantage Hawk Class-X — full-frame friendly |
| 2× | Cooke S35, Panavision, traditional CinemaScope/Hollywood scope |

### Geometric validity gotcha

**The semi-aperture must be smaller than `|R|`** for any spherical or cylindrical curved surface. A surface with R = −30 cannot have semi_aperture = 40 — the sphere physically doesn't extend that far in the radial direction. The trace will silently miss those rays and you'll see almost no ghosts.

Rule of thumb: keep `semi_aperture < |R|` for curved surfaces. Use `semi_aperture < 0.7·|R|` if you want margin for off-axis behavior. (This applies to all curved types — spherical, cylindrical, and toric. Toric must satisfy this for both Rx and Ry.)

### Coating and Abbe number choices for the look you want

Coating layer count maps to ghost brightness via Fresnel reflectance:

| Goal | Coating value | Per-surface reflectance (approx) |
| --- | --- | --- |
| Modern multi-coated lens (clean, dim ghosts) | `2` or higher | <0.5% |
| Vintage single-coated lens (visible flares) | `1` (MgF2 quarter-wave) | ~1% |
| Cooke "Special Flair" / heavy flare style | `0` (uncoated) | ~4% |

Abbe number controls dispersion (rainbow color spread). Lower Abbe → more spectral separation in ghosts:

- Crown glass: `n=1.5168, abbe=64.2` (BK7) — minimal chromatic aberration in ghosts
- Heavy flint: `n=1.6068, abbe=37.0` (SF6) — strong color spread, the basis of the "rainbow flare" look

---

## What Changed

### Anamorphic support (new since the previous version of this fork)

* **Cylindrical surfaces** — CPU intersection in `trace.cpp::intersect_cylinder_x/y`, GPU mirror in `ghost_cuda.cu::d_intersect_cylinder_x/y`
* **Toric surfaces** — Newton-Raphson on the implicit quartic, CPU + GPU. `MAX_ITER` tuned to 16 (down from a conservative 30) based on instrumented convergence data on adversarial test sets
* **Lens parser extensions** — optional 7th and 8th tokens for surface type and Ry. Legacy spherical files load byte-identically

---


## Future Ideas

1. **Starburst as a separate node** — a standalone diffraction spike generator with more controls, decoupled from the ghost renderer.

---

## Credits

FlareSim is built on the foundational work of **Steve Watts Kennedy** ([LocalStarlight](https://github.com/LocalStarlight/flaresim_nuke)), whose original CUDA-based lens flare renderer established the core ray-tracing approach, optical physics, and lens file format.

The original physics engine is based on the work of **Eamonn Nugent** ([@space55](https://github.com/space55) · [55.dev](https://55.dev/)), whose CPU-based renderer ([blackhole-rt](https://github.com/space55/blackhole-rt/)) provided the ray-tracing foundation.

Tutorial by Steve: [https://youtu.be/yEsBOQNG16Y](https://youtu.be/yEsBOQNG16Y)

---

## License

MIT — see [LICENSE](LICENSE).

Peter Mercell — [petermercell.com](https://petermercell.com)
