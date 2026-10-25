#!/usr/bin/env bash
# package_release.sh — zip dist/ builds into one ready-to-install folder per
# Nuke version (Linux or macOS).
#
# Usage:
#   scripts/package_release.sh --version 1.0.0
#   scripts/package_release.sh --version 1.0.0 --nuke-versions "15 16"
#   scripts/package_release.sh --version 1.6.0-beta1 --nuke-versions "15" --edition Beta
#
# Each zip unpacks to FlareSim/ — drop it in ~/.nuke/plugins/ and add
#   nuke.pluginAddPath('./plugins/FlareSim')
# to ~/.nuke/init.py.
#
# --edition Beta packages a side-by-side build (scripts/build_docker.sh
# --edition Beta): FlareSimPlusBeta_v<ver>_Nuke<NV>_<os>.zip unpacking to
# FlareSimPlusBeta/, with nodes FlareSimPlusBeta / FlareSimPlusBeta3D and
# FlareSimPlusBeta_*.py, which load next to a regular FlareSim+ install.

set -euo pipefail

VERSION=""
NUKE_VERSIONS="14 15 16 17"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${REPO_DIR}/dist"
OUT_DIR="${REPO_DIR}/release_packages"
EDITION=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --version)       VERSION="$2";       shift 2 ;;
        --nuke-versions) NUKE_VERSIONS="$2"; shift 2 ;;
        --dist-dir)      DIST_DIR="$2";      shift 2 ;;
        --out-dir)       OUT_DIR="$2";       shift 2 ;;
        --edition)       EDITION="$2";       shift 2 ;;
        *) echo "Usage: $0 --version 1.0.0 [--nuke-versions \"14 15 16 17\"] [--dist-dir ./dist] [--out-dir ./release_packages] [--edition Beta]" >&2; exit 1 ;;
    esac
done
[[ -n "${VERSION}" ]] || { echo "Error: --version is required" >&2; exit 1; }
if [[ -n "${EDITION}" && ! "${EDITION}" =~ ^[A-Z][A-Za-z0-9]*$ ]]; then
    echo "Error: --edition must be a capitalised word such as Beta" >&2; exit 1
fi
SUFFIX="${EDITION:+-${EDITION,,}}"     # "" or e.g. "-beta"
PLUGIN="FlareSimPlus${EDITION}"
if [[ -n "${EDITION}" ]]; then PKG_NAME="${PLUGIN}"; else PKG_NAME="FlareSim"; fi

case "$(uname -s)" in
    Darwin) EXT=dylib; OS=macos ;;
    *)      EXT=so;    OS=linux ;;
esac

mkdir -p "${OUT_DIR}"
STAGE="$(mktemp -d)"
trap 'rm -rf "${STAGE}"' EXIT

for NV in $NUKE_VERSIONS; do
    SRC="${DIST_DIR}/nuke${NV}${SUFFIX}"
    if [[ ! -f "${SRC}/${PLUGIN}.${EXT}" || ! -f "${SRC}/${PLUGIN}3D.${EXT}" ]]; then
        echo "  Nuke ${NV}: plugins not found in ${SRC} — skipping."
        continue
    fi

    PKG="${STAGE}/${PKG_NAME}"
    rm -rf "${PKG}" && mkdir -p "${PKG}/lenses"
    cp "${SRC}/${PLUGIN}.${EXT}" "${SRC}/${PLUGIN}3D.${EXT}" "${PKG}/"
    if [[ -f "${SRC}/flaresim_preview.${EXT}" ]]; then
        cp "${SRC}/flaresim_preview.${EXT}" "${PKG}/"
    else
        echo "  Nuke ${NV}: flaresim_preview.${EXT} not found — Lens Browser preview will be off."
    fi
    if [[ -n "${EDITION}" ]]; then
        python3 "${REPO_DIR}/scripts/make_edition.py" "${EDITION}" "${REPO_DIR}/nuke" "${PKG}"
    else
        cp "${REPO_DIR}/nuke/menu.py" "${REPO_DIR}/nuke/FlareSimPlus_LensBrowser.py" "${REPO_DIR}/nuke/FlareSimPlus_Looks.py" "${REPO_DIR}/nuke/FlareSimPlus_Header.py" "${REPO_DIR}/nuke/FlareSimPlus_SelfTest.py" "${REPO_DIR}/nuke/FlareSimPlus_Log.py" "${REPO_DIR}/nuke/FlareSimPlus_Convert.py" "${REPO_DIR}/nuke/FlareSimPlus_Preview.py" "${PKG}/"
    fi
    cp -r "${REPO_DIR}/nuke/icons" "${PKG}/"
    cp -r "${REPO_DIR}/looks" "${PKG}/"
    cp -r "${REPO_DIR}/lenses/lens_files" "${REPO_DIR}/lenses/anamorphic" "${PKG}/lenses/"
    cp "${REPO_DIR}"/lenses/*.lens "${REPO_DIR}"/lenses/convert_*.py "${REPO_DIR}"/lenses/make_anamorphic.py "${PKG}/lenses/"
    cp "${REPO_DIR}/LICENSE" "${PKG}/"

    ZIP="${OUT_DIR}/${PKG_NAME}_v${VERSION}_Nuke${NV}_${OS}.zip"
    rm -f "${ZIP}"
    if command -v zip &>/dev/null; then
        (cd "${STAGE}" && zip -qr "${ZIP}" "${PKG_NAME}")
    else
        (cd "${STAGE}" && python3 -m zipfile -c "${ZIP}" "${PKG_NAME}")
    fi
    echo "  Nuke ${NV}: ${ZIP}"
done
