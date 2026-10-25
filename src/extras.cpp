// ============================================================================
// extras.cpp — haze and starburst (see extras.h)
//
// The starburst is the original FlareSim's (starburst.cpp): rasterise the
// iris, 2D FFT, |FFT|^2 with the centre moved to the middle, peak 1, then
// sample it at each light with a scale per channel (650 / 550 / 450 nm).
// ============================================================================
#include "extras.h"

#include <algorithm>
#include <cmath>
#include <complex>
#include <cstdint>
#include <cstring>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

namespace flaresim {

namespace {

// Haze Gain 1: a light of kRefLightArea pixels at Haze Radius kRefRadius
// gives a haze peaking at kRefPeak of its brightness.
constexpr float kRefLightArea = 64.0f;
constexpr float kRefRadius    = 0.15f;
constexpr float kRefPeak      = 0.1f;
// Dirt Gain 1: the same light at Dirt Spread kDirtRefRadius lights the
// dirt at kDirtRefPeak of its brightness (dirt value 1 = that much light).
constexpr float kDirtRefRadius = 0.4f;
constexpr float kDirtRefPeak   = 0.25f;
constexpr int   kPsfSize      = 512;
constexpr float kDefocusRadius = 6.0f;   // low-res blob radius the dirt defocus keeps
constexpr int   kHazeMinRadius = 12;     // low-res blur radius the haze keeps
// Starburst Gain unit.  The original FlareSim drew the plate's own values
// times the gain; lights here carry Source Intensity (default 8), so a gain
// is divided by 8 to keep the original's numbers.
constexpr float kStarburstUnit = 1.0f / 8.0f;
// Rays and Ring Gain 1: their brightest point is a hundredth of the light's
// brightness.
constexpr float kRaysUnit = 0.01f;
constexpr float kRingUnit = 0.01f;
// Starburst Reach: the diffraction core's radius in pattern pixels; the
// spikes are boosted from there out.
constexpr float kPsfCore = 1.5f;

// 1D radix-2 Cooley-Tukey FFT, in place, n a power of two.
void fft1d(std::complex<float>* data, int n)
{
    for (int i = 1, j = 0; i < n; ++i) {
        int bit = n >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) std::swap(data[i], data[j]);
    }
    for (int len = 2; len <= n; len <<= 1) {
        const float ang = -2.0f * (float)M_PI / len;
        const std::complex<float> wlen(std::cos(ang), std::sin(ang));
        for (int i = 0; i < n; i += len) {
            std::complex<float> w(1.0f, 0.0f);
            for (int j = 0; j < len / 2; ++j) {
                const std::complex<float> u = data[i + j];
                const std::complex<float> v = data[i + j + len / 2] * w;
                data[i + j]           = u + v;
                data[i + j + len / 2] = u - v;
                w *= wlen;
            }
        }
    }
}

void fft2d(std::vector<std::complex<float>>& grid, int n)
{
    for (int r = 0; r < n; ++r)
        fft1d(grid.data() + (size_t)r * n, n);
    std::vector<std::complex<float>> col(n);
    for (int c = 0; c < n; ++c) {
        for (int r = 0; r < n; ++r) col[r] = grid[(size_t)r * n + c];
        fft1d(col.data(), n);
        for (int r = 0; r < n; ++r) grid[(size_t)r * n + c] = col[r];
    }
}

// Same round / polygon test as the pupil sampler in ghost_cuda.cu, so the
// spikes match the iris the ghosts are traced through.
struct Iris
{
    float rot, apothem, sector;
    bool polygon;
    Iris(int blades, float rot_deg)
        : rot(rot_deg * (float)M_PI / 180.0f),
          apothem(blades >= 3 ? std::cos((float)M_PI / blades) : 1.0f),
          sector(blades >= 3 ? 2.0f * (float)M_PI / blades : 1.0f),
          polygon(blades >= 3) {}
    // (u, v) in [-1, 1]: inside the iris?
    bool inside(float u, float v) const
    {
        const float r2 = u * u + v * v;
        if (r2 > 1.0f) return false;
        if (!polygon) return true;
        float s = std::fmod(std::atan2(v, u) - rot, sector);
        if (s < 0.0f) s += sector;
        return std::sqrt(r2) * std::cos(s - sector * 0.5f) <= apothem;
    }
};

void aperture_mask(std::vector<float>& mask, int n, int blades, float rot_deg)
{
    mask.assign((size_t)n * n, 0.0f);
    const Iris iris(blades, rot_deg);
    for (int r = 0; r < n; ++r)
        for (int c = 0; c < n; ++c)
            if (iris.inside(((c + 0.5f) / n) * 2.0f - 1.0f, ((r + 0.5f) / n) * 2.0f - 1.0f))
                mask[(size_t)r * n + c] = 1.0f;
}

struct LightPos { float x, y, r, g, b; };

LightPos light_pos(const BrightPixel& bp, const ExtrasFrame& f)
{
    const float cx = f.fmt_x0_in_buf + f.fmt_w * 0.5f;
    const float cy = f.fmt_y0_in_buf + f.fmt_h * 0.5f;
    LightPos p;
    p.x = cx + std::tan(bp.angle_x) / (2.0f * f.tan_half_h) * f.fmt_w;
    p.y = cy + std::tan(bp.angle_y) / (2.0f * f.tan_half_v) * f.fmt_h;
    p.r = bp.r * f.light_scale;
    p.g = bp.g * f.light_scale;
    p.b = bp.b * f.light_scale;
    return p;
}

} // namespace

// ---------------------------------------------------------------------------

