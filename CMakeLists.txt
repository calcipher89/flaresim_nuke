cmake_minimum_required(VERSION 3.18 FATAL_ERROR)

# ============================================================================
# FlareSim — single build for every platform
#
#   Linux / Windows : CUDA backend   (src/*.cu)
#   macOS           : Metal backend  (src/metal/*)
#
# Common usage:
#   cmake -S . -B build -DNUKE_VERSION=15.1v10
#   cmake --build build --config Release -j
#
# Options:
#   FLARESIM_BUILD_PLUGINS  (ON)  build FlareSim + FlareSim3D for Nuke
#   FLARESIM_BUILD_TESTS    (ON)  build the CPU-only optics smoke test
#
# The smoke test needs no Nuke, CUDA or Metal:
#   cmake -S . -B build-tests -DFLARESIM_BUILD_PLUGINS=OFF
#   cmake --build build-tests && ctest --test-dir build-tests
# ============================================================================

# Must be set before project() so MSVC runtime selection works.
cmake_policy(SET CMP0091 NEW)

option(FLARESIM_BUILD_PLUGINS "Build the FlareSim Nuke plugins" ON)
option(FLARESIM_BUILD_TESTS   "Build the CPU-only optics smoke test" ON)

if(APPLE)
    # macOS deployment target + architecture must be set before project().
    set(CMAKE_OSX_DEPLOYMENT_TARGET "13.0" CACHE STRING "Minimum macOS version")
    set(CMAKE_OSX_ARCHITECTURES "arm64" CACHE STRING "Build for Apple Silicon")
endif()

project(FlareSim LANGUAGES CXX)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

set(CMAKE_CXX_STANDARD 17 CACHE STRING "C++ standard")
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

if(MSVC)
    # Match Nuke's MSVC runtime — MultiThreaded DLL (/MD).
    # Mixing runtimes (/MT vs /MD) causes heap corruption.
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")
endif()

if(WIN32)
    add_compile_definitions(NOMINMAX _USE_MATH_DEFINES WIN32_LEAN_AND_MEAN _CRT_SECURE_NO_WARNINGS)
endif()

# ---------------------------------------------------------------------------
# Nuke version / ABI
#
# Nuke 14.x and earlier were built with the old pre-C++11 GCC ABI (VFX
# Reference Platform CY2022 and earlier).  Must apply to ALL languages and
# to the smoke test, so it is set before any target is defined.
# ---------------------------------------------------------------------------
if(NOT DEFINED NUKE_VERSION)
    set(NUKE_VERSION "15.1v10")
endif()
string(REGEX MATCH "^[0-9]+" NUKE_MAJOR "${NUKE_VERSION}")
if(FLARESIM_BUILD_PLUGINS AND NOT WIN32 AND NOT APPLE AND NUKE_MAJOR AND NUKE_MAJOR LESS 15)
    message(STATUS "Nuke ${NUKE_MAJOR}.x detected — using old GCC ABI (_GLIBCXX_USE_CXX11_ABI=0)")
    add_compile_definitions(_GLIBCXX_USE_CXX11_ABI=0)
endif()

# Shared, backend-independent optics code.
set(FLARESIM_CORE_SOURCES
    src/lens.cpp
    src/trace.cpp
    src/ghost.cpp
)

# ===========================================================================
# Smoke test (CPU only)
# ===========================================================================
if(FLARESIM_BUILD_TESTS)
    enable_testing()
    add_executable(flaresim_smoke src/smoke.cpp src/lens.cpp src/trace.cpp)
    target_include_directories(flaresim_smoke PRIVATE src)
    if(NOT MSVC)
        target_compile_options(flaresim_smoke PRIVATE -Wall -Wno-deprecated-declarations)
    endif()
    add_test(NAME optics_smoke COMMAND flaresim_smoke)
endif()

if(NOT FLARESIM_BUILD_PLUGINS)
    return()
endif()

# ===========================================================================
# Locate the Nuke NDK
# ===========================================================================
if(APPLE)
    if(NOT DEFINED NUKE_ROOT)
        set(NUKE_ROOT "/Applications/Nuke${NUKE_VERSION}/Nuke${NUKE_VERSION}.app/Contents/MacOS")
    endif()
    set(NDK_ROOT "${NUKE_ROOT}/include" CACHE PATH "Nuke NDK include directory")
    set(NUKE_LIB_DIR "${NUKE_ROOT}" CACHE PATH "Nuke library directory (contains libDDImage.dylib)")
    set(_ddimage_lib "${NUKE_LIB_DIR}/libDDImage.dylib")
