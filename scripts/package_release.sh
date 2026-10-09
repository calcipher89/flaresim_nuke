#!/usr/bin/env bash
# package_release.sh — zip dist/ builds into one ready-to-install folder per
# Nuke version (Linux or macOS).
#
# Usage:
#   scripts/package_release.sh --version 1.0.0
#   scripts/package_release.sh --version 1.0.0 --nuke-versions "15 16"
#
# Each zip unpacks to FlareSim/ — drop it in ~/.nuke/plugins/ and add
#   nuke.pluginAddPath('./plugins/FlareSim')
# to ~/.nuke/init.py.

set -euo pipefail

VERSION=""
NUKE_VERSIONS="14 15 16 17"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${REPO_DIR}/dist"
OUT_DIR="${REPO_DIR}/release_packages"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --version)       VERSION="$2";       shift 2 ;;
        --nuke-versions) NUKE_VERSIONS="$2"; shift 2 ;;
        --dist-dir)      DIST_DIR="$2";      shift 2 ;;
        --out-dir)       OUT_DIR="$2";       shift 2 ;;
        *) echo "Usage: $0 --version 1.0.0 [--nuke-versions \"14 15 16 17\"] [--dist-dir ./dist] [--out-dir ./release_packages]" >&2; exit 1 ;;
    esac
done
[[ -n "${VERSION}" ]] || { echo "Error: --version is required" >&2; exit 1; }

case "$(uname -s)" in
    Darwin) EXT=dylib; OS=macos ;;
    *)      EXT=so;    OS=linux ;;
esac

mkdir -p "${OUT_DIR}"
STAGE="$(mktemp -d)"
trap 'rm -rf "${STAGE}"' EXIT

for NV in $NUKE_VERSIONS; do
    SRC="${DIST_DIR}/nuke${NV}"
    if [[ ! -f "${SRC}/FlareSim.${EXT}" || ! -f "${SRC}/FlareSim3D.${EXT}" ]]; then
        echo "  Nuke ${NV}: plugins not found in ${SRC} — skipping."
        continue
    fi

    PKG="${STAGE}/FlareSim"
    rm -rf "${PKG}" && mkdir -p "${PKG}/lenses"
    cp "${SRC}/FlareSim.${EXT}" "${SRC}/FlareSim3D.${EXT}" "${PKG}/"
    cp "${REPO_DIR}/nuke/menu.py" "${REPO_DIR}/nuke/FlareSim_LensBrowser.py" "${REPO_DIR}/nuke/FlareSim_Looks.py" "${PKG}/"
    cp -r "${REPO_DIR}/looks" "${PKG}/"
    cp -r "${REPO_DIR}/lenses/lens_files" "${PKG}/lenses/"
    cp "${REPO_DIR}"/lenses/*.lens "${REPO_DIR}"/lenses/convert_*.py "${PKG}/lenses/"
    cp "${REPO_DIR}/LICENSE" "${PKG}/"

    ZIP="${OUT_DIR}/FlareSim_v${VERSION}_Nuke${NV}_${OS}.zip"
    rm -f "${ZIP}"
    if command -v zip &>/dev/null; then
        (cd "${STAGE}" && zip -qr "${ZIP}" FlareSim)
    else
        (cd "${STAGE}" && python3 -m zipfile -c "${ZIP}" FlareSim)
    fi
    echo "  Nuke ${NV}: ${ZIP}"
done