void box_blur_inplace(float* buf, int w, int h, int radius, int passes,
                      std::vector<float>& tmp, bool zero_edges, int radius_y)
{
    const int rx = radius, ry = radius_y < 0 ? radius : radius_y;
    if ((rx < 1 && ry < 1) || passes < 1 || w <= 0 || h <= 0) return;
    const size_t npx = (size_t)w * h;
    tmp.resize(npx);
    std::vector<double> ps((size_t)std::max(w, h) + 1);
    for (int p = 0; p < passes; ++p) {
        for (int y = 0; y < h; ++y) {
            const float* row = buf + (size_t)y * w;
            float* out = tmp.data() + (size_t)y * w;
            ps[0] = 0.0;
            for (int x = 0; x < w; ++x) ps[x + 1] = ps[x] + row[x];
            for (int x = 0; x < w; ++x) {
                const int lo = std::max(0, x - rx), hi = std::min(w - 1, x + rx);
                out[x] = (float)((ps[hi + 1] - ps[lo]) / (zero_edges ? 2 * rx + 1 : hi - lo + 1));
            }
        }
        for (int x = 0; x < w; ++x) {
            ps[0] = 0.0;
            for (int y = 0; y < h; ++y) ps[y + 1] = ps[y] + tmp[(size_t)y * w + x];
            for (int y = 0; y < h; ++y) {
                const int lo = std::max(0, y - ry), hi = std::min(h - 1, y + ry);
                buf[(size_t)y * w + x] = (float)((ps[hi + 1] - ps[lo]) / (zero_edges ? 2 * ry + 1 : hi - lo + 1));
            }
        }
    }
}

float box_blur_peak(int radius, int passes)
{
    if (radius < 1 || passes < 1) return 1.0f;
    const int width = 2 * radius + 1;
    std::vector<double> k(1, 1.0);
    for (int p = 0; p < passes; ++p) {
        std::vector<double> next(k.size() + width - 1, 0.0);
        for (size_t i = 0; i < k.size(); ++i)
            for (int j = 0; j < width; ++j)
                next[i + j] += k[i] / width;
        k.swap(next);
    }
    return (float)*std::max_element(k.begin(), k.end());
}

void starburst_psf(int blades, float rotation_deg, int n, std::vector<float>& out)
{
    std::vector<float> mask;
    aperture_mask(mask, n, blades, rotation_deg);
    std::vector<std::complex<float>> grid((size_t)n * n);
    for (size_t i = 0; i < grid.size(); ++i) grid[i] = {mask[i], 0.0f};
    fft2d(grid, n);
    out.resize((size_t)n * n);
    float peak = 0.0f;
    for (int r = 0; r < n; ++r) {
        const int rs = (r + n / 2) % n;
        for (int c = 0; c < n; ++c) {
            const auto& z = grid[(size_t)rs * n + (c + n / 2) % n];
            const float v = z.real() * z.real() + z.imag() * z.imag();
            out[(size_t)r * n + c] = v;
            peak = std::max(peak, v);
        }
    }
    if (peak > 0.0f)
        for (float& v : out) v /= peak;
}

// ---------------------------------------------------------------------------

void FlareExtras::clear()
{
    haze_.clear();
    haze_core_[0].clear();
    haze_core_[1].clear();
    dirt_.clear();
    scatter_.clear();
    for (int c = 0; c < 3; ++c) dirt_img_[c].clear();
    dirt_w_ = dirt_h_ = 0;
    for (int c = 0; c < 3; ++c) star_[c].clear();
    star_w_ = star_h_ = 0;
    streaks_.clear();
}

void FlareExtras::compute(const ExtrasConfig& cfg, const ExtrasFrame& frame,
                          const std::vector<BrightPixel>& lights)
{
    clear();
    if (lights.empty() || frame.buf_w <= 0 || frame.buf_h <= 0) return;
    if (cfg.haze_gain > 0.0f) {
        // Haze Shape moves light from the main glow into glows a third and
        // a ninth as wide, energy shared 9 : 3 : 1 at Shape 1, so each
        // tighter glow is brighter at its centre: together they fall off
        // about as 1/distance from the light out to Haze Radius.
        const float s = std::clamp(cfg.haze_shape, 0.0f, 1.0f);
        const float share[3] = { 1.0f - s * 4.0f / 13.0f, s * 3.0f / 13.0f, s * 1.0f / 13.0f };
        const float radius[3] = { cfg.haze_radius, cfg.haze_radius / 3.0f, cfg.haze_radius / 9.0f };
        GlowField* field[3] = { &haze_, &haze_core_[0], &haze_core_[1] };
        for (int i = 0; i < 3; ++i) {
            if (share[i] <= 0.0f) continue;
            field[i]->compute(cfg.haze_gain * share[i], radius[i], cfg.haze_passes,
                              kRefRadius, kRefPeak, frame, lights, cfg.haze_aspect);
            field[i]->tint(cfg.haze_tint);
            if (i > 0) field[i]->tint(cfg.haze_core_tint);
        }
    }
    if (cfg.dirt_gain > 0.0f)
        dirt_.compute(cfg.dirt_gain, cfg.dirt_spread, 3,
                      kDirtRefRadius, kDirtRefPeak, frame, lights);
    if (cfg.starburst_gain > 0.0f) compute_starburst(cfg, frame, lights);
    if (cfg.rays_gain > 0.0f && cfg.rays_count > 0) compute_rays(cfg, frame, lights);
    if (cfg.ring_gain > 0.0f) compute_ring(cfg, frame, lights);
}

void GlowField::clear()
{
    for (int c = 0; c < 3; ++c) ch[c].clear();
    w = h = 0;  ds = 1;  pad = 0;
}

