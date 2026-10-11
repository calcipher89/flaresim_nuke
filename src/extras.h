// ============================================================================
// extras.h — haze (veiling glare) and starburst (diffraction spikes)
//
// The two light effects that sit on top of the ghosts, brought back from the
// original FlareSim.  Both are built on the CPU from the node's light
// sources, so they work the same on every GPU backend:
//
//   Haze       a wide soft glow around each light: its light spread out by
//              a blur.  Kept at a lower resolution (it's a blur) and
//              sampled back up row by row.
//   Starburst  the aperture's Fraunhofer diffraction pattern (the squared
//              magnitude of the FFT of the iris shape) drawn at each light,
//              wider in red than in blue as in a real lens.
//
// Brightness:
//   The haze carries the light's energy, as in a real lens: bigger or
//   brighter lights give more haze, and a wider haze is fainter.  At Haze
//   Gain 1 and the default radius, a light of 8 x 8 pixels gives a haze
//   that peaks at a tenth of its brightness.
//   Starburst Gain scales the spikes; their centre is a small Airy disk,
//   about a fifth of the light's brightness at Gain 1.
// ============================================================================
#pragma once

#include "ghost.h"   // BrightPixel

#include <vector>

namespace flaresim {

struct ExtrasConfig
{
    float haze_gain        = 0.0f;   // 0 = off
    float haze_radius      = 0.15f;  // blur radius, fraction of the diagonal
    int   haze_passes      = 3;      // box-blur passes (3 ~ Gaussian)
    float starburst_gain   = 0.0f;   // 0 = off
    float starburst_scale  = 0.15f;  // spike length, fraction of the diagonal
    int   aperture_blades  = 0;      // 0 = round (no spikes, Airy rings)
    float aperture_rotation = 0.0f;  // degrees
    int   max_starbursts   = 32;     // brightest lights that get spikes
};

// Where the lights are.  Lights are stored as angles (BrightPixel); these
// map them back to pixels in the output buffer, as the ghost renderer does.
struct ExtrasFrame
{
    int   buf_w = 0, buf_h = 0;      // output buffer (the node's bbox)
    int   fmt_w = 0, fmt_h = 0;      // format
    int   fmt_x0_in_buf = 0;         // format origin inside the buffer
    int   fmt_y0_in_buf = 0;
    float tan_half_h = 1.0f;         // tan(FOV / 2)
    float tan_half_v = 1.0f;
    float diag = 0.0f;               // length the radius/scale fractions use
    float light_size = 8.0f;         // side of each light's block, pixels
    float light_scale = 1.0f;        // light colour = BrightPixel rgb * this
};

class FlareExtras
{
public:
    // Build haze and starburst for these lights.  Clears both first.
    void compute(const ExtrasConfig& cfg, const ExtrasFrame& frame,
                 const std::vector<BrightPixel>& lights);
    void clear();

    bool has_haze()      const { return !haze_[0].empty(); }
    bool has_starburst() const { return !star_[0].empty(); }
    bool empty()         const { return !has_haze() && !has_starburst(); }

    // Add (or write) channel c (0 R, 1 G, 2 B) of buffer row y, columns
    // x0..x1 (buffer coordinates) to dst[0 .. x1 - x0).
    void add_haze_row     (int c, int y, int x0, int x1, float* dst) const;
    void add_starburst_row(int c, int y, int x0, int x1, float* dst) const;

private:
    void compute_haze(const ExtrasConfig& cfg, const ExtrasFrame& frame,
                      const std::vector<BrightPixel>& lights);
    void compute_starburst(const ExtrasConfig& cfg, const ExtrasFrame& frame,
                           const std::vector<BrightPixel>& lights);

    // Haze at 1/haze_ds_ resolution.
    std::vector<float> haze_[3];
    int haze_w_ = 0, haze_h_ = 0, haze_ds_ = 1, haze_pad_ = 0;

    // Starburst at full resolution.
    std::vector<float> star_[3];
    int star_w_ = 0, star_h_ = 0;

    // Diffraction pattern, recomputed when the iris changes.
    std::vector<float> psf_;
    int   psf_n_ = 0;
    int   psf_blades_ = -1;
    float psf_rotation_ = 0.0f;
};

// The aperture's diffraction pattern: n x n, centre at (n/2, n/2), peak 1.
// n must be a power of two.
void starburst_psf(int blades, float rotation_deg, int n, std::vector<float>& out);

// One-dimensional peak of `passes` box blurs of radius r applied to a
// single pixel of value 1 (the 2D peak is its square).
float box_blur_peak(int radius, int passes);

// Separable box blur, in place.  With zero_edges, pixels outside count as
// black, so light spreading past the edge is lost (as a lens's haze leaves
// the frame); otherwise edges average over the pixels inside.
void box_blur_inplace(float* buf, int w, int h, int radius, int passes,
                      std::vector<float>& tmp, bool zero_edges = false);

} // namespace flaresim