elseif(WIN32)
    set(NDK_ROOT "C:/Program Files/Nuke${NUKE_VERSION}/include" CACHE PATH "Nuke NDK include directory")
    set(NUKE_LIB_DIR "C:/Program Files/Nuke${NUKE_VERSION}" CACHE PATH "Nuke library directory (contains DDImage.lib)")
    set(_ddimage_lib "${NUKE_LIB_DIR}/DDImage.lib")
else()
    set(NDK_ROOT "/usr/local/Nuke${NUKE_VERSION}/include" CACHE PATH "Nuke NDK include directory")
    set(NUKE_LIB_DIR "/usr/local/Nuke${NUKE_VERSION}" CACHE PATH "Nuke library directory (contains libDDImage.so)")
    set(_ddimage_lib "${NUKE_LIB_DIR}/libDDImage.so")
endif()

if(NOT EXISTS "${NDK_ROOT}/DDImage/Iop.h")
    message(FATAL_ERROR
        "Nuke NDK headers not found at ${NDK_ROOT}.\n"
        "  Set -DNUKE_VERSION=<ver>, or -DNDK_ROOT=<include dir> and -DNUKE_LIB_DIR=<lib dir>.\n"
        "  To build only the smoke test, pass -DFLARESIM_BUILD_PLUGINS=OFF.")
endif()
if(NOT EXISTS "${_ddimage_lib}")
    message(FATAL_ERROR
        "Nuke DDImage library not found: ${_ddimage_lib}\n"
        "  Set -DNUKE_VERSION=<ver> or -DNUKE_LIB_DIR=<path>.")
endif()

set(FLARESIM_TARGETS FlareSim FlareSim3D)

# ===========================================================================
# macOS — Metal backend
# ===========================================================================
if(APPLE)
    enable_language(OBJCXX)
    enable_language(ASM)
    set(CMAKE_OBJCXX_STANDARD ${CMAKE_CXX_STANDARD})

    find_library(METAL_FRAMEWORK Metal REQUIRED)
    find_library(FOUNDATION_FRAMEWORK Foundation REQUIRED)

    # Compile Metal shaders → .air → .metallib, then embed via .incbin.
    set(METAL_SOURCE "${CMAKE_CURRENT_SOURCE_DIR}/src/metal/flare_shaders.metal")
    set(METAL_AIR    "${CMAKE_BINARY_DIR}/flare_shaders.air")
    set(METALLIB     "${CMAKE_BINARY_DIR}/flare_shaders.metallib")
    set(EMBED_ASM    "${CMAKE_BINARY_DIR}/embed_metallib.s")

    add_custom_command(
        OUTPUT "${METAL_AIR}"
        COMMAND xcrun metal -std=metal3.0 -target air64-apple-macos13.0 -O2
                -c "${METAL_SOURCE}" -o "${METAL_AIR}"
        DEPENDS "${METAL_SOURCE}"
        COMMENT "Compiling Metal shaders → .air"
        VERBATIM)
    add_custom_command(
        OUTPUT "${METALLIB}"
        COMMAND xcrun metallib "${METAL_AIR}" -o "${METALLIB}"
        DEPENDS "${METAL_AIR}"
        COMMENT "Linking Metal library → .metallib"
        VERBATIM)
    file(WRITE "${EMBED_ASM}"
        ".section __DATA,__const\n"
        ".globl __binary_flare_shaders_metallib_start\n"
        ".globl __binary_flare_shaders_metallib_end\n"
        ".align 4\n"
        "__binary_flare_shaders_metallib_start:\n"
        ".incbin \"${METALLIB}\"\n"
        "__binary_flare_shaders_metallib_end:\n")
    set_source_files_properties("${EMBED_ASM}" PROPERTIES
        OBJECT_DEPENDS "${METALLIB}" LANGUAGE ASM)
    add_custom_target(embedded_metallib DEPENDS "${METALLIB}")

    set(FLARESIM_BACKEND_SOURCES src/metal/ghost_metal.mm "${EMBED_ASM}")
    add_library(FlareSim   SHARED src/metal/FlareSim.mm   ${FLARESIM_CORE_SOURCES} ${FLARESIM_BACKEND_SOURCES})
    add_library(FlareSim3D SHARED src/metal/FlareSim3D.mm ${FLARESIM_CORE_SOURCES} ${FLARESIM_BACKEND_SOURCES})

    # Objective-C++ without ARC — manual memory management to match the NDK.
    set_source_files_properties(
        src/metal/FlareSim.mm src/metal/FlareSim3D.mm src/metal/ghost_metal.mm
        PROPERTIES COMPILE_FLAGS "-fno-objc-arc")

    foreach(_tgt ${FLARESIM_TARGETS})
        add_dependencies(${_tgt} embedded_metallib)
        target_include_directories(${_tgt} PRIVATE src src/metal "${NDK_ROOT}")
        target_link_libraries(${_tgt} PRIVATE "${_ddimage_lib}" ${METAL_FRAMEWORK} ${FOUNDATION_FRAMEWORK})
        target_compile_options(${_tgt} PRIVATE
            -Wall -Wextra -Wno-unused-parameter -Wno-deprecated-declarations
            -fvisibility=hidden -O3)
        set_target_properties(${_tgt} PROPERTIES
            MACOSX_RPATH TRUE
            INSTALL_RPATH "@loader_path"
            BUILD_WITH_INSTALL_RPATH TRUE)
    endforeach()

    set(_backend_summary "Metal (Apple Silicon, ${CMAKE_OSX_ARCHITECTURES}, macOS ${CMAKE_OSX_DEPLOYMENT_TARGET}+)")