void GlowField::compute(float gain, float radius, int passes_in, float ref_radius,
                        float ref_peak, const ExtrasFrame& f,
                        const std::vector<BrightPixel>& lights, float aspect)
{
    clear();
    const float diag = f.diag > 0.0f ? f.diag
        : std::sqrt((float)f.buf_w * f.buf_w + (float)f.buf_h * f.buf_h);
    // Aspect stretches the glow across and squashes it up, keeping its area.
    const float stretch = std::sqrt(std::clamp(aspect, 0.05f, 20.0f));
    const float r_px    = std::max(radius, 0.0f) * diag;
    const int radius_x  = std::max(1, (int)std::lround(r_px * stretch));
    const int radius_y  = std::max(1, (int)std::lround(r_px / stretch));
    const int passes    = std::max(passes_in, 1);

    // A blur this wide doesn't need every pixel: work at a resolution where
    // the radius is still kHazeMinRadius pixels or more.
    const int lds = std::max(1, std::min(radius_x, radius_y) / kHazeMinRadius);
    const int lrx = std::max(1, (int)std::lround((float)radius_x / lds));
    const int lry = std::max(1, (int)std::lround((float)radius_y / lds));
    // The grid reaches past the buffer by the blur's reach, so lights just
    // outside it (Manual XY lights flare off screen) still add glow.
    const int lpad = std::max(lrx, lry) * passes;
    const int lw = (f.buf_w + lds - 1) / lds + 2 * lpad;
    const int lh = (f.buf_h + lds - 1) / lds + 2 * lpad;

    // Each light's light, splatted (bilinear) into the low-res grid.  A
    // cell holds the average over its lds x lds full-res pixels.
    const float area = std::max(f.light_size, 1.0f) * std::max(f.light_size, 1.0f);
    const float cell = 1.0f / ((float)lds * lds);
    for (int c = 0; c < 3; ++c) ch[c].assign((size_t)lw * lh, 0.0f);
    for (const BrightPixel& bp : lights) {
        const LightPos p = light_pos(bp, f);
        const float gx = p.x / lds - 0.5f + lpad, gy = p.y / lds - 0.5f + lpad;
        const int ix = (int)std::floor(gx), iy = (int)std::floor(gy);
        const float fx = gx - ix, fy = gy - iy;
        const float rgb[3] = { p.r, p.g, p.b };
        for (int dy = 0; dy < 2; ++dy) {
            const int y = iy + dy;
            if (y < 0 || y >= lh) continue;
            const float wy = dy ? fy : 1.0f - fy;
            for (int dx = 0; dx < 2; ++dx) {
                const int x = ix + dx;
                if (x < 0 || x >= lw) continue;
                const float wgt = (dx ? fx : 1.0f - fx) * wy * area * cell;
                for (int c = 0; c < 3; ++c)
                    ch[c][(size_t)y * lw + x] += rgb[c] * wgt;
            }
        }
    }

    std::vector<float> tmp;
    for (int c = 0; c < 3; ++c)
        box_blur_inplace(ch[c].data(), lw, lh, lrx, passes, tmp, /*zero_edges=*/true, lry);

    // Scale so that Gain 1 gives ref_peak for the reference light at the
    // reference radius (3 passes).  The peak of a blurred point of energy E
    // (in full-res pixels) is E * peak1d^2 at full resolution.
    const int ref_r = std::max(1, (int)std::lround(ref_radius * diag));
    const float ref1d = box_blur_peak(ref_r, 3);
    const float norm = gain * ref_peak / (kRefLightArea * ref1d * ref1d);
    for (int c = 0; c < 3; ++c)
        for (float& v : ch[c]) v *= norm;

    w = lw;  h = lh;  ds = lds;  pad = lpad;
}

void GlowField::tint(const float rgb[3])
{
    for (int c = 0; c < 3; ++c) {
        const float k = std::max(rgb[c], 0.0f);
        if (k != 1.0f)
            for (float& v : ch[c]) v *= k;
    }
}

void GlowField::add_row(int c, int y, int x0, int x1, float* dst) const
{
    if (empty() || y < 0 || c < 0 || c > 2) return;
    const std::vector<float>& g = ch[c];
    const float gy = (y + 0.5f) / ds - 0.5f + pad;
    int iy = (int)std::floor(gy);
    const float fy = gy - iy;
    const int ya = std::clamp(iy, 0, h - 1);
    const int yb = std::clamp(iy + 1, 0, h - 1);
    const float* ra = g.data() + (size_t)ya * w;
    const float* rb = g.data() + (size_t)yb * w;
    for (int x = x0; x < x1; ++x) {
        const float gx = (x + 0.5f) / ds - 0.5f + pad;
        const int ix = (int)std::floor(gx);
        const float fx = gx - ix;
        const int xa = std::clamp(ix, 0, w - 1);
        const int xb = std::clamp(ix + 1, 0, w - 1);
        const float top = ra[xa] * (1.0f - fx) + ra[xb] * fx;
        const float bot = rb[xa] * (1.0f - fx) + rb[xb] * fx;
        dst[x - x0] += top * (1.0f - fy) + bot * fy;
    }
}

