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
//   Dirt light the light that reaches dust and smudges on the front of the
//              lens: a very wide glow of each light.  The node multiplies
//              it by the dirt image, so specks near a light glow.
//   Starburst  the aperture's Fraunhofer diffraction pattern (the squared
//              magnitude of the FFT of the iris shape) drawn at each light,
//              wider in red than in blue as in a real lens.
//
// Brightness:
//   The haze carries the light's energy, as in a real lens: bigger or
//   brighter lights give more haze, and a wider haze is fainter.  At Haze
//   Gain 1 and the default radius, a light of 8 x 8 pixels gives a haze
//   that peaks at a tenth of its brightness.
//   Starburst Gain uses the original FlareSim's scale (typical 50 to 500):
//   at the default Source Intensity of 8 a gain means the same as there.
//   The spikes are faint next to their centre, a small Airy disk, so they
//   need a large gain to show.
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
    // 0 = one soft glow of Haze Radius.  Towards 1, more of the light goes
    // into tighter glows (a third and a ninth of the radius), so the haze
    // is bright near the light and falls off about as 1/distance, as the
    // veiling glare of a real lens does.  The total light stays the same.
    float haze_shape       = 0.0f;
    float haze_tint[3]     = { 1.0f, 1.0f, 1.0f };   // multiplies the light's colour
    float starburst_gain   = 0.0f;   // 0 = off
    float starburst_scale  = 0.15f;  // spike length, fraction of the diagonal
    float starburst_tint[3] = { 1.0f, 1.0f, 1.0f };
    // How much wider the spikes are in red than in blue: 1 = as in a real
    // lens (by wavelength), 0 = the same in every colour (no colour fringes).
    float starburst_fringe = 1.0f;
    // Softens the spikes: blur, as a fraction of the spike length.
    float starburst_softness = 0.0f;
    int   aperture_blades  = 0;      // 0 = round (no spikes, Airy rings)
    float aperture_rotation = 0.0f;  // degrees
    int   max_starbursts   = 32;     // brightest lights that get spikes
    float dirt_gain        = 0.0f;   // 0 = off: light falling on lens dirt
    float dirt_spread      = 0.4f;   // how far it reaches, fraction of the diagonal
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

// A wide glow of the lights, kept at low resolution (it's a blur) and
// sampled back up row by row.  Used for the haze and for the light that
// falls on lens dirt.
struct GlowField
{
    std::vector<float> ch[3];
    int w = 0, h = 0, ds = 1, pad = 0;

    // Energy-conserving: Gain 1 gives ref_peak of a light of 8 x 8 pixels
    // at radius ref_radius; a wider glow is fainter.
    void compute(float gain, float radius, int passes, float ref_radius,
                 float ref_peak, const ExtrasFrame& frame,
                 const std::vector<BrightPixel>& lights);
    void clear();
    void tint(const float rgb[3]);
    bool empty() const { return ch[0].empty(); }
    void add_row(int c, int y, int x0, int x1, float* dst) const;
};

// How the lens dirt image is treated before the light reaches it.
struct DirtLook
{
    float softness  = 0.01f;   // defocus radius, fraction of the diagonal; 0 = sharp
    float scatter   = 0.0f;    // share of the dirt's light spread into a soft veil
    float scatter_radius = 0.08f;  // how far the veil spreads, fraction of the diagonal
    int   aperture_blades = 0;     // blob shape: the iris (0 = round)
    float aperture_rotation = 0.0f;
};

class FlareExtras
{
public:
    // Build haze and starburst for these lights.  Clears both first.
    void compute(const ExtrasConfig& cfg, const ExtrasFrame& frame,
                 const std::vector<BrightPixel>& lights);
    void clear();

    bool has_haze()      const { return !haze_.empty() || !haze_core_[0].empty()
                                        || !haze_core_[1].empty(); }
    bool has_starburst() const { return !star_[0].empty(); }
    bool has_dirt()      const { return !dirt_.empty(); }
    bool has_dirt_image() const { return !dirt_img_[0].empty(); }
    bool has_scatter()   const { return !scatter_.empty(); }
    bool empty()         const { return !has_haze() && !has_starburst() && !has_dirt()
                                        && !has_scatter(); }

    // Lens dirt: the dirt image for the whole buffer (buf_w x buf_h per
    // channel, row 0 at the bottom), defocused into soft blobs the shape
    // of the iris, and the veil the dirt scatters from the light on it.
    // Call after compute(); takes the image.
    void set_dirt(const DirtLook& look, const ExtrasFrame& frame,
                  std::vector<float> rgb[3]);
    // Channel c of the softened dirt for buffer row y (buf_w values), or
    // null when there is no dirt image.
    const float* dirt_row(int c, int y) const;
    int dirt_width() const { return dirt_w_; }
    void add_scatter_row(int c, int y, int x0, int x1, float* dst) const
    { scatter_.add_row(c, y, x0, x1, dst); }

    // Add (or write) channel c (0 R, 1 G, 2 B) of buffer row y, columns
    // x0..x1 (buffer coordinates) to dst[0 .. x1 - x0).
    void add_haze_row     (int c, int y, int x0, int x1, float* dst) const
    {
        haze_.add_row(c, y, x0, x1, dst);
        haze_core_[0].add_row(c, y, x0, x1, dst);
        haze_core_[1].add_row(c, y, x0, x1, dst);
    }
    // The light reaching the lens dirt (multiply by the dirt image).
    void add_dirt_light_row(int c, int y, int x0, int x1, float* dst) const
    { dirt_.add_row(c, y, x0, x1, dst); }
    void add_starburst_row(int c, int y, int x0, int x1, float* dst) const;

private:
    void compute_starburst(const ExtrasConfig& cfg, const ExtrasFrame& frame,
                           const std::vector<BrightPixel>& lights);

    GlowField haze_;
    GlowField haze_core_[2];   // Haze Shape: tighter glows near the light
    GlowField dirt_;      // light on the lens dirt
    std::vector<float> dirt_img_[3];   // softened dirt image, full res
    int dirt_w_ = 0, dirt_h_ = 0;
    GlowField scatter_;   // veil scattered by the lit dirt

    // Starburst at full resolution.
    std::vector<float> star_[3];
    int star_w_ = 0, star_h_ = 0;

    // Diffraction pattern, recomputed when the iris changes.
    std::vector<float> psf_;
    int   psf_n_ = 0;
    int   psf_blades_ = -1;
    float psf_rotation_ = 0.0f;
    float psf_softness_ = 0.0f;
};

// Defocus an image (in place) with a blob the shape of the iris, radius
// in pixels.  Worked at a lower resolution for big radii.
void defocus_inplace(float* img, int w, int h, float radius, int blades, float rotation_deg);

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
