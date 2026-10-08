#!/usr/bin/env bash
# build_linux.sh — build FlareSim + FlareSim3D for every Nuke install found
# on this machine, using the local compiler and CUDA toolkit.
#
# Usage:
#   scripts/build_linux.sh
#   scripts/build_linux.sh --versions "15 16"      # only these major versions
#   scripts/build_linux.sh --nuke-root /opt         # where Nuke<ver> folders live
#   scripts/build_linux.sh --dist-dir ./dist
#
# Output: dist/nuke<major>/FlareSim.so and FlareSim3D.so
#
# Compiler: Nuke 14 expects GCC 9.3–9.5, Nuke 15+ GCC 11.  The CMake build
# sets the libstdc++ ABI from the Nuke version; picking the right GCC is up to
# the environment.  For reproducible builds use scripts/build_docker.sh.

set -euo pipefail

VERSIONS="14 15 16 17"
NUKE_ROOT="/usr/local"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${REPO_DIR}/dist"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --versions)  VERSIONS="$2";  shift 2 ;;
        --nuke-root) NUKE_ROOT="$2"; shift 2 ;;
        --dist-dir)  DIST_DIR="$2";  shift 2 ;;
        *) echo "Usage: $0 [--versions \"14 15 16 17\"] [--nuke-root /usr/local] [--dist-dir ./dist]" >&2; exit 1 ;;
    esac
done

succeeded=(); failed=(); skipped=()

for V in $VERSIONS; do
    echo "--- Nuke ${V} ---"
    # Newest patch install, e.g. /usr/local/Nuke16.0v6
    NUKE_DIR=$(find "${NUKE_ROOT}" -maxdepth 1 -type d -name "Nuke${V}.*" 2>/dev/null | sort -V | tail -1)
    if [[ -z "${NUKE_DIR}" || ! -f "${NUKE_DIR}/include/DDImage/Iop.h" ]]; then
        echo "  Nuke ${V} (with NDK headers) not found under ${NUKE_ROOT} — skipping."
        skipped+=("${V}"); continue
    fi
    NUKE_VERSION="${NUKE_DIR##*/Nuke}"
    BUILD_DIR="${REPO_DIR}/build/nuke${V}"
    echo "  Found ${NUKE_DIR}"

    if cmake -S "${REPO_DIR}" -B "${BUILD_DIR}" \
            -DCMAKE_BUILD_TYPE=Release \
            -DFLARESIM_BUILD_TESTS=OFF \
            -DNUKE_VERSION="${NUKE_VERSION}" \
            -DNDK_ROOT="${NUKE_DIR}/include" \
            -DNUKE_LIB_DIR="${NUKE_DIR}" \
       && cmake --build "${BUILD_DIR}" -j"$(nproc)"; then
        mkdir -p "${DIST_DIR}/nuke${V}"
        cp "${BUILD_DIR}/FlareSim.so" "${BUILD_DIR}/FlareSim3D.so" "${DIST_DIR}/nuke${V}/"
        echo "  OK -> ${DIST_DIR}/nuke${V}"
        succeeded+=("${V}")
    else
        echo "  Build FAILED"
        failed+=("${V}")
    fi
done

echo ""
[[ ${#succeeded[@]} -gt 0 ]] && echo "Built:   Nuke ${succeeded[*]}"
[[ ${#skipped[@]}   -gt 0 ]] && echo "Skipped: Nuke ${skipped[*]}"
[[ ${#failed[@]}    -gt 0 ]] && echo "Failed:  Nuke ${failed[*]}"
[[ ${#failed[@]} -eq 0 ]]