void defocus_inplace(float* img, int w, int h, float radius, int blades, float rotation_deg)
{
    if (radius < 0.75f || w <= 0 || h <= 0) return;
    // A defocused speck is a soft blob, so the blob can be worked at a
    // lower resolution: keep its radius around kDefocusRadius low-res pixels.
    const int ds = std::max(1, (int)(radius / kDefocusRadius));
    const int lw = (w + ds - 1) / ds, lh = (h + ds - 1) / ds;
    std::vector<float> lo((size_t)lw * lh, 0.0f), cnt((size_t)lw * lh, 0.0f);
    for (int y = 0; y < h; ++y)
        for (int x = 0; x < w; ++x) {
            const size_t i = (size_t)(y / ds) * lw + x / ds;
            lo[i] += img[(size_t)y * w + x];
            cnt[i] += 1.0f;
        }
    for (size_t i = 0; i < lo.size(); ++i) lo[i] /= std::max(cnt[i], 1.0f);

    // The blob: the iris shape, supersampled 4 x 4 for soft edges.
    const float kr = std::max(radius / ds, 1.0f);
    const int   kn = (int)std::ceil(kr);
    const Iris iris(blades, rotation_deg);
    struct Tap { int dx, dy; float w; };
    std::vector<Tap> taps;
    float total = 0.0f;
    for (int dy = -kn; dy <= kn; ++dy)
        for (int dx = -kn; dx <= kn; ++dx) {
            int in = 0;
            for (int sy = 0; sy < 4; ++sy)
                for (int sx = 0; sx < 4; ++sx)
                    in += iris.inside((dx + (sx + 0.5f) / 4 - 0.5f) / kr,
                                      (dy + (sy + 0.5f) / 4 - 0.5f) / kr);
            if (in) { taps.push_back({dx, dy, in / 16.0f}); total += in / 16.0f; }
        }
    for (Tap& t : taps) t.w /= total;

    std::vector<float> out((size_t)lw * lh, 0.0f);
    for (int y = 0; y < lh; ++y)
        for (int x = 0; x < lw; ++x) {
            float acc = 0.0f;
            for (const Tap& t : taps) {
                const int sx = std::clamp(x + t.dx, 0, lw - 1);
                const int sy = std::clamp(y + t.dy, 0, lh - 1);
                acc += lo[(size_t)sy * lw + sx] * t.w;
            }
            out[(size_t)y * lw + x] = acc;
        }

    if (ds == 1) { std::copy(out.begin(), out.end(), img); return; }
    // Back up to full resolution, bilinear.
    for (int y = 0; y < h; ++y) {
        const float gy = (y + 0.5f) / ds - 0.5f;
        const int iy = (int)std::floor(gy);
        const float fy = gy - iy;
        const int ya = std::clamp(iy, 0, lh - 1), yb = std::clamp(iy + 1, 0, lh - 1);
        for (int x = 0; x < w; ++x) {
            const float gx = (x + 0.5f) / ds - 0.5f;
            const int ix = (int)std::floor(gx);
            const float fx = gx - ix;
            const int xa = std::clamp(ix, 0, lw - 1), xb = std::clamp(ix + 1, 0, lw - 1);
            const float top = out[(size_t)ya * lw + xa] * (1 - fx) + out[(size_t)ya * lw + xb] * fx;
            const float bot = out[(size_t)yb * lw + xa] * (1 - fx) + out[(size_t)yb * lw + xb] * fx;
            img[(size_t)y * w + x] = top * (1 - fy) + bot * fy;
        }
    }
}

void FlareExtras::set_dirt(const DirtLook& look, const ExtrasFrame& f,
                           std::vector<float> rgb[3])
{
    for (int c = 0; c < 3; ++c) dirt_img_[c].clear();
    scatter_.clear();
    const int w = f.buf_w, h = f.buf_h;
    const size_t npx = (size_t)w * h;
    if (w <= 0 || h <= 0) return;
    for (int c = 0; c < 3; ++c)
        if (rgb[c].size() != npx) return;
    const float diag = f.diag > 0.0f ? f.diag : std::sqrt((float)w * w + (float)h * h);

    for (int c = 0; c < 3; ++c) {
        dirt_img_[c].swap(rgb[c]);
        for (float& v : dirt_img_[c]) v = std::max(v, 0.0f);
        defocus_inplace(dirt_img_[c].data(), w, h, std::max(look.softness, 0.0f) * diag,
                        look.aperture_blades, look.aperture_rotation);
    }
    dirt_w_ = w;  dirt_h_ = h;

    // The veil: the light on the dirt, spread wide.  It carries
    // look.scatter of the dirt's light (the blur keeps the total), so
    // it's faint next to the specks but covers far more of the frame.
    if (look.scatter <= 0.0f || dirt_.empty()) return;
    const int radius_px = std::max(1, (int)std::lround(std::max(look.scatter_radius, 0.0f) * diag));
    const int ds = std::max(1, radius_px / kHazeMinRadius);
    const int lr = std::max(1, (int)std::lround((float)radius_px / ds));
    const int lw = (w + ds - 1) / ds, lh = (h + ds - 1) / ds;
    const float cell = 1.0f / ((float)ds * ds);
    std::vector<float> light(w), tmp;
    for (int c = 0; c < 3; ++c) {
        std::vector<float>& g = scatter_.ch[c];
        g.assign((size_t)lw * lh, 0.0f);
        for (int y = 0; y < h; ++y) {
            std::fill(light.begin(), light.end(), 0.0f);
            dirt_.add_row(c, y, 0, w, light.data());
            const float* d = dirt_img_[c].data() + (size_t)y * w;
            float* gr = g.data() + (size_t)(y / ds) * lw;
            for (int x = 0; x < w; ++x) gr[x / ds] += d[x] * light[x] * cell;
        }
        box_blur_inplace(g.data(), lw, lh, lr, 3, tmp, /*zero_edges=*/true);
        for (float& v : g) v *= look.scatter;
    }
    scatter_.w = lw;  scatter_.h = lh;  scatter_.ds = ds;  scatter_.pad = 0;
}

const float* FlareExtras::dirt_row(int c, int y) const
{
    if (!has_dirt_image() || c < 0 || c > 2 || y < 0 || y >= dirt_h_) return nullptr;
    return dirt_img_[c].data() + (size_t)y * dirt_w_;
}

