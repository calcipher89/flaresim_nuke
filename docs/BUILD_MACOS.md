# Building FlareSim+ for macOS

On macOS, FlareSim+ runs on the GPU through Metal. It needs an **Apple Silicon Mac** (M1 or newer) with **macOS 13 or newer**, and Nuke 15 to 17. Intel Macs aren't supported.

> **Not every feature is on macOS yet.** The Mac nodes don't have haze, starburst, lens dirt, output layers, or the Auto Detect Source Cap and Cluster Radius knobs. Everything else works the same as on Linux and Windows, including the Lens Browser, looks, the simple panel, the camera input, mattes and the FlareSim+ header. Looks saved with those features still load; the Mac just doesn't draw those parts.

## 1. Install the tools (once)

1. Install **Xcode** from the App Store and open it once to finish setup. The Command Line Tools on their own aren't enough, because they don't include the Metal shader compiler.
2. In Terminal:
   ```bash
   sudo xcode-select -s /Applications/Xcode.app
   xcrun -f metal          # should print a path
   ```
   If it says the Metal toolchain is missing, run `xcodebuild -downloadComponent MetalToolchain`.
3. Install CMake, for example with [Homebrew](https://brew.sh): `brew install cmake`.

## 2. Install Nuke

Install the Mac Nuke the studio runs (e.g. 15.1v3) in its default place, `/Applications/Nuke15.1v3/`. The build uses its NDK headers and `libDDImage.dylib`. No licence is needed for this.

## 3. Get the source

```bash
git clone https://github.com/calcipher89/flaresim_nuke.git ~/flaresim_nuke
cd ~/flaresim_nuke
```

To update later: `git pull`.

## 4. Build

```bash
cmake -S . -B build/nuke15 -DCMAKE_BUILD_TYPE=Release -DFLARESIM_BUILD_TESTS=OFF -DNUKE_VERSION=15.1v3
cmake --build build/nuke15 -j

mkdir -p dist/nuke15
cp build/nuke15/FlareSimPlus.dylib build/nuke15/FlareSimPlus3D.dylib build/nuke15/flaresim_preview.dylib dist/nuke15/
scripts/package_release.sh --version 1.0.0 --nuke-versions "15"
```

`NUKE_VERSION` must match the folder name in `/Applications`. If Nuke is somewhere else, pass `-DNUKE_ROOT=/path/to/Nuke15.1v3.app/Contents/MacOS` instead.

The result is `release_packages/FlareSim_v1.0.0_Nuke15_macos.zip`. It unpacks to a `FlareSim/` folder.

## 5. Install

1. Unzip it to `~/.nuke/plugins/FlareSim` and add this to `~/.nuke/init.py`:
   ```python
   nuke.pluginAddPath('./plugins/FlareSim')
   ```
2. If the zip was downloaded or copied from another machine, macOS may block the unsigned plugins. Clear the quarantine flag once:
   ```bash
   xattr -dr com.apple.quarantine ~/.nuke/plugins/FlareSim
   ```
3. Run the self-test:
   ```bash
   /Applications/Nuke15.1v3/Nuke15.1v3.app/Contents/MacOS/Nuke15.1 -t ~/.nuke/plugins/FlareSim/FlareSimPlus_SelfTest.py
   ```
   It writes `FlareSim_selftest_<machine>_<date>.txt` to your home folder.
