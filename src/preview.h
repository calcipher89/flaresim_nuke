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

#define FSP_API_VERSION 2

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

// Per-surface art direction, matching the node's Surfaces tab (surf_*
// knobs).  A ghost bounces off two surfaces, A and B: it is dropped when
// either is disabled, its gain and colour are the product of both, its
// offsets add and its scales multiply.
typedef struct FspSurface
{
    int   enabled;
    float gain;
    float r, g, b;               // tint
    float offset_x, offset_y;    // fraction of the image width, +y is up
    float scale;                 // about the image centre
} FspSurface;

// One surface of the loaded lens, for drawing the lens diagram.
typedef struct FspSurfaceInfo
{
    float radius;                // mm, 0 = flat (Rx for toric)
    float radius_y;              // toric only
    float thickness;             // mm to the next surface
    float ior;                   // medium after this surface
    float abbe_v;
    float semi_aperture;         // mm
    float z;                     // vertex position, mm
    int   coating;               // AR layers, 0 = uncoated
    int   is_stop;
    int   surface_type;          // 0 spherical, 1 cyl x, 2 cyl y, 3 toric
} FspSurfaceInfo;

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

// Per-surface settings for the following renders and picks.  n = 0
// clears them (every surface on, neutral).  Surfaces past n are neutral.
FSP_API void        fsp_set_surfaces(void* handle, const FspSurface* surfaces, int n);

// Highlight ghosts in the following renders by dimming the rest:
//   a < 0          no highlight
//   b < 0          every ghost that bounces off surface a
//   otherwise      the ghost bouncing off surfaces a and b
FSP_API void        fsp_set_highlight(void* handle, int surf_a, int surf_b);

// Describe surface index of the loaded lens.  Returns 0, or -1 when there
// is no such surface.
FSP_API int         fsp_surface_info(void* handle, int index, FspSurfaceInfo* out);

// Axial position of the sensor plane (mm) of the loaded lens.
FSP_API float       fsp_sensor_z(void* handle);

// Which ghosts light up pixel (x, y) (origin top-left) of a width x height
// render with these settings?  Writes up to max_out ghosts, brightest
// first, as their two surfaces and their share of the light there (0..1).
// Returns how many were written; 0 when nothing reaches that pixel.
FSP_API int         fsp_pick_ghosts(void* handle, const FspParams* params,
                                    int width, int height, float x, float y,
                                    int* out_surf_a, int* out_surf_b,
                                    float* out_share, int max_out);

#ifdef __cplusplus
}
#endif