void FlareExtras::compute_starburst(const ExtrasConfig& cfg, const ExtrasFrame& f,
                                    const std::vector<BrightPixel>& lights)
{
    const float softness = std::clamp(cfg.starburst_softness, 0.0f, 0.5f);
    const float reach    = std::clamp(cfg.starburst_reach, 0.0f, 3.0f);
    if (psf_.empty() || psf_blades_ != cfg.aperture_blades
            || psf_rotation_ != cfg.aperture_rotation || psf_softness_ != softness
            || psf_reach_ != reach) {
        starburst_psf(cfg.aperture_blades, cfg.aperture_rotation, kPsfSize, psf_);
        psf_n_ = kPsfSize;
        psf_blades_ = cfg.aperture_blades;
        psf_rotation_ = cfg.aperture_rotation;
        psf_softness_ = softness;
        psf_reach_ = reach;
        // Starburst Softness: blur the pattern (the spikes reach half its
        // width), keeping its light, so soft spikes are wider and fainter.
        const int r = (int)std::lround(softness * kPsfSize * 0.5f);
        if (r > 0) {
            std::vector<float> tmp;
            box_blur_inplace(psf_.data(), kPsfSize, kPsfSize, r, 3, tmp, /*zero_edges=*/true);
        }
        // Starburst Reach: brighten the pattern away from its core, as
        // distance^reach, so the spikes fade more slowly; it fades out
        // over the last third so the spikes don't end in a hard edge.
        if (reach > 0.0f) {
            const float half = kPsfSize * 0.5f;
            for (int j = 0; j < kPsfSize; ++j)
                for (int i = 0; i < kPsfSize; ++i) {
                    const float d = std::hypot(i - half, j - half);
                    const float t = d / half;
                    const float fade = t < 0.66f ? 1.0f
                        : std::max(0.0f, 1.0f - (t - 0.66f) / 0.34f);
                    const float boost = d > kPsfCore ? std::pow(d / kPsfCore, reach) : 1.0f;
                    psf_[(size_t)j * kPsfSize + i] *= boost * fade * fade * (3.0f - 2.0f * fade);
                }
        }
    }

    // Spikes cost a lot per light; the brightest lights get them.
    std::vector<LightPos> pos;
    pos.reserve(lights.size());
    for (const BrightPixel& bp : lights) pos.push_back(light_pos(bp, f));
    auto luma = [](const LightPos& p) { return 0.2126f * p.r + 0.7152f * p.g + 0.0722f * p.b; };
    const int keep = std::max(cfg.max_starbursts, 1);
    if ((int)pos.size() > keep) {
        std::partial_sort(pos.begin(), pos.begin() + keep, pos.end(),
                          [&](const LightPos& a, const LightPos& b) { return luma(a) > luma(b); });
        pos.resize(keep);
    }

    ensure_star_buffer(f);
    const int w = f.buf_w, h = f.buf_h;

    const float diag = f.diag > 0.0f ? f.diag : std::sqrt((float)w * w + (float)h * h);
    const float r_ref = std::max(cfg.starburst_scale, 1e-4f) * diag;
    const int   n = psf_n_;
    const float half_n = n * 0.5f;
    // Red spreads wider than blue (by wavelength), as much as Starburst
    // Fringe asks.
    const float fringe = std::max(cfg.starburst_fringe, 0.0f);
    const float scale_ch[3] = { 1.0f + fringe * (650.0f / 550.0f - 1.0f), 1.0f,
                                std::max(1.0f + fringe * (450.0f / 550.0f - 1.0f), 0.05f) };
    const float r_max_scale = std::max({ scale_ch[0], scale_ch[1], scale_ch[2] });
    const float* psf = psf_.data();

    for (const LightPos& p : pos) {
        const float r_max = r_ref * r_max_scale;
        const int ox0 = std::max(0, (int)std::floor(p.x - r_max));
        const int ox1 = std::min(w, (int)std::ceil(p.x + r_max) + 1);
        const int oy0 = std::max(0, (int)std::floor(p.y - r_max));
        const int oy1 = std::min(h, (int)std::ceil(p.y + r_max) + 1);
        if (ox0 >= ox1 || oy0 >= oy1) continue;
        const float rgb[3] = { p.r, p.g, p.b };
        for (int c = 0; c < 3; ++c) {
            const float v = rgb[c] * cfg.starburst_gain * kStarburstUnit
                          * std::max(cfg.starburst_tint[c], 0.0f);
            if (v <= 0.0f) continue;
            const float inv_r = half_n / (r_ref * scale_ch[c]);
            float* o = star_[c].data();
            for (int oy = oy0; oy < oy1; ++oy) {
                const float dj_f = (oy + 0.5f - p.y) * inv_r + half_n;
                if (dj_f < 0.0f || dj_f >= (float)(n - 1)) continue;
                const int dj = (int)dj_f;
                const float fj = dj_f - dj;
                const float* r0 = psf + (size_t)dj * n;
                const float* r1 = r0 + n;
                float* orow = o + (size_t)oy * w;
                for (int ox = ox0; ox < ox1; ++ox) {
                    const float di_f = (ox + 0.5f - p.x) * inv_r + half_n;
                    if (di_f < 0.0f || di_f >= (float)(n - 1)) continue;
                    const int di = (int)di_f;
                    const float fi = di_f - di;
                    const float s = (r0[di] * (1.0f - fi) + r0[di + 1] * fi) * (1.0f - fj)
                                  + (r1[di] * (1.0f - fi) + r1[di + 1] * fi) * fj;
                    orow[ox] += v * s;
                }
            }
        }
    }
}

