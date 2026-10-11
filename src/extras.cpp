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
constexpr int   kPsfSize      = 512;
constexpr int   kHazeMinRadius = 12;     // low-res blur radius the haze keeps

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
void aperture_mask(std::vector<float>& mask, int n, int blades, float rot_deg)
{
    mask.assign((size_t)n * n, 0.0f);
    const float rot     = rot_deg * (float)M_PI / 180.0f;
    const bool  polygon = blades >= 3;
    const float apothem = polygon ? std::cos((float)M_PI / blades) : 1.0f;
    const float sector  = polygon ? 2.0f * (float)M_PI / blades : 1.0f;
    for (int r = 0; r < n; ++r) {
        for (int c = 0; c < n; ++c) {
            const float u  = ((c + 0.5f) / n) * 2.0f - 1.0f;
            const float v  = ((r + 0.5f) / n) * 2.0f - 1.0f;
            const float r2 = u * u + v * v;
            if (r2 > 1.0f) continue;
            if (polygon) {
                float s = std::fmod(std::atan2(v, u) - rot, sector);
                if (s < 0.0f) s += sector;
                if (std::sqrt(r2) * std::cos(s - sector * 0.5f) > apothem) continue;
            }
            mask[(size_t)r * n + c] = 1.0f;
        }
    }
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
                      std::vector<float>& tmp, bool zero_edges)
{
    if (radius < 1 || passes < 1 || w <= 0 || h <= 0) return;
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
                const int lo = std::max(0, x - radius), hi = std::min(w - 1, x + radius);
                out[x] = (float)((ps[hi + 1] - ps[lo]) / (zero_edges ? 2 * radius + 1 : hi - lo + 1));
            }
        }
        for (int x = 0; x < w; ++x) {
            ps[0] = 0.0;
            for (int y = 0; y < h; ++y) ps[y + 1] = ps[y] + tmp[(size_t)y * w + x];
            for (int y = 0; y < h; ++y) {
                const int lo = std::max(0, y - radius), hi = std::min(h - 1, y + radius);
                buf[(size_t)y * w + x] = (float)((ps[hi + 1] - ps[lo]) / (zero_edges ? 2 * radius + 1 : hi - lo + 1));
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
    for (int c = 0; c < 3; ++c) {
        haze_[c].clear();
        star_[c].clear();
    }
    haze_w_ = haze_h_ = 0;
    star_w_ = star_h_ = 0;
}

void FlareExtras::compute(const ExtrasConfig& cfg, const ExtrasFrame& frame,
                          const std::vector<BrightPixel>& lights)
{
    clear();
    if (lights.empty() || frame.buf_w <= 0 || frame.buf_h <= 0) return;
    if (cfg.haze_gain > 0.0f)      compute_haze(cfg, frame, lights);
    if (cfg.starburst_gain > 0.0f) compute_starburst(cfg, frame, lights);
}

void FlareExtras::compute_haze(const ExtrasConfig& cfg, const ExtrasFrame& f,
                               const std::vector<BrightPixel>& lights)
{
    const float diag = f.diag > 0.0f ? f.diag
        : std::sqrt((float)f.buf_w * f.buf_w + (float)f.buf_h * f.buf_h);
    const int radius_px = std::max(1, (int)std::lround(std::max(cfg.haze_radius, 0.0f) * diag));
    const int passes    = std::max(cfg.haze_passes, 1);

    // A blur this wide doesn't need every pixel: work at a resolution where
    // the radius is still kHazeMinRadius pixels or more.
    const int ds = std::max(1, radius_px / kHazeMinRadius);
    const int lr = std::max(1, (int)std::lround((float)radius_px / ds));
    // The grid reaches past the buffer by the blur's reach, so lights just
    // outside it (Manual XY lights flare off screen) still add haze.
    const int pad = lr * passes;
    const int lw = (f.buf_w + ds - 1) / ds + 2 * pad;
    const int lh = (f.buf_h + ds - 1) / ds + 2 * pad;

    // Each light's light, splatted (bilinear) into the low-res grid.  A
    // cell holds the average over its ds x ds full-res pixels.
    const float area = std::max(f.light_size, 1.0f) * std::max(f.light_size, 1.0f);
    const float cell = 1.0f / ((float)ds * ds);
    for (int c = 0; c < 3; ++c) haze_[c].assign((size_t)lw * lh, 0.0f);
    for (const BrightPixel& bp : lights) {
        const LightPos p = light_pos(bp, f);
        const float gx = p.x / ds - 0.5f + pad, gy = p.y / ds - 0.5f + pad;
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
                    haze_[c][(size_t)y * lw + x] += rgb[c] * wgt;
            }
        }
    }

    std::vector<float> tmp;
    for (int c = 0; c < 3; ++c)
        box_blur_inplace(haze_[c].data(), lw, lh, lr, passes, tmp, /*zero_edges=*/true);

    // Scale so that Gain 1 matches kRefPeak for the reference light at the
    // reference radius (3 passes).  The peak of a blurred point of energy E
    // (in full-res pixels) is E * peak1d^2 at full resolution.
    const int ref_r = std::max(1, (int)std::lround(kRefRadius * diag));
    const float ref1d = box_blur_peak(ref_r, 3);
    const float norm = cfg.haze_gain * kRefPeak / (kRefLightArea * ref1d * ref1d);
    for (int c = 0; c < 3; ++c)
        for (float& v : haze_[c]) v *= norm;

    haze_w_ = lw;  haze_h_ = lh;  haze_ds_ = ds;  haze_pad_ = pad;
}

