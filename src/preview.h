// ============================================================================
// preview.h — small C API for rendering a live flare preview.
//
// Used by the Lens Browser window (nuke/FlareSim_LensBrowser.py) through
// ctypes, so it has no Nuke dependency.  Renders one point source through a
// lens into an 8-bit RGBA image on the CPU.
//
// Keep FspParams in sync with the ctypes Structure in FlareSim_LensBrowser.py.
// ============================================================================
#pragma once

#if defined(_WIN32)
#  if defined(FLARESIM_PREVIEW_BUILD)
#    define FSP_API __declspec(dllexport)
#  else
#    define FSP_API __declspec(dllimport)
#  endif
#else
#  define FSP_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

#define FSP_API_VERSION 1

typedef struct FspParams
{
    float src_x, src_y;          // source position in pixels, origin top-left
    float src_r, src_g, src_b;   // source colour (linear, 1 = white)
    float source_intensity;      // same scale as the node's Source Intensity
    float flare_gain;            // same scale as the node's Flare Gain
    float fov_h_deg;             // horizontal field of view
    int   ray_grid;              // pupil samples per side
    int   aperture_blades;       // 0 = round
    float aperture_rotation;     // degrees
    float ghost_blur;            // fraction of the image diagonal
    int   ghost_blur_passes;
    float exposure;              // display exposure in stops
    int   draw_source;           // draw a glow at the source position
    int   accumulate;            // 1 = add this pass to the previous ones
    int   seed;                  // pupil jitter seed for this pass
} FspParams;

// Returns FSP_API_VERSION, so callers can check they match this header.
FSP_API int         fsp_api_version(void);

FSP_API void*       fsp_create(void);
FSP_API void        fsp_destroy(void* handle);

// Load a .lens file.  Returns the number of surfaces, or 0 on failure.
FSP_API int         fsp_load_lens(void* handle, const char* path);

// Render into out_rgba (width * height * 4 bytes, rows top to bottom).
// Each call traces one jittered pass.  With accumulate = 1 the pass is
// averaged with the previous ones (same size and settings), so calling it
// repeatedly while the user is idle refines the image.
// Returns 0 on success, -1 on error.
FSP_API int         fsp_render(void* handle, const FspParams* params,
                               int width, int height, unsigned char* out_rgba);

// Number of passes in the current accumulated image.
FSP_API int         fsp_num_passes(void* handle);

// Number of ghost pairs used by the last render.
FSP_API int         fsp_num_pairs(void* handle);

// Last error or warning message ("" when none).
FSP_API const char* fsp_last_error(void* handle);

#ifdef __cplusplus
}
#endif