void FlareExtras::ensure_star_buffer(const ExtrasFrame& f)
{
    const size_t npx = (size_t)f.buf_w * f.buf_h;
    if (star_w_ == f.buf_w && star_h_ == f.buf_h && star_[0].size() == npx) return;
    for (int c = 0; c < 3; ++c) star_[c].assign(npx, 0.0f);
    star_w_ = f.buf_w;  star_h_ = f.buf_h;
}

namespace {

// The brightest lights, as positions, at most `keep` of them.
std::vector<LightPos> brightest(const std::vector<BrightPixel>& lights, const ExtrasFrame& f,
                                int keep)
{
    std::vector<LightPos> pos;
    pos.reserve(lights.size());
    for (const BrightPixel& bp : lights) pos.push_back(light_pos(bp, f));
    auto luma = [](const LightPos& p) { return 0.2126f * p.r + 0.7152f * p.g + 0.0722f * p.b; };
    keep = std::max(keep, 1);
    if ((int)pos.size() > keep) {
        std::partial_sort(pos.begin(), pos.begin() + keep, pos.end(),
                          [&](const LightPos& a, const LightPos& b) { return luma(a) > luma(b); });
        pos.resize(keep);
    }
    return pos;
}

// Signed difference of two angles (radians), in (-pi, pi].
inline float angle_diff(float a, float b)
{
    float d = std::fmod(a - b, 2.0f * (float)M_PI);
    if (d > (float)M_PI) d -= 2.0f * (float)M_PI;
    if (d <= -(float)M_PI) d += 2.0f * (float)M_PI;
    return d;
}

// 0..1 inside an arc of `arc` degrees around `centre` (radians), fading
// over the last quarter of each half; 1 everywhere for a full circle.
inline float arc_mask(float angle, float centre, float arc_deg)
{
    if (arc_deg >= 360.0f) return 1.0f;
    const float half = std::max(arc_deg, 0.0f) * 0.5f * (float)M_PI / 180.0f;
    if (half <= 0.0f) return 0.0f;
    const float d = std::fabs(angle_diff(angle, centre));
    const float soft = half * 0.25f;
    if (d >= half) return 0.0f;
    if (d <= half - soft) return 1.0f;
    const float t = (half - d) / soft;
    return t * t * (3.0f - 2.0f * t);
}

uint32_t ray_hash(uint32_t s)
{
    s = (s ^ 61u) ^ (s >> 16u);
    s *= 9u;
    s ^= s >> 4u;
    s *= 0x27d4eb2du;
    s ^= s >> 15u;
    return s;
}

} // namespace

void FlareExtras::compute_rays(const ExtrasConfig& cfg, const ExtrasFrame& f,
                               const std::vector<BrightPixel>& lights)
{
    ensure_star_buffer(f);
    const int w = f.buf_w, h = f.buf_h;
    const float diag = f.diag > 0.0f ? f.diag : std::sqrt((float)w * w + (float)h * h);

    // The rays, the same at every light: angle, length, brightness and
    // width each drawn from the seed.
    struct Ray { float angle, length, value, width; };
    const int n = std::clamp(cfg.rays_count, 1, 1000);
    const float spread = std::clamp(cfg.rays_spread, 0.0f, 360.0f) * (float)M_PI / 180.0f;
    const float centre = cfg.rays_angle * (float)M_PI / 180.0f;
    const float len    = std::max(cfg.rays_length, 1e-4f) * diag;
    const float width  = std::max(cfg.rays_width, 0.01f) * (float)M_PI / 180.0f;
    std::vector<Ray> rays(n);
    float longest = 0.0f, widest = 0.0f;
    for (int k = 0; k < n; ++k) {
        auto rnd = [&](uint32_t salt) {
            return ray_hash((uint32_t)cfg.rays_seed * 7919u + (uint32_t)k * 4u + salt) / 4294967296.0f;
        };
        Ray& r = rays[k];
        r.angle  = centre + spread * (rnd(0) - 0.5f);
        r.length = len * (0.25f + 0.75f * rnd(1));
        r.value  = 0.25f + 0.75f * rnd(2);
        r.width  = width * (0.5f + rnd(3));
        longest  = std::max(longest, r.length);
        widest   = std::max(widest, r.width);
    }
    // Sorted by angle in (-pi, pi], so a pixel only looks at the rays near
    // its own angle.
    for (Ray& r : rays) r.angle = angle_diff(r.angle, 0.0f);
    std::sort(rays.begin(), rays.end(), [](const Ray& a, const Ray& b) { return a.angle < b.angle; });
    std::vector<float> angles(n);
    for (int k = 0; k < n; ++k) angles[k] = rays[k].angle;

    const float reach = longest * 5.0f;   // exp(-5): out of sight
    const float tint[3] = { std::max(cfg.rays_tint[0], 0.0f), std::max(cfg.rays_tint[1], 0.0f),
                            std::max(cfg.rays_tint[2], 0.0f) };
    std::vector<float> prof;
    for (const LightPos& p : brightest(lights, f, kMaxRayLights)) {
        const float rgb[3] = { p.r * tint[0], p.g * tint[1], p.b * tint[2] };
        if (std::max({ rgb[0], rgb[1], rgb[2] }) <= 0.0f) continue;
        const int ox0 = std::max(0, (int)std::floor(p.x - reach));
        const int ox1 = std::min(w, (int)std::ceil(p.x + reach) + 1);
        const int oy0 = std::max(0, (int)std::floor(p.y - reach));
        const int oy1 = std::min(h, (int)std::ceil(p.y + reach) + 1);
        for (int oy = oy0; oy < oy1; ++oy) {
            const float dy = oy + 0.5f - p.y;
            const size_t row = (size_t)oy * w;
            for (int ox = ox0; ox < ox1; ++ox) {
                const float dx = ox + 0.5f - p.x;
                const float d  = std::sqrt(dx * dx + dy * dy);
                if (d > reach) continue;
                const float phi = std::atan2(dy, dx);
                // A ray is a wedge of angular width r.width, at least a
                // pixel wide near the light.
                const float win = 4.0f * std::max(widest, 0.7f / std::max(d, 0.5f));
                float sum = 0.0f;
                auto visit = [&](float lo, float hi) {
                    const int a = (int)(std::lower_bound(angles.begin(), angles.end(), lo) - angles.begin());
                    for (int k = a; k < n && angles[k] <= hi; ++k) {
                        const Ray& r = rays[k];
                        const float da = angle_diff(phi, r.angle);
                        const float sig = std::max(r.width, 0.7f / std::max(d, 0.5f));
                        const float g = (da * da) / (2.0f * sig * sig);
                        if (g > 12.0f) continue;
                        sum += r.value * std::exp(-g - d / r.length);
                    }
                };
                const float lo = phi - win, hi = phi + win;
                visit(std::max(lo, -(float)M_PI), std::min(hi, (float)M_PI));
                if (lo < -(float)M_PI) visit(lo + 2.0f * (float)M_PI, (float)M_PI);
                if (hi > (float)M_PI)  visit(-(float)M_PI, hi - 2.0f * (float)M_PI);
                if (sum <= 0.0f) continue;
                for (int c = 0; c < 3; ++c)
                    star_[c][row + ox] += sum * rgb[c] * cfg.rays_gain * kRaysUnit;
            }
        }
    }
}

