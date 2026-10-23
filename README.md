# FlareSim+ for Nuke

An artist-focused lens flare tool for Foundry Nuke, built on [LocalStarlight/flaresim_nuke](https://github.com/LocalStarlight/flaresim_nuke) and [petermercell/flaresim_nuke](https://github.com/petermercell/flaresim_nuke).

Many thanks to Steven (LocalStarlight) for open-sourcing FlareSim, and Peter Mercell for his fork. Steven built the real optics: rays traced through actual lens prescriptions, Fresnel coatings, haze and starburst. Peter made it fast, with a much quicker GPU pipeline, Linux and macOS builds, anamorphic lenses and a 3D node.

The first time I tried FlareSim, I looked at it from an artist's point of view. I've always liked how Video Copilot's Optical Flares lets you browse flare presets. That OG tool had a great preset browser where you could shape the overall look of each flare element.

So FlareSim+ keeps the physics and the speed, and puts an artist-friendly layer on top. It has a Lens Browser with a live preview and looks you can save, a simple panel with a Quality menu, bright lights in the plate as the flare source, a camera input, matte occlusion, and haze, starburst, lens dirt and AOV layers for the comp. It's built to drop into a studio pipeline and run across the farm, and just work. :)

---

## What's New

Full version history: [CHANGELOG.md](CHANGELOG.md).

### FlareSim+ Panel Header
<img width="519" height="1029" alt="image" src="https://github.com/user-attachments/assets/e564b428-d2d1-4ec2-8d62-34914afe8418" />

The FlareSim tab opens with a FlareSim+ header and a lens card showing a thumbnail of the look, the lens name, the look name and the focal length. It updates on its own when you change the lens, the look or the camera.

That styling works on Nuke's own panel widgets, only while a FlareSim panel is open. If it ever causes trouble, turn it off with `os.environ['FLARESIM_PANEL_STYLE'] = '0'` in `init.py` (or `FlareSimPlus_Header.PANEL_STYLE = False` in `menu.py`): you get Nuke's plain panel, and the header still shows at a fixed size and updates the next time the panel opens.

The node classes are `FlareSimPlus` and `FlareSimPlus3D` (plugins `FlareSimPlus.so` / `.dll` / `.dylib`, Python modules `FlareSimPlus_*.py`), so FlareSim+ installs next to an original FlareSim without either one taking over the other's nodes.

**Old scripts.** Original `FlareSim` / `FlareSim3D` nodes stay as they are until you convert them. The knobs carry over, so a converted node starts with the same settings:

- In Nuke, **Help → FlareSim+ → Convert FlareSim Nodes** converts the selected FlareSim nodes, or all of them when none is selected (the original plugin must be installed so the old nodes open). Ctrl+Z undoes it.
- On a script file, without either plugin: `python FlareSimPlus_Convert.py shot.nk` writes `shot_flaresimplus.nk`; `--in-place` overwrites the script and keeps a `.bak`.

Looks saved before the rename apply as before.

### Simpler Node Panel

The node opens with just the main controls: Lens Browser, Source, Matte, **Quality**, **Viewer Quality**, **Ghost Render**, **Flare Gain**, **Ghost Blur** and **Output**. Tick **Show Advanced** to see everything else (Source Extraction, Pupil Jitter, Camera, Distance, Aperture, Spectral, Highlight, Blur Passes, Sample Radius, Edge Blend, Source Cap). Hidden settings still apply, and looks and the Lens Browser still set them.

- **Quality** — **Low**, **Medium** (default), **High**, **Ultra** or **Custom**. The presets follow the image width, about 128, 256, 512 and 1024 rays across on a 1920 plate, so a look renders the same at any resolution. Each step up takes about four times as long. **Custom** shows **Ray Grid** to set it yourself. Scripts saved with their own Ray Grid open as Custom and keep it; scripts on the old default (64) now render at Medium. Looks no longer set the ray grid.
- **Viewer Quality** — a lower Quality (Low, Medium or High) used only in Nuke's Viewer, for faster interaction and playback. Every render uses **Quality**: farm and command-line renders ignore Viewer Quality, and a Write rendered from the open Nuke switches the nodes to full Quality while it runs. It is never higher than Quality, and the node shows `(viewer: Low)` or similar in the Node Graph while it is on. Default: Same as Render.
- **Ghost Render** — **Smooth** (new nodes) or **Points**. Smooth joins each ghost's rays into a mesh and fills it, so large soft ghosts come out clean instead of grainy, even at Medium; Quality then sets how finely edges and fine detail follow the optics. Points draws every ray as a dot, as before, and needs a very high ray grid to look smooth on big ghosts. Smooth ignores Pupil Jitter and Spectral Jitter (they would make ghosts flicker from frame to frame); raise **Spectral Samples** for smoother colour fringes. Scripts saved before Ghost Render existed open on Points and render as they did. The Lens Browser preview has always drawn ghosts the Smooth way.

### Camera Input (FlareSim)

2D FlareSim has an optional **cam** input. Connect the shot camera and the field of view comes from its focal length and horizontal aperture (the vertical follows the format's aspect, as Nuke's own renders do), including any animation, instead of the FOV knobs. Dot and NoOp nodes between the camera and FlareSim are fine. Under **Show Advanced → Camera**, the line next to **Use Camera** shows what the camera gives (focal length, aperture, FOV), and the FOV and sensor knobs are greyed out while it drives them. Turn **Use Camera** off to use the knobs with a camera still connected. With nothing connected the FOV knobs work as before.

The Lens Browser reads the same camera (input 1 on FlareSim3D): **Focal** gets a **Near camera** entry that lists lenses within 15% of the camera's focal length, or the closest few if none are that close. It is picked for you when the node has no lens yet or its lens is already one of them; otherwise it stays one click away. Without a camera, **Use Sensor Size** with a Focal Length on the node works too.

### Overscan (Clip To)

For an undistort → FlareSim → redistort workflow. **Clip To** in the Output section sets the area the flare is rendered into:

- **BBox** (default) — the input's bbox, as before. An undistorted plate's bbox already includes its overscan, and Auto Detect finds lights out there too.
- **Format** — crop the flare to the format.
- **Format + Overscan** — the format grown by **Overscan** (width, height) pixels on each side, so ghosts that fall outside the frame are kept for redistorting.

FOV still matches the format, so ghosts land in the same place whichever you pick. Ghost Blur is sized from the format in Format and Format + Overscan, so changing the overscan doesn't change the blur. The matte now works across its whole bbox, so a matte that covers the overscan also hides lights out there.

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
- Lights outside the frame are not affected, unless the matte's bbox reaches them (the overscan of an undistorted plate).
- The flare fades as a whole; a real half-hidden light would also change the flare's shape, which this does not simulate.

### Haze, Starburst, Lens Dirt and Output Layers

Two light effects from the original FlareSim are back, on both FlareSim and FlareSim3D. Each has its own checkbox, off by default, so existing scripts render as before.

- **Haze** — veiling glare: a wide soft glow around each light from light scattered inside the lens. It carries the light's energy, so brighter and bigger lights give more haze and a wider haze is fainter. **Haze Gain** defaults to 0.1; try 0.05 to 0.2. **Haze Radius** (fraction of the diagonal, default 0.15) and **Haze Passes** are under Show Advanced.
- **Starburst** — diffraction spikes from the iris at the brightest lights (up to 32). **Starburst Gain** uses the original FlareSim's scale: typical 50 to 500, default 100. The shape comes from **Aperture Blades** and **Aperture Rotation**: 6 blades give 6 spikes, an odd count twice as many, 0 a round iris with soft rings. Red spreads wider than blue. **Starburst Size** is under Show Advanced.
- Both follow the occlusion matte and Source Intensity, since they are built from the same lights as the ghosts, and their sizes use the same diagonal as Ghost Blur.
- **Output Layers** (Output section, off by default) — also outputs the parts on their own: `flare.rgb` (ghosts), plus `haze.rgb` while Haze is on and `starburst.rgb` while Starburst is on, and `dirt.rgb` while Lens Dirt is on, for grading them separately. RGBA is always the whole flare.
- **Lens Dirt** — dust and smudges on the lens, lit by the lights. Connect an image to the **dirt** input (input 3 on FlareSim, input 4 on FlareSim3D): a dirt texture or scan, or Nuke's Noise or Grain for a procedural look, reformatted to the plate's format. Its RGB is how much each pixel catches the light. The node spreads each light's glow wide across the frame (**Dirt Spread**, default 0.4 of the diagonal) and multiplies the dirt by it, so dirt near a light glows and dirt far from any light stays dark. **Dirt Gain** (default 1) sets how bright; **Ghosts Light Dirt** lets the ghosts light it too. Dirt on the front element is far out of focus, so **Dirt Softness** (default 0.01 of the diagonal) defocuses it into soft blobs the shape of the iris (Aperture Blades), and **Dirt Scatter** (default 0.5) spreads the light it catches into a soft veil, so dirty areas near a light go hazy and lose contrast once merged over the plate. The plate itself getting dimmer or softer behind the dirt is left to the comp (the node outputs only the flare): use the same dirt image as a mask. With Output Layers on it also comes out in `dirt.rgb`.
- Looks save the haze, starburst and dirt settings (not whether Lens Dirt is on, since that needs a dirt image).
- Not yet in the Lens Browser preview, and the macOS (Metal) build doesn't have them yet.

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
| **macOS** | Metal (Apple Silicon) | 15–17 | Behind: missing the newest features (see below), not yet tested with FlareSim+. See [docs/BUILD_MACOS.md](docs/BUILD_MACOS.md) |

**macOS is behind Linux and Windows.** The Mac nodes don't have haze, starburst, lens dirt, output layers, or Auto Detect's Source Cap and Cluster Radius yet, and the Mac build hasn't been tested since the FlareSim+ changes. The Lens Browser, looks, the simple panel, the camera input, mattes and the panel header are there. Looks that use the missing features still load; the Mac just doesn't draw those parts. A Mac port is planned but not scheduled.

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
- **Linux build for a studio**: see [docs/BUILD_LINUX.md](docs/BUILD_LINUX.md) (from Windows with WSL2, or on a Linux machine).
- **macOS build**: see [docs/BUILD_MACOS.md](docs/BUILD_MACOS.md).

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

1. Unzip a release (or run `cmake --install`) so you have `~/.nuke/plugins/FlareSim/` containing `FlareSimPlus`, `FlareSimPlus3D`, `flaresim_preview`, `menu.py`, the `FlareSimPlus_*.py` files, `icons/`, `looks/` and `lenses/`.
2. Add this to `~/.nuke/init.py`:
   ```python
   nuke.pluginAddPath('./plugins/FlareSim')
   ```
3. Restart Nuke. The nodes appear under **Filter** as **FlareSim+** and **FlareSim+ 3D**. The **Lens Browser** button on each node opens the Lens Browser window on the bundled lens library.

### Studio and farm deployment

- **One shared install.** Put the `FlareSim` folder on a read-only share and add it with `nuke.pluginAddPath()` in the facility `init.py` (not only in a GUI `menu.py`), so terminal and farm renders find the nodes. Each Nuke major.minor version (15.0, 15.1, 16.0, ...) needs its own build.
- **GPU.** Every machine that renders FlareSim+ needs an NVIDIA GPU (sm_70 or newer: Volta, Turing, Ampere, Ada, Hopper, Blackwell) and driver 525 or newer. Without one the node errors with a message naming the machine, so the render fails instead of writing frames without a flare. Send FlareSim+ farm jobs to GPU machines only.
- **Facility install guide.** [docs/FACILITY_INSTALL.md](docs/FACILITY_INSTALL.md) covers install layouts, every environment variable, what artists need writable, quick checks and a rollout test list.
- **Split installs.** The `.py` files can sit in one folder (e.g. `.../nuke/`) with the compiled files in a per-version folder below it (e.g. `.../nuke/15.1/EL9.x86_64/`), as the original FlareSim does: the Lens Browser and Self Test find `flaresim_preview`, `lenses/` and `looks/` next to either. Or set `FLARESIM_PREVIEW_LIB` to the preview library's full path.
- **Studio lenses.** Put shared lenses in a folder on `FLARESIM_LENS_PATH` (`:`-separated on Linux and macOS, `;` on Windows). Lenses imported in the Lens Browser go to the artist's own `~/.nuke/FlareSim/lenses`, which other artists and the farm can't see.
- **Lens paths in scripts.** When a script's lens path doesn't exist on this machine (a script from another OS, a moved install), the node looks for the same file in `FLARESIM_LENS_PATH`, the bundled `lenses` folder and `~/.nuke/FlareSim/lenses`, first by the path under `lenses/`, then by file name. If it can't find it, the node shows an error rather than rendering no flare.
- **Shared looks.** Put studio looks in a folder on `FLARESIM_LOOKS_PATH`.
- **Self-test.** Run `nuke -t /path/to/FlareSim/FlareSimPlus_SelfTest.py` on a workstation and as a farm job (or **Help → FlareSim+ → Self Test** in Nuke). It checks the GPU and driver, plugin loading, the lens library, writable folders and a test render, and writes a text report to your home folder. Add `--script shot.nk` to check a script's lens paths, and `--all` to render every lens. It exits with an error when a check fails.
- **Debug logging.** Errors always go to `~/.nuke/FlareSim/logs/FlareSim_<machine>.log`. For more detail (knob events, panel layout, lens lookups, Lens Browser actions), turn on **Help → FlareSim+ → Debug Logging On or Off**, or set `FLARESIM_DEBUG=1` for a session or farm job. Then repeat the problem and use **Help → FlareSim+ → Save Debug Report**, which writes one text file to your home folder with the setup, the FlareSim+ nodes, the layout of open FlareSim+ panels and the end of the log.
- **Render trace.** For Viewer refresh problems (a flare that goes black or doesn't update), start Nuke with `FLARESIM_TRACE_LOG` set to a file path, e.g. `FLARESIM_TRACE_LOG=$HOME/flaresim_trace.log`. Both nodes then log each update, each compute (why it ran, whether Nuke cancelled it, how long it took, any CUDA error) and the first rows served for each new state; a row served without a flare is marked `<<< BLACK`. Unset, it costs nothing.

---

## Quick Start

**FlareSim** (2D source):
1. Connect your plate to the input.
2. Click **Lens Browser** on the node to pick a lens with a live preview. The header at the top of the panel shows the node's current lens.
3. Connect your shot camera to the **cam** input so the flare uses its focal length and film back. Without one, tick **Show Advanced** and set **FOV H** to match your camera.
4. Pick the flare sources. **Source Mode** defaults to **Auto Detect**, where every bright light in the plate becomes a flare source:
   - Set **View** to **Sources Only** to see which lights are picked up, without rendering the flare.
   - Adjust **Threshold** until only the lights you want are marked.
   - Under **Show Advanced**, raise **Cluster Radius** so each large light (a headlight, the sun) counts as one source instead of many.
   - Use **Source Cap** to stop one very hot light from overpowering the rest, and **Max Sources** to cap how many are traced.
   - Set **View** back to **Flare** for renders.

   To place a light yourself instead, switch **Source Mode** to **Manual XY**, put **Source XY** where the light is (animate it or link it to a Tracker), and set **Light Colour** and **Source Intensity**.
5. Adjust **Flare Gain** to taste, and raise **Quality** for the final render.

**FlareSim3D** (3D source):
1. Connect your plate to input 0, Camera to input 1, Axis (at the light position) to input 2.
2. Click **Lens Browser** to pick a lens.
3. The flare tracks the Axis through the Camera automatically.
4. Under **Show Advanced**, enable **Intensity Falloff** and set **Reference Distance** for distance-based dimming.
5. Set **Light Colour** and **Source Intensity**; the flare keeps going when the Axis leaves the frame.
6. If the light passes behind something, connect a matte of it to the **matte** input (see Occlusion Matte).

### Lens Browser
<img width="2558" height="1388" alt="image" src="https://github.com/user-attachments/assets/9fb82657-defe-4278-96b5-f086ec3e24f2" />

The **Lens Browser** button on a FlareSim or FlareSim3D node opens a window for picking a lens and building a look before you render:

- **Lens thumbnails**: every lens is shown as a small render of its flare, with the same light and settings, so you can compare them at a glance. The thumbnails sit under the preview: drag the divider between them to go from a single scrolling row (tiles grow to fill it) to a grid with more rows (the **Tile size** slider sets their size). Narrow them with the search box and the type (cine, stills, anamorphic), maker, focal length (including **Near camera** when the node has a camera) and speed filters on the left. The window remembers its size and dividers. Click a lens to preview it, double-click to apply it to the node, Page Up / Page Down to step through. Thumbnails are rendered in the background the first time (lenses on screen first) and cached in `~/.nuke/FlareSim/thumbnails`.
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

### Ready-made anamorphic lenses

`lenses/anamorphic/` has front anamorphics built on real primes: the Leica Summilux-C 40mm and 100mm T1.4, Leica Summilux-M 50mm f/1.4, and Nikkor 35mm and 85mm f/1.4, each at 1.33x, 1.8x and 2x squeeze. They show up under **Anamorphic** in the Lens Browser's type filter. Each one is the original lens with an uncoated Galilean cylindrical attachment in front (like Iscorama-style adapters and classic scope attachments), which gives the horizontal streak. They are plausible designs for flare work, not real products. `focal_length` is the vertical one; the horizontal field of view is the squeeze times wider.

FlareSim+ expects a **desqueezed** plate. It works out the squeeze from the lens (vertical over horizontal focal length) and lays the flare out desqueezed, so it lines up with the light and the ghosts come out as wide ovals. Set **FOV H** (or the camera) to the desqueezed horizontal field of view. To force a value, add a `squeeze: 2` line under `focal_length:` in the .lens file.

**Hawk-style lenses.** There is no public prescription for any Vantage Hawk lens, so `lenses/anamorphic/` also has six stand-ins tuned against Hawk test footage, each with a look of the same name: *Hawk V-Lite style* 35, 55 and 110mm (uncoated attachment, long blue streak, small blue ghost mirrored below the light, magenta veil on the 110) and *Hawk APL style* 30, 50 and 100mm (single-coated attachment; pale streak, a lavender oval ghost and six-point starburst on the 50, a red-pink oval ghost on the 100). Each lens has its iris set to the stop of the footage. The streak, the mirrored ghost and the veil were matched to light-on minus light-off frames of the footage at 1920 wide; the looks assume a white light, so use Colour From Plate or Light Colour for the light's own tint. Start from the look, then tune Flare Gain, Haze and Starburst to the shot.

Make your own from any spherical lens with `lenses/make_anamorphic.py`:

```
python3 lenses/make_anamorphic.py "lenses/lens_files/Canon_85mm_F1.24.lens" --squeeze 1.5 -o Canon_85mm_Anamorphic_1.5x.lens
```

`--coating 1` gives a single-coated attachment (subtler streak), `--glass sf6` a low-Abbe flint for rainbow ghosts. The air gap is solved so the attachment is exactly afocal, so the lens stays focused at infinity. Then use **Import Lens** in the Lens Browser.

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
--- n/a

---

## Credits

FlareSim is built on the foundational work of **Steve Watts Kennedy** ([LocalStarlight](https://github.com/LocalStarlight/flaresim_nuke)), whose original CUDA-based lens flare renderer established the core ray-tracing approach, optical physics, and lens file format.

The original physics engine is based on the work of **Eamonn Nugent** ([@space55](https://github.com/space55) · [55.dev](https://55.dev/)), whose CPU-based renderer ([blackhole-rt](https://github.com/space55/blackhole-rt/)) provided the ray-tracing foundation.

**Peter Mercell** ([petermercell/flaresim_nuke](https://github.com/petermercell/flaresim_nuke)) made it fast and portable: the optimised GPU pipeline, Linux and macOS builds, Nuke 14–17 support, anamorphic lenses, FlareSim3D, and per-surface art direction.

FlareSim+ is **Jeff Baldemoro**'s artist-focused overhaul built on Peter's fork: the Lens Browser, looks, the simpler node panel with Quality presets, placed lights, occlusion mattes, overscan, the camera input, haze, starburst, lens dirt, output layers and the FlareSim+ panel header.

Tutorial by Steve: [https://youtu.be/yEsBOQNG16Y](https://youtu.be/yEsBOQNG16Y)

---

## License

MIT — see [LICENSE](LICENSE).

Peter Mercell — [petermercell.com](https://petermercell.com)
