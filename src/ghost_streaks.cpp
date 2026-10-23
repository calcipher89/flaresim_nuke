// ============================================================================
// ghost_streaks.cpp — see ghost_streaks.h
// ============================================================================
#include "ghost_streaks.h"

#include "trace.h"

#include <algorithm>
#include <cmath>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

namespace flaresim {

namespace {

// Pupil samples per side for the trace: enough for a ghost's centre and
// light, cheap next to the render.  A ghost that only a corner of the
// pupil reaches gets few of them, so it is traced again on finer grids
// (a ghost through a thin sliver of the pupil can miss even the 40 grid).
constexpr int kGrids[] = { 12, 40, 128 };
constexpr int kMinHits = 8;

// The same round / polygonal iris as the ghost render's pupil grid.
bool in_iris(float u, float v, int blades, float rot)
{
    const float r2 = u * u + v * v;
    if (r2 > 1.0f) return false;
    if (blades < 3) return true;
    const float sector = 2.0f * (float)M_PI / blades;
    float s = std::fmod(std::atan2(v, u) - rot, sector);
    if (s < 0.0f) s += sector;
    return std::sqrt(r2) * std::cos(s - sector * 0.5f) <= std::cos((float)M_PI / blades);
}

// The render's wavelength samples and how much each adds to R, G and B
// (mirrors launch_ghost_cuda): 3 samples are the R, G, B wavelengths at
// full weight; more are spread over 400-700 nm with normalised CIE-like
// weights.  With Spectral Jitter the render moves each ray's wavelength
// within its sample's bin and re-weights it, so the expected weights are
// the bin's average.
struct SpectralSample { float lambda; float w[3]; };

float cie(int c, float l)
{
    if (c == 0) {
        const float a = (l - 600.0f) / 70.0f, b = (l - 450.0f) / 30.0f;
        return std::max(0.0f, 0.63f * std::exp(-0.5f * a * a) + 0.22f * std::exp(-0.5f * b * b));
    }
    const float a = c == 1 ? (l - 545.0f) / 55.0f : (l - 445.0f) / 45.0f;
    return std::max(0.0f, std::exp(-0.5f * a * a));
}

std::vector<SpectralSample> spectral_samples(const GhostConfig& cfg)
{
    const int ns = std::max(3, cfg.spectral_samples);
    std::vector<SpectralSample> out(ns);
    float sum[3] = { 0.0f, 0.0f, 0.0f };
    float bin;
    if (ns == 3) {
        for (int s = 0; s < 3; ++s) {
            out[s].lambda = cfg.wavelengths[s];
            for (int c = 0; c < 3; ++c) out[s].w[c] = s == c ? 1.0f : 0.0f;
        }
        for (int c = 0; c < 3; ++c) sum[c] = cie(c, 450.0f) + cie(c, 550.0f) + cie(c, 650.0f);
        bin = 100.0f;
    } else {
        for (int s = 0; s < ns; ++s) {
            out[s].lambda = 400.0f + 300.0f * s / (ns - 1);
            for (int c = 0; c < 3; ++c) { out[s].w[c] = cie(c, out[s].lambda); sum[c] += out[s].w[c]; }
        }
        for (auto& o : out)
            for (int c = 0; c < 3; ++c) o.w[c] = sum[c] > 1e-9f ? o.w[c] / sum[c] : 0.0f;
        bin = 300.0f / (ns - 1);
    }
    bin *= std::max(0.0f, cfg.spectral_jitter_scale);
    if (cfg.spectral_jitter && bin > 0.1f) {
        constexpr int kSub = 16;
        for (auto& o : out)
            for (int c = 0; c < 3; ++c) {
                float m = 0.0f;
                for (int k = 0; k < kSub; ++k) {
                    const float l = o.lambda + ((k + 0.5f) / kSub - 0.5f) * bin;
                    m += cie(c, std::max(380.0f, std::min(720.0f, l)));
                }
                o.w[c] = sum[c] > 1e-9f ? m / kSub / sum[c] : 0.0f;
            }
    }
    return out;
}

} // namespace

std::vector<GhostStreak> trace_ghost_streaks(const LensSystem& lens,
                                             const std::vector<StreakGhost>& ghosts,
                                             const std::vector<BrightPixel>& lights_in,
                                             const GhostConfig& cfg,
                                             const StreakFrame& f)
{
    std::vector<GhostStreak> out;
    if (ghosts.empty() || lights_in.empty() || lens.surfaces.empty() || f.fmt_w <= 0 || f.fmt_h <= 0)
        return out;

    std::vector<BrightPixel> lights(lights_in);
    auto luma = [](const BrightPixel& p) { return 0.2126f * p.r + 0.7152f * p.g + 0.0722f * p.b; };
    if ((int)lights.size() > kMaxStreakLights) {
        std::partial_sort(lights.begin(), lights.begin() + kMaxStreakLights, lights.end(),
                          [&](const BrightPixel& a, const BrightPixel& b) { return luma(a) > luma(b); });
        lights.resize(kMaxStreakLights);
    }

    // Pupil points, as the render samples the front of the lens.
    const float rot = cfg.aperture_rotation_deg * (float)M_PI / 180.0f;
    auto make_pupil = [&](int n) {
        std::vector<std::pair<float, float>> pts;
        for (int j = 0; j < n; ++j)
            for (int i = 0; i < n; ++i) {
                const float u = (i + 0.5f) / n * 2.0f - 1.0f;
                const float v = (j + 0.5f) / n * 2.0f - 1.0f;
                if (in_iris(u, v, cfg.aperture_blades, rot)) pts.push_back({ u, v });
            }
        return pts;
    };
    constexpr int n_grids = (int)(sizeof(kGrids) / sizeof(kGrids[0]));
    std::vector<std::pair<float, float>> pupils[n_grids];   // finer ones made when first needed
    pupils[0] = make_pupil(kGrids[0]);
    if (pupils[0].empty()) return out;
    const std::vector<SpectralSample> spectra = spectral_samples(cfg);
    const float front_R = lens.surfaces[0].semi_aperture;
    const float start_z = lens.surfaces[0].z - 20.0f;
    const float fcx = f.fmt_x0_in_buf + f.fmt_w * 0.5f;
    const float fcy = f.fmt_y0_in_buf + f.fmt_h * 0.5f;
    const float diag = f.diag > 0.0f ? f.diag
        : std::sqrt((float)f.fmt_w * f.fmt_w + (float)f.fmt_h * f.fmt_h);

    for (const BrightPixel& light : lights) {
        const float bx = std::tan(light.angle_x), by = std::tan(light.angle_y);
        const float inv = 1.0f / std::sqrt(bx * bx + by * by + 1.0f);
        const Vec3f dir(bx * inv, by * inv, inv);
        const float src[3] = { light.r, light.g, light.b };
        for (const StreakGhost& g : ghosts) {
            if (g.gain <= 0.0f) continue;
            GhostStreak s;
            s.length = std::max(g.length, 1e-4f) * diag;
            s.sigma  = std::max(g.thickness * diag, 0.5f);
            // A line exp(-|dx| / length) across and a Gaussian up holds
            // 2 length x sqrt(2 pi) sigma times its peak.
            const float spread = 2.0f * s.length * std::sqrt(2.0f * (float)M_PI) * s.sigma;
            // The ghost's light and centre in each channel, as the render
            // adds them up.
            double sum[3], sx[3], sy[3], ray_weight = 0.0;
            auto trace = [&](const std::vector<std::pair<float, float>>& pupil) {
                int hits = 0;
                for (int c = 0; c < 3; ++c) sum[c] = sx[c] = sy[c] = 0.0;
                for (const SpectralSample& sm : spectra) {
                    double ws = 0.0, wx = 0.0, wy = 0.0;
                    for (const auto& p : pupil) {
                        Ray ray;
                        ray.origin = Vec3f(p.first * front_R, p.second * front_R, start_z);
                        ray.dir = dir;
                        const TraceResult tr = trace_ghost_ray(ray, lens, g.pair.surf_a, g.pair.surf_b,
                                                               sm.lambda);
                        if (!tr.valid || !(tr.weight > 0.0f) || !std::isfinite(tr.position.x) ||
                            !std::isfinite(tr.position.y))
                            continue;
                        float px = (tr.position.x / (2.0f * f.sensor_half_w) + 0.5f) * f.fmt_w + f.fmt_x0_in_buf;
                        float py = (tr.position.y / (2.0f * f.sensor_half_h) + 0.5f) * f.fmt_h + f.fmt_y0_in_buf;
                        px = fcx + (px - fcx) * g.sx + g.ox;
                        py = fcy + (py - fcy) * g.sy + g.oy;
                        ws += tr.weight;
                        wx += tr.weight * px;
                        wy += tr.weight * py;
                        ++hits;
                    }
                    for (int c = 0; c < 3; ++c) {
                        sum[c] += sm.w[c] * ws;
                        sx[c]  += sm.w[c] * wx;
                        sy[c]  += sm.w[c] * wy;
                    }
                }
                ray_weight = 1.0 / (double)pupil.size();
                return hits;
            };
            for (int gi = 0; trace(pupils[gi]) < kMinHits * (int)spectra.size() / 3 && gi + 1 < n_grids; ) {
                ++gi;
                if (pupils[gi].empty()) pupils[gi] = make_pupil(kGrids[gi]);
            }
            bool any = false;
            for (int c = 0; c < 3; ++c) {
                if (sum[c] <= 0.0) { s.peak[c] = 0.0f; s.x[c] = s.y[c] = -1e7f; continue; }
                s.x[c] = (float)(sx[c] / sum[c]);
                s.y[c] = (float)(sy[c] / sum[c]);
                const float energy = (float)(sum[c] * ray_weight) * cfg.gain * g.boost * g.col[c] * src[c];
                s.peak[c] = g.gain * std::max(g.tint[c], 0.0f) * energy / spread;
                any = any || s.peak[c] > 0.0f;
            }
            // A channel that missed takes another's centre (its peak is 0).
            for (int c = 0; c < 3; ++c)
                if (s.peak[c] <= 0.0f)
                    for (int k = 0; k < 3; ++k)
                        if (s.peak[k] > 0.0f) { s.x[c] = s.x[k]; s.y[c] = s.y[k]; break; }
            if (any) out.push_back(s);
        }
    }
    return out;
}

} // namespace flaresim