void FlareExtras::compute_ring(const ExtrasConfig& cfg, const ExtrasFrame& f,
                               const std::vector<BrightPixel>& lights)
{
    ensure_star_buffer(f);
    const int w = f.buf_w, h = f.buf_h;
    const float diag = f.diag > 0.0f ? f.diag : std::sqrt((float)w * w + (float)h * h);
    const float stretch = std::sqrt(std::clamp(cfg.ring_aspect, 0.05f, 20.0f));
    const float radius  = std::max(cfg.ring_radius, 1e-4f) * diag;
    const float rx = radius * stretch, ry = radius / stretch;
    const float width = std::max(cfg.ring_width, 0.002f);
    // Each colour's ring radius, by wavelength: red outside, blue inside.
    const float rainbow = std::max(cfg.ring_rainbow, 0.0f);
    const float rad_ch[3] = { 1.0f + rainbow * (650.0f / 550.0f - 1.0f), 1.0f,
                              std::max(1.0f + rainbow * (450.0f / 550.0f - 1.0f), 0.05f) };
    const float outer = rad_ch[0] + 4.0f * width;
    const float centre = cfg.ring_angle * (float)M_PI / 180.0f;
    // Ring Streaks: brightness round the ring, a table of thin streaks by
    // angle with a mean of 1, so the ring keeps its light.
    const float streaks = std::clamp(cfg.ring_streaks, 0.0f, 1.0f);
    constexpr int kStreakBins = 4096;
    std::vector<float> streak_tab;
    if (streaks > 0.0f) {
        streak_tab.assign(kStreakBins, 0.0f);
        const int n = std::clamp(cfg.ring_streak_count, 1, 2000);
        const float bin = 2.0f * (float)M_PI / kStreakBins;
        // At least a pixel wide on the ring.
        const float width0 = std::max(std::max(cfg.ring_streak_width, 0.01f) * (float)M_PI / 180.0f,
                                      0.7f / std::max(std::min(rx, ry), 1.0f));
        for (int k = 0; k < n; ++k) {
            auto rnd = [&](uint32_t salt) {
                return ray_hash((uint32_t)cfg.rays_seed * 6271u + (uint32_t)k * 4u + salt + 0x9e37u) / 4294967296.0f;
            };
            const float a = 2.0f * (float)M_PI * rnd(0);
            const float value = 0.25f + 0.75f * rnd(1);
            const float sig = width0 * (0.5f + rnd(2));
            const int reach = (int)std::ceil(4.0f * sig / bin);
            const int mid = (int)std::floor(a / bin);
            for (int j = mid - reach; j <= mid + reach; ++j) {
                const float da = (j + 0.5f) * bin - a;
                streak_tab[((j % kStreakBins) + kStreakBins) % kStreakBins] += value * std::exp(-0.5f * da * da / (sig * sig));
            }
        }
        // The mean over the drawn ring, weighted by how much ring each angle
        // holds (an oval's far ends hold more).
        double mean = 0.0, wsum = 0.0;
        for (int j = 0; j < kStreakBins; ++j) {
            const float a = (j + 0.5f) * bin;
            const float cu = std::cos(a) / rx, sv = std::sin(a) / ry;
            const float wt = arc_mask(std::atan2(sv, cu), centre, cfg.ring_arc) / (cu * cu + sv * sv);
            mean += wt * streak_tab[j]; wsum += wt;
        }
        mean /= std::max(wsum, 1e-12);
        for (float& v : streak_tab) v = (1.0f - streaks) + streaks * (float)(v / std::max(mean, 1e-12));
    }
    // Ring Jitter: each colour's ring radius moves by smooth noise round the
    // ring, one random value per cell (about one per streak), eased between.
    const float jitter = std::max(cfg.ring_jitter, 0.0f) * width;
    std::vector<float> jitter_tab[3];
    if (jitter > 0.0f) {
        const int cells = std::clamp(cfg.ring_streak_count, 8, 2000);
        for (int c = 0; c < 3; ++c) {
            std::vector<float> knot(cells);
            for (int k = 0; k < cells; ++k)
                knot[k] = 2.0f * ray_hash((uint32_t)cfg.rays_seed * 4813u + (uint32_t)(k * 3 + c) + 0x5bd1u)
                          / 4294967296.0f - 1.0f;
            jitter_tab[c].resize(kStreakBins);
            for (int j = 0; j < kStreakBins; ++j) {
                const float x = (j + 0.5f) / kStreakBins * cells;
                const int k0 = (int)std::floor(x);
                const float t = x - k0, e = t * t * (3.0f - 2.0f * t);
                jitter_tab[c][j] = jitter * (knot[k0 % cells] * (1.0f - e) + knot[(k0 + 1) % cells] * e);
            }
        }
    }
    const float outer_j = outer + 2.0f * jitter;
    for (const LightPos& p : brightest(lights, f, kMaxRayLights)) {
        const float rgb[3] = { p.r * std::max(cfg.ring_tint[0], 0.0f),
                               p.g * std::max(cfg.ring_tint[1], 0.0f),
                               p.b * std::max(cfg.ring_tint[2], 0.0f) };
        const int ox0 = std::max(0, (int)std::floor(p.x - rx * outer_j));
        const int ox1 = std::min(w, (int)std::ceil(p.x + rx * outer_j) + 1);
        const int oy0 = std::max(0, (int)std::floor(p.y - ry * outer_j));
        const int oy1 = std::min(h, (int)std::ceil(p.y + ry * outer_j) + 1);
        for (int oy = oy0; oy < oy1; ++oy) {
            const float v = (oy + 0.5f - p.y) / ry;
            const size_t row = (size_t)oy * w;
            for (int ox = ox0; ox < ox1; ++ox) {
                const float u = (ox + 0.5f - p.x) / rx;
                const float rho = std::sqrt(u * u + v * v);
                if (rho > outer_j) continue;
                const float phi = std::atan2(v, u);
                float m = arc_mask(phi, centre, cfg.ring_arc);
                if (m <= 0.0f) continue;
                int bin = 0;
                if (!streak_tab.empty() || jitter > 0.0f) {
                    // By the pixel's true angle from the light, so the streaks point at it.
                    float a = std::atan2(oy + 0.5f - p.y, ox + 0.5f - p.x);
                    if (a < 0.0f) a += 2.0f * (float)M_PI;
                    bin = std::min((int)(a / (2.0f * (float)M_PI) * kStreakBins), kStreakBins - 1);
                }
                if (!streak_tab.empty()) m *= streak_tab[bin];
                for (int c = 0; c < 3; ++c) {
                    const float rc = jitter > 0.0f ? rad_ch[c] + jitter_tab[c][bin] : rad_ch[c];
                    const float t = (rho - rc) / width;
                    if (t * t > 32.0f) continue;
                    star_[c][row + ox] += m * std::exp(-0.5f * t * t) * rgb[c]
                                        * cfg.ring_gain * kRingUnit;
                }
            }
        }
    }
}

