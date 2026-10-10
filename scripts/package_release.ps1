# package_release.ps1 - Zip dist\ builds into one ready-to-install folder per
# Nuke version (Windows).
#
# Usage:
#   .\scripts\package_release.ps1 -Version 1.0.0
#   .\scripts\package_release.ps1 -Version 1.0.0 -NukeVersions 15,16
#
# Each zip unpacks to FlareSim\ - drop it in %USERPROFILE%\.nuke\plugins\ and add
#   nuke.pluginAddPath('./plugins/FlareSim')
# to %USERPROFILE%\.nuke\init.py.

param(
    [Parameter(Mandatory = $true)] [string] $Version,
    [int[]]  $NukeVersions = @(14, 15, 16, 17),
    [string] $DistDir = (Join-Path (Split-Path $PSScriptRoot -Parent) "dist"),
    [string] $OutDir  = (Join-Path (Split-Path $PSScriptRoot -Parent) "release_packages")
)

$ErrorActionPreference = "Stop"
$repoDir = Split-Path $PSScriptRoot -Parent
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

foreach ($nv in $NukeVersions) {
    $src = Join-Path $DistDir "nuke$nv"
    if (-not (Test-Path (Join-Path $src "FlareSim.dll")) -or -not (Test-Path (Join-Path $src "FlareSim3D.dll"))) {
        Write-Host "  Nuke ${nv}: plugins not found in $src - skipping." -ForegroundColor Yellow
        continue
    }

    $stage = Join-Path ([System.IO.Path]::GetTempPath()) ("flaresim_" + [guid]::NewGuid())
    $pkg   = Join-Path $stage "FlareSim"
    New-Item -ItemType Directory -Force -Path (Join-Path $pkg "lenses") | Out-Null

    Copy-Item (Join-Path $src "FlareSim.dll"), (Join-Path $src "FlareSim3D.dll") $pkg
    $preview = Join-Path $src "flaresim_preview.dll"
    if (Test-Path $preview) { Copy-Item $preview $pkg }
    else { Write-Host "  Nuke ${nv}: flaresim_preview.dll not found - Lens Browser preview will be off." -ForegroundColor Yellow }
    Copy-Item (Join-Path $repoDir "nuke\menu.py"), (Join-Path $repoDir "nuke\FlareSim_LensBrowser.py"), (Join-Path $repoDir "nuke\FlareSim_Looks.py"), (Join-Path $repoDir "nuke\FlareSim_Header.py") $pkg
    Copy-Item (Join-Path $repoDir "nuke\icons") $pkg -Recurse
    Copy-Item (Join-Path $repoDir "looks") $pkg -Recurse
    Copy-Item (Join-Path $repoDir "lenses\lens_files") (Join-Path $pkg "lenses") -Recurse
    Copy-Item (Join-Path $repoDir "lenses\*.lens"), (Join-Path $repoDir "lenses\convert_*.py") (Join-Path $pkg "lenses")
    Copy-Item (Join-Path $repoDir "LICENSE") $pkg

    $zip = Join-Path $OutDir "FlareSim_v${Version}_Nuke${nv}_windows.zip"
    if (Test-Path $zip) { Remove-Item $zip }
    Compress-Archive -Path $pkg -DestinationPath $zip
    Remove-Item $stage -Recurse -Force
    Write-Host "  Nuke ${nv}: $zip" -ForegroundColor Green
}