# ===========================================================================
# Linux / Windows — CUDA backend
# ===========================================================================
else()
    # CUDA architectures.  Minimum sm_70 (Volta): the ghost scatter kernel
    # uses __match_any_sync warp intrinsics.
    #
    #   sm_70  Volta        (V100, Titan V)          CUDA 9.0+
    #   sm_75  Turing       (RTX 2000, T4)           CUDA 10.0+
    #   sm_86  Ampere       (RTX 3000, A-series)     CUDA 11.1+
    #   sm_89  Ada Lovelace (RTX 4000)               CUDA 11.8+
    #   sm_90  Hopper       (H100)                   CUDA 12.0+
    #   sm_100 Blackwell    (B100/B200)              CUDA 12.8+
    #   sm_120 Blackwell    (RTX 5000)               CUDA 12.8+
    #
    # When not given, the list is chosen from the nvcc version below and
    # written to the cache, so CMake re-runs keep it.  enable_language(CUDA)
    # would otherwise cache nvcc's own default (sm_52) on the first run, so
    # remember what the user passed before it runs.
    set(_user_cuda_archs "${CMAKE_CUDA_ARCHITECTURES}")
    if(_user_cuda_archs STREQUAL "${_FLARESIM_AUTO_CUDA_ARCHS}")
        set(_user_cuda_archs "")   # our own earlier pick, not the user's
    endif()

    enable_language(CUDA)
    set(CMAKE_CUDA_STANDARD ${CMAKE_CXX_STANDARD})
    set(CMAKE_CUDA_STANDARD_REQUIRED ON)

    if(_user_cuda_archs)
        set(_archs ${_user_cuda_archs})
    elseif(CMAKE_CUDA_COMPILER_VERSION VERSION_GREATER_EQUAL 12.8)
        set(_archs 70 75 86 89 90 100 120)
    elseif(CMAKE_CUDA_COMPILER_VERSION VERSION_GREATER_EQUAL 12.0)
        set(_archs 70 75 86 89 90)
    elseif(CMAKE_CUDA_COMPILER_VERSION VERSION_GREATER_EQUAL 11.8)
        set(_archs 70 75 86 89)
    else()
        set(_archs 70 75 86)
    endif()
    set(CMAKE_CUDA_ARCHITECTURES "${_archs}" CACHE STRING "CUDA architectures to build for" FORCE)
    if(NOT _user_cuda_archs)
        set(_FLARESIM_AUTO_CUDA_ARCHS "${_archs}" CACHE INTERNAL "")
    endif()

    foreach(_arch IN LISTS CMAKE_CUDA_ARCHITECTURES)
        string(REGEX MATCH "^[0-9]+" _num "${_arch}")
        if(_num LESS 70)
            message(FATAL_ERROR
                "FlareSim requires sm_70+ (Volta or newer); sm_${_arch} is not supported.\n"
                "  Pass -DCMAKE_CUDA_ARCHITECTURES=\"86;89\" (or similar), or delete the\n"
                "  build folder to let FlareSim pick the list from your CUDA version.")
        endif()
    endforeach()

    # Static CUDA runtime — no cudart dependency at runtime on any platform.
    # With CMP0091 NEW + /MD above, static cudart links cleanly on Windows.
    set(CMAKE_CUDA_RUNTIME_LIBRARY Static)

    if(WIN32)
        # .cu files define functions (ensure_pinned_output_fp16 etc.) that the
        # .cpp files call across translation units.
        set(CMAKE_WINDOWS_EXPORT_ALL_SYMBOLS ON)
    endif()

    set(FLARESIM_BACKEND_SOURCES src/ghost_cuda.cu src/blur_cuda.cu)
    add_library(FlareSim   SHARED src/FlareSim.cpp   ${FLARESIM_CORE_SOURCES} ${FLARESIM_BACKEND_SOURCES})
    add_library(FlareSim3D SHARED src/FlareSim3D.cpp ${FLARESIM_CORE_SOURCES} ${FLARESIM_BACKEND_SOURCES})

    foreach(_tgt ${FLARESIM_TARGETS})
        target_include_directories(${_tgt} PRIVATE src "${NDK_ROOT}")
        target_link_libraries(${_tgt} PRIVATE "${_ddimage_lib}")

        if(WIN32)
            if(EXISTS "${NUKE_LIB_DIR}/Ndk.lib")
                target_link_libraries(${_tgt} PRIVATE "${NUKE_LIB_DIR}/Ndk.lib")
            endif()
        elseif(EXISTS "${NUKE_LIB_DIR}/libNdk.so")
            target_link_libraries(${_tgt} PRIVATE "${NUKE_LIB_DIR}/libNdk.so")
        endif()

        if(MSVC)
            target_compile_options(${_tgt} PRIVATE
                $<$<COMPILE_LANGUAGE:CXX>:/W3 /O2 /Ob2 /EHsc /FS /nologo /MP>
                $<$<COMPILE_LANGUAGE:CXX>:/wd4996 /wd4244 /wd4267 /wd4305 /wd4251>
                $<$<COMPILE_LANGUAGE:CUDA>:-Xcompiler=/W3 -Xcompiler=/wd4996 -Xcompiler=/wd4244
                                           -Xcompiler=/wd4267 -Xcompiler=/O2 -Xcompiler=/EHsc -Xcompiler=/FS>)
        else()
            target_compile_options(${_tgt} PRIVATE
                $<$<COMPILE_LANGUAGE:CXX>:-Wall -Wno-deprecated-declarations>)
        endif()

        # Allow constexpr on device without __device__ tags; sm_70 is still
        # supported on purpose, so silence nvcc's deprecation notice for it.
        target_compile_options(${_tgt} PRIVATE
            $<$<COMPILE_LANGUAGE:CUDA>:--expt-relaxed-constexpr -Wno-deprecated-gpu-targets>)
        set_target_properties(${_tgt} PROPERTIES CUDA_SEPARABLE_COMPILATION OFF)
    endforeach()

    set(_backend_summary "CUDA ${CMAKE_CUDA_COMPILER_VERSION}, archs ${CMAKE_CUDA_ARCHITECTURES}, static runtime")
