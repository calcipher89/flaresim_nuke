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
// light, cheap next to the render.
constexpr int kGrid = 12;

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
    std::vector<std::pair<float, float>> pupil;
    for (int j = 0; j < kGrid; ++j)
        for (int i = 0; i < kGrid; ++i) {
            const float u = (i + 0.5f) / kGrid * 2.0f - 1.0f;
            const float v = (j + 0.5f) / kGrid * 2.0f - 1.0f;
            if (in_iris(u, v, cfg.aperture_blades, rot)) pupil.push_back({ u, v });
        }
    if (pupil.empty()) return out;
    const float ray_weight = 1.0f / pupil.size();
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
            bool any = false;
            for (int c = 0; c < 3; ++c) {
                double sum = 0.0, sx = 0.0, sy = 0.0;
                for (const auto& p : pupil) {
                    Ray ray;
                    ray.origin = Vec3f(p.first * front_R, p.second * front_R, start_z);
                    ray.dir = dir;
                    const TraceResult tr = trace_ghost_ray(ray, lens, g.pair.surf_a, g.pair.surf_b,
                                                           cfg.wavelengths[c]);
                    if (!tr.valid || !(tr.weight > 0.0f) || !std::isfinite(tr.position.x) ||
                        !std::isfinite(tr.position.y))
                        continue;
                    float px = (tr.position.x / (2.0f * f.sensor_half_w) + 0.5f) * f.fmt_w + f.fmt_x0_in_buf;
                    float py = (tr.position.y / (2.0f * f.sensor_half_h) + 0.5f) * f.fmt_h + f.fmt_y0_in_buf;
                    px = fcx + (px - fcx) * g.sx + g.ox;
                    py = fcy + (py - fcy) * g.sy + g.oy;
                    sum += tr.weight;
                    sx  += tr.weight * px;
                    sy  += tr.weight * py;
                }
                if (sum <= 0.0) { s.peak[c] = 0.0f; s.x[c] = s.y[c] = -1e7f; continue; }
                s.x[c] = (float)(sx / sum);
                s.y[c] = (float)(sy / sum);
                // The ghost's light in this channel, as the render adds it up.
                const float energy = (float)sum * ray_weight * cfg.gain * g.boost * g.col[c] * src[c];
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