void FlareExtras::compute_starburst(const ExtrasConfig& cfg, const ExtrasFrame& f,
                                    const std::vector<BrightPixel>& lights)
{
    if (psf_.empty() || psf_blades_ != cfg.aperture_blades
            || psf_rotation_ != cfg.aperture_rotation) {
        starburst_psf(cfg.aperture_blades, cfg.aperture_rotation, kPsfSize, psf_);
        psf_n_ = kPsfSize;
        psf_blades_ = cfg.aperture_blades;
        psf_rotation_ = cfg.aperture_rotation;
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

    const int w = f.buf_w, h = f.buf_h;
    for (int c = 0; c < 3; ++c) star_[c].assign((size_t)w * h, 0.0f);

    const float diag = f.diag > 0.0f ? f.diag : std::sqrt((float)w * w + (float)h * h);
    const float r_ref = std::max(cfg.starburst_scale, 1e-4f) * diag;
    const int   n = psf_n_;
    const float half_n = n * 0.5f;
    const float scale_ch[3] = { 650.0f / 550.0f, 1.0f, 450.0f / 550.0f };
    const float* psf = psf_.data();

    for (const LightPos& p : pos) {
        const float r_max = r_ref * scale_ch[0];
        const int ox0 = std::max(0, (int)std::floor(p.x - r_max));
        const int ox1 = std::min(w, (int)std::ceil(p.x + r_max) + 1);
        const int oy0 = std::max(0, (int)std::floor(p.y - r_max));
        const int oy1 = std::min(h, (int)std::ceil(p.y + r_max) + 1);
        if (ox0 >= ox1 || oy0 >= oy1) continue;
        const float rgb[3] = { p.r, p.g, p.b };
        for (int c = 0; c < 3; ++c) {
            const float v = rgb[c] * cfg.starburst_gain;
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
    star_w_ = w;  star_h_ = h;
}

void FlareExtras::add_haze_row(int c, int y, int x0, int x1, float* dst) const
{
    if (!has_haze() || y < 0 || c < 0 || c > 2) return;
    const std::vector<float>& g = haze_[c];
    const float gy = (y + 0.5f) / haze_ds_ - 0.5f + haze_pad_;
    int iy = (int)std::floor(gy);
    const float fy = gy - iy;
    const int ya = std::clamp(iy, 0, haze_h_ - 1);
    const int yb = std::clamp(iy + 1, 0, haze_h_ - 1);
    const float* ra = g.data() + (size_t)ya * haze_w_;
    const float* rb = g.data() + (size_t)yb * haze_w_;
    for (int x = x0; x < x1; ++x) {
        const float gx = (x + 0.5f) / haze_ds_ - 0.5f + haze_pad_;
        const int ix = (int)std::floor(gx);
        const float fx = gx - ix;
        const int xa = std::clamp(ix, 0, haze_w_ - 1);
        const int xb = std::clamp(ix + 1, 0, haze_w_ - 1);
        const float top = ra[xa] * (1.0f - fx) + ra[xb] * fx;
        const float bot = rb[xa] * (1.0f - fx) + rb[xb] * fx;
        dst[x - x0] += top * (1.0f - fy) + bot * fy;
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