endif()

# Plugin file names must match the Nuke node class names, with no "lib" prefix.
foreach(_tgt ${FLARESIM_TARGETS})
    set_target_properties(${_tgt} PROPERTIES PREFIX "" OUTPUT_NAME "${_tgt}")
endforeach()

# ===========================================================================
# Install — plugins + Python + lenses laid out ready for ~/.nuke/plugins
#
#   cmake --install build --prefix ~/.nuke/plugins/FlareSim
# ===========================================================================
install(TARGETS ${FLARESIM_TARGETS}
    RUNTIME DESTINATION .
    LIBRARY DESTINATION .)
install(FILES nuke/menu.py nuke/FlareSim_LensBrowser.py DESTINATION .)
install(DIRECTORY lenses/ DESTINATION lenses
    FILES_MATCHING PATTERN "*.lens")

message(STATUS "")
message(STATUS "========================================")
message(STATUS "FlareSim build configuration")
message(STATUS "  Nodes        : ${FLARESIM_TARGETS}")
message(STATUS "  Nuke version : ${NUKE_VERSION}")
message(STATUS "  NDK include  : ${NDK_ROOT}")
message(STATUS "  Nuke libs    : ${NUKE_LIB_DIR}")
message(STATUS "  Backend      : ${_backend_summary}")
message(STATUS "  C++ standard : ${CMAKE_CXX_STANDARD}")
message(STATUS "========================================")
message(STATUS "")
