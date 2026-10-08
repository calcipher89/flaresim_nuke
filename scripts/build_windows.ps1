# build_windows.ps1 - Build FlareSim + FlareSim3D for every Nuke install found.
#
# Run from a "x64 Native Tools Command Prompt for VS 2019" (Nuke's NDK
# requires the VS 2019 / v142 compiler; on VS 2022 use
# vcvars64.bat -vcvars_ver=14.29) with the CUDA 12.x toolkit installed.
# Uses the Ninja generator, which honours the CUDA toolkit on PATH.
# Full walkthrough: docs/BUILD_WINDOWS.md
#
# Usage:
#   .\scripts\build_windows.ps1
#   .\scripts\build_windows.ps1 -Versions 15,16
#   .\scripts\build_windows.ps1 -NukeRoot "D:\Nuke"
#
# Output: dist\nuke<major>\FlareSim.dll and FlareSim3D.dll

param(
    [int[]]  $Versions = @(14, 15, 16, 17),
    [string] $NukeRoot = "C:\Program Files",
    [string] $DistDir  = (Join-Path (Split-Path $PSScriptRoot -Parent) "dist")
)

$ErrorActionPreference = "Continue"
$repoDir   = Split-Path $PSScriptRoot -Parent
$succeeded = @(); $failed = @(); $skipped = @()

if (-not (Get-Command nvcc -ErrorAction SilentlyContinue)) {
    Write-Host "nvcc not found on PATH - install the CUDA 12.x toolkit first." -ForegroundColor Red
    exit 1
}
if (-not (Get-Command ninja -ErrorAction SilentlyContinue)) {
    Write-Host "ninja not found on PATH - it ships with Visual Studio's CMake tools." -ForegroundColor Red
    exit 1
}

foreach ($version in $Versions) {
    Write-Host "--- Nuke $version ---" -ForegroundColor Cyan

    # Newest patch install, e.g. "Nuke16.0v6"
    $nukeDir = Get-ChildItem $NukeRoot -Directory -Filter "Nuke${version}.*" -ErrorAction SilentlyContinue |
               Sort-Object { [version](($_.Name -replace '^Nuke', '') -replace 'v', '.') } -Descending |
               Select-Object -First 1

    if (-not $nukeDir -or -not (Test-Path (Join-Path $nukeDir.FullName "include\DDImage\Iop.h"))) {
        Write-Host "  Nuke $version (with NDK headers) not found under $NukeRoot - skipping." -ForegroundColor Yellow
        $skipped += $version
        continue
    }

    $nukePath    = $nukeDir.FullName
    $nukeVersion = $nukeDir.Name -replace '^Nuke', ''
    $buildDir    = Join-Path $repoDir "build\nuke$version"
    Write-Host "  Found: $nukePath"

    cmake -S $repoDir -B $buildDir -G Ninja `
        -DCMAKE_BUILD_TYPE=Release `
        -DCMAKE_CXX_COMPILER=cl `
        -DFLARESIM_BUILD_TESTS=OFF `
        "-DNUKE_VERSION=$nukeVersion" `
        "-DNDK_ROOT=$nukePath\include" `
        "-DNUKE_LIB_DIR=$nukePath"
    if ($LASTEXITCODE -ne 0) { Write-Host "  Configure FAILED" -ForegroundColor Red; $failed += $version; continue }

    cmake --build $buildDir
    if ($LASTEXITCODE -ne 0) { Write-Host "  Build FAILED" -ForegroundColor Red; $failed += $version; continue }

    $outDir = Join-Path $DistDir "nuke$version"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    Copy-Item (Join-Path $buildDir "FlareSim.dll"), (Join-Path $buildDir "FlareSim3D.dll") $outDir -Force
    Write-Host "  OK -> $outDir" -ForegroundColor Green
    $succeeded += $version
}

Write-Host ""
if ($succeeded.Count -gt 0) { Write-Host "Built:   Nuke $($succeeded -join ', ')" -ForegroundColor Green  }
if ($skipped.Count   -gt 0) { Write-Host "Skipped: Nuke $($skipped   -join ', ')" -ForegroundColor Yellow }
if ($failed.Count    -gt 0) { Write-Host "Failed:  Nuke $($failed    -join ', ')" -ForegroundColor Red    }
if ($failed.Count -gt 0) { exit 1 } else { exit 0 }
