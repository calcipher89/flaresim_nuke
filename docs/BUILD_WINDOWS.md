# Building FlareSim on Windows for Nuke 15

This guide builds `FlareSim.dll` and `FlareSim3D.dll` for Nuke 15.x on Windows, then installs them with the menu entries, the Lens Browser and the lens library. Other Nuke versions use the same steps, but check that version's NDK guide (Appendix A, Windows) for the Visual Studio version it requires.

## 1. Install the tools

| Tool | Version | Notes |
|---|---|---|
| **Nuke** | 15.0 / 15.1 / 15.2 | Already installed. The NDK headers (`include\`) and `DDImage.lib` ship with every Nuke install. |
| **Visual Studio** | 2019, or 2022 with the **MSVC v142 (VS 2019)** toolset | Foundry's [Nuke 15 NDK guide](https://learn.foundry.com/nuke/developers/15.1/ndkdevguide/appendixa/windows.html) says plugins must be built with Visual Studio 2019's compiler. Free Community or Build Tools editions are fine. |
| **CUDA Toolkit** | 12.4 – 12.8 | Get it from [developer.nvidia.com/cuda-downloads](https://developer.nvidia.com/cuda-downloads). Use 12.8 if you need RTX 5000 (Blackwell) support. Install it **after** Visual Studio so it registers with it. |
| **NVIDIA driver** | 528 or newer | Only needed on machines that run the plugin. The CUDA runtime is linked statically, so artists don't need the CUDA toolkit. |
| **Git** | any | Or download the repo as a zip from GitHub. |

In the Visual Studio Installer, tick:

- **Desktop development with C++**
- Under *Individual components*: **C++ CMake tools for Windows** (this provides `cmake` and `ninja`).
- If you're on VS 2022: **MSVC v142 – VS 2019 C++ x64/x86 build tools**.

## 2. Get the source

```bat
git clone https://github.com/calcipher89/flaresim_nuke.git
cd flaresim_nuke
```

## 3. Open a VS 2019 developer prompt

From the Start menu, open **x64 Native Tools Command Prompt for VS 2019**.

If you only have VS 2022, open a normal `cmd` window and load the v142 toolset into it:

```bat
"C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat" -vcvars_ver=14.29
```

(Change `Community` to `Professional`, `Enterprise` or `BuildTools` to match your install.)

Check that everything is found:

```bat
cl
nvcc --version
cmake --version
ninja --version
```

`cl` should report version **19.29** (the VS 2019 compiler). `nvcc` should report release 12.x.

## 4. Build

Replace `15.1v5` with your exact Nuke folder name under `C:\Program Files\`.

```bat
cmake -S . -B build\nuke15 -G Ninja ^
      -DCMAKE_BUILD_TYPE=Release ^
      -DCMAKE_CXX_COMPILER=cl ^
      -DNUKE_VERSION=15.1v5

cmake --build build\nuke15
```

If Nuke isn't in `C:\Program Files`, point at it directly:

```bat
cmake -S . -B build\nuke15 -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=cl ^
      -DNDK_ROOT="D:/Apps/Nuke15.1v5/include" ^
      -DNUKE_LIB_DIR="D:/Apps/Nuke15.1v5"
```

The configure step prints a summary. It should look like this:

```
FlareSim build configuration
  Nodes        : FlareSim;FlareSim3D
  Nuke version : 15.1v5
  Backend      : CUDA 12.8, archs 70;75;86;89;90;100;120, static runtime
```

To build a smaller, faster-compiling plugin for just your own GPU, add for example `-DCMAKE_CUDA_ARCHITECTURES=86` (RTX 3000), `89` (RTX 4000) or `120` (RTX 5000).

The output is `build\nuke15\FlareSim.dll` and `build\nuke15\FlareSim3D.dll`.

## 5. Install

The install step copies the plugins, the preview library (`flaresim_preview.dll`), `menu.py`, the Lens Browser and the lens library into one folder:

```bat
cmake --install build\nuke15 --prefix "%USERPROFILE%\.nuke\plugins\FlareSim"
```

Then add this line to `%USERPROFILE%\.nuke\init.py` (create the file if it doesn't exist):

```python
nuke.pluginAddPath('./plugins/FlareSim')
```

Restart Nuke 15. You should see:

- **Filter → FlareSim** and **Filter → FlareSim3D** in the node menu
- A **Lens Browser** button on each FlareSim node, and **Window → FlareSim Lens Browser**, both opening the Lens Browser window on the bundled lens library

## 6. Quick test in Nuke

1. Read in a plate with a bright light, or use a `Constant` with a small bright `Radial` merged on top.
2. Add **FlareSim** after it.
3. Click **Lens Browser** on the node, type `50mm` in the search box, click a lens thumbnail (for example the Canon New FD 50mm f/1.4), drag the light around the preview, then click **Apply to Node**.
4. **Source Mode** is **Auto Detect** by default. Set **View** to **Sources Only**, adjust **Threshold** until only the light is marked, then set **View** back to **Flare**.
5. Adjust **Flare Gain**.
6. In the Lens Browser, open the **Lens Elements** tab and Shift+click a ghost in the preview. Pick the ghost in the list, change its surface's **Tint** or **Gain**, and click **Apply to Node**: the node's Surfaces tab shows the same values.

If CUDA fails (no GPU, old driver), the node shows the error in red.

## Building several Nuke versions at once

From the same developer prompt:

```bat
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1 -Versions 15
```

It finds the newest `Nuke15.*` under `C:\Program Files` and writes `dist\nuke15\FlareSim.dll` and `FlareSim3D.dll`. Use `-Versions 14,15,16` for more versions, or `-NukeRoot "D:\Apps"` for a different install location. Then `scripts\package_release.ps1 -Version 1.0.0` zips each one for a release.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Nuke NDK headers not found` | The path in `NUKE_VERSION` doesn't match your folder name. Use the exact name (e.g. `15.1v5`), or pass `NDK_ROOT` / `NUKE_LIB_DIR`. |
| `No CUDA toolset found` or `nvcc not found` | The prompt was opened before CUDA was installed, or CUDA isn't on `PATH`. Open a new developer prompt, or reinstall CUDA after Visual Studio. |
| `unsupported Microsoft Visual Studio version` from nvcc | Your CUDA version is older than your MSVC. Use CUDA 12.4 or newer with the v142 toolset. |
| `cl` reports 19.3x or 19.4x | That's the VS 2022 compiler. Reopen the prompt with `-vcvars_ver=14.29` (step 3). |
| Nuke says *"FlareSim.dll: The specified module could not be found"* | Usually a missing or old NVIDIA driver. Update to 528+. |
| Nodes don't appear in the menu | `init.py` doesn't add the folder, or `menu.py` isn't next to the DLLs. Re-run the install step. |
| A stale build after changing options | Delete `build\nuke15` and configure again. |