void FlareExtras::set_ghost_streaks(std::vector<GhostStreak> streaks)
{
    streaks_.clear();
    for (GhostStreak& s : streaks)
        if (s.length > 0.0f && s.sigma > 0.0f && (s.peak[0] > 0.0f || s.peak[1] > 0.0f || s.peak[2] > 0.0f))
            streaks_.push_back(s);
}

void FlareExtras::add_ghost_streak_row(int c, int y, int x0, int x1, float* dst) const
{
    if (c < 0 || c > 2) return;
    const float py = y + 0.5f;
    for (const GhostStreak& s : streaks_) {
        const float dy = py - s.y[c];
        const float g = s.edge == 2.0f ? dy * dy / (2.0f * s.sigma * s.sigma)
                                       : 0.5f * std::pow(std::fabs(dy) / s.sigma, s.edge);
        if (g > 12.0f || s.peak[c] <= 0.0f) continue;
        const float v = s.peak[c] * std::exp(-g);
        if (s.edge != 2.0f) {
            // exp(-|dx / length|^(edge / 2)): flat along the middle with
            // sharp ends, out to where it is under exp(-12).
            const float q = 0.5f * s.edge;
            const float reach = s.length * std::pow(12.0f, 1.0f / q);
            const int a = std::max(x0, (int)std::floor(s.x[c] - reach));
            const int b = std::min(x1, (int)std::ceil(s.x[c] + reach));
            for (int x = a; x < b; ++x)
                dst[x - x0] += v * std::exp(-std::pow(std::fabs(x + 0.5f - s.x[c]) / s.length, q));
            continue;
        }
        // exp(-|dx| / length), out to 12 lengths, stepped by a constant
        // factor from the centre outwards.
        const float reach = 12.0f * s.length;
        const int a = std::max(x0, (int)std::floor(s.x[c] - reach));
        const int b = std::min(x1, (int)std::ceil(s.x[c] + reach));
        if (a >= b) continue;
        const int mid = std::clamp((int)std::floor(s.x[c]), a, b);
        const float step = std::exp(-1.0f / s.length);
        float k = v * std::exp(-std::fabs(mid + 0.5f - s.x[c]) / s.length);
        for (int x = mid; x < b; ++x, k *= step) dst[x - x0] += k;
        k = v * std::exp(-std::fabs(mid - 0.5f - s.x[c]) / s.length);
        for (int x = mid - 1; x >= a; --x, k *= step) dst[x - x0] += k;
    }
}

void FlareExtras::add_starburst_row(int c, int y, int x0, int x1, float* dst) const
{
    if (!has_starburst() || y < 0 || y >= star_h_ || c < 0 || c > 2) return;
    const float* row = star_[c].data() + (size_t)y * star_w_;
    const int a = std::max(x0, 0), b = std::min(x1, star_w_);
    for (int x = a; x < b; ++x) dst[x - x0] += row[x];
}

} // namespace flaresim
