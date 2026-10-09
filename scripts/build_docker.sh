#!/usr/bin/env bash
# build_docker.sh — reproducible Linux builds inside ASWF containers, one per
# Nuke version, so each gets the VFX Reference Platform compiler it expects.
#
# Nuke itself is not in the image: the local Nuke install is mounted
# read-only for its NDK headers and libDDImage.so.
#
# Usage:
#   scripts/build_docker.sh --images                 # build the images once (cached afterwards)
#   scripts/build_docker.sh                          # build plugins for Nuke 14–17
#   scripts/build_docker.sh --versions "16"
#   scripts/build_docker.sh --nuke-root /opt
#
# Images (docker/Dockerfile = ASWF base + minimal CUDA nvcc):
#   flaresim-build:nuke14  aswf/ci-vfxall:2022  GCC 9  + CUDA 12.4
#   flaresim-build:nuke15  aswf/ci-vfxall:2023  GCC 11 + CUDA 12.8
#   flaresim-build:nuke16  aswf/ci-vfxall:2024  GCC 11 + CUDA 12.8
#   flaresim-build:nuke17  aswf/ci-vfxall:2025  GCC 11 + CUDA 12.8
#
# Output: dist/nuke<major>/FlareSim.so, FlareSim3D.so and flaresim_preview.so

set -euo pipefail

VERSIONS="14 15 16 17"
NUKE_ROOT="/usr/local"
BUILD_IMAGES=false
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${REPO_DIR}/dist"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --images)    BUILD_IMAGES=true; shift ;;
        --versions)  VERSIONS="$2";  shift 2 ;;
        --nuke-root) NUKE_ROOT="$2"; shift 2 ;;
        --dist-dir)  DIST_DIR="$2";  shift 2 ;;
        *) echo "Usage: $0 [--images] [--versions \"14 15 16 17\"] [--nuke-root /usr/local] [--dist-dir ./dist]" >&2; exit 1 ;;
    esac
done

declare -A BASE_IMAGE=(
    [14]="aswf/ci-vfxall:2022"
    [15]="aswf/ci-vfxall:2023"
    [16]="aswf/ci-vfxall:2024"
    [17]="aswf/ci-vfxall:2025"
)

if [[ "${BUILD_IMAGES}" == "true" ]]; then
    for V in $VERSIONS; do
        echo "--- flaresim-build:nuke${V} (base ${BASE_IMAGE[$V]}) ---"
        docker build --build-arg BASE_IMAGE="${BASE_IMAGE[$V]}" \
                     -t "flaresim-build:nuke${V}" "${REPO_DIR}/docker"
    done
    exit 0
fi

succeeded=(); failed=(); skipped=()

for V in $VERSIONS; do
    echo "=== Nuke ${V} ==="
    IMAGE="flaresim-build:nuke${V}"
    if ! docker image inspect "${IMAGE}" &>/dev/null; then
        echo "  Image ${IMAGE} missing — run: $0 --images --versions \"${V}\""
        skipped+=("${V}"); continue
    fi
    NUKE_DIR=$(find "${NUKE_ROOT}" -maxdepth 1 -type d -name "Nuke${V}.*" 2>/dev/null | sort -V | tail -1)
    if [[ -z "${NUKE_DIR}" || ! -f "${NUKE_DIR}/include/DDImage/Iop.h" ]]; then
        echo "  Nuke ${V} (with NDK headers) not found under ${NUKE_ROOT} — skipping."
        skipped+=("${V}"); continue
    fi
    NUKE_VERSION="${NUKE_DIR##*/Nuke}"
    echo "  Nuke install : ${NUKE_DIR}"

    # Build as the calling user so build/ and dist/ are not root-owned.
    if docker run --rm \
            --user "$(id -u):$(id -g)" \
            -v "${REPO_DIR}:/src" \
            -v "${NUKE_DIR}:/nuke:ro" \
            "${IMAGE}" \
            bash -c "
                set -euo pipefail
                NVCC=\$(ls -d /usr/local/cuda-*/bin/nvcc 2>/dev/null | sort -V | tail -1)
                export PATH=\"\$(dirname \"\${NVCC}\"):\${PATH}\"
                nvcc --version | tail -1
                cmake -S /src -B /src/build/docker-nuke${V} \
                    -DCMAKE_BUILD_TYPE=Release \
                    -DFLARESIM_BUILD_TESTS=OFF \
                    -DNUKE_VERSION=${NUKE_VERSION} \
                    -DNDK_ROOT=/nuke/include \
                    -DNUKE_LIB_DIR=/nuke
                cmake --build /src/build/docker-nuke${V} -j\$(nproc)
            "; then
        mkdir -p "${DIST_DIR}/nuke${V}"
        cp "${REPO_DIR}/build/docker-nuke${V}/FlareSim.so" \
           "${REPO_DIR}/build/docker-nuke${V}/FlareSim3D.so" \
           "${REPO_DIR}/build/docker-nuke${V}/flaresim_preview.so" "${DIST_DIR}/nuke${V}/"
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
