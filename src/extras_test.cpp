// extras_test.cpp — checks for haze and starburst (extras.cpp).
// Optional argument: a folder to write haze.ppm / starburst.ppm into.
#include "extras.h"

#include <cmath>
#include <cstdio>
#include <string>
#include <vector>

using namespace flaresim;

static int failures = 0;
static void check(bool ok, const char* what, double got)
{
    std::printf("%s %s (%g)\n", ok ? "ok  " : "FAIL", what, got);
    if (!ok) ++failures;
}

static void write_ppm(const std::string& path, const FlareExtras& fx, int w, int h, bool haze)
{
    FILE* f = std::fopen(path.c_str(), "wb");
    if (!f) return;
    std::fprintf(f, "P6\n%d %d\n255\n", w, h);
    std::vector<float> row[3];
    for (int y = h - 1; y >= 0; --y) {
        for (int c = 0; c < 3; ++c) {
            row[c].assign(w, 0.0f);
            if (haze) fx.add_haze_row(c, y, 0, w, row[c].data());
            else      fx.add_starburst_row(c, y, 0, w, row[c].data());
        }
        for (int x = 0; x < w; ++x)
            for (int c = 0; c < 3; ++c) {
                const float v = std::pow(std::min(std::max(row[c][x] * 4.0f, 0.0f), 1.0f), 1.0f / 2.2f);
                std::fputc((int)(v * 255.0f + 0.5f), f);
            }
    }
    std::fclose(f);
}

int main(int argc, char** argv)
{
    const int w = 960, h = 540;
    ExtrasFrame fr;
    fr.buf_w = fr.fmt_w = w;
    fr.buf_h = fr.fmt_h = h;
    fr.tan_half_h = std::tan(0.5f);
    fr.tan_half_v = fr.tan_half_h * h / w;
    fr.light_size = 8.0f;
    fr.light_scale = 1.0f;

    // One white light at the centre of the frame.
    BrightPixel bp{0.0f, 0.0f, 1.0f, 1.0f, 1.0f};
    std::vector<BrightPixel> lights{bp};

    ExtrasConfig cfg;
    cfg.haze_gain = 1.0f;
    cfg.haze_radius = 0.1f;
    FlareExtras fx;
    fx.compute(cfg, fr, lights);
    check(fx.has_haze() && !fx.has_starburst(), "haze only when starburst gain is 0", 0);

    std::vector<float> row(w, 0.0f);
    float peak = 0.0f;
    cfg.haze_radius = 0.15f;
    fx.compute(cfg, fr, lights);
    row.assign(w, 0.0f);
    fx.add_haze_row(1, h / 2, 0, w, row.data());
    peak = 0.0f;
    for (float v : row) peak = std::max(peak, v);
    check(std::fabs(peak - 0.1f) < 0.015f, "gain 1 at the default radius peaks at a tenth", peak);

    // Twice the radius: about a quarter of the peak (same energy, 4x area).
    cfg.haze_radius = 0.3f;
    fx.compute(cfg, fr, lights);
    row.assign(w, 0.0f);
    fx.add_haze_row(1, h / 2, 0, w, row.data());
    float peak2 = 0.0f;
    for (float v : row) peak2 = std::max(peak2, v);
    check(peak2 > 0.18f * peak && peak2 < 0.32f * peak, "wider haze is fainter", peak2 / peak);

    // Energy spreads: far from the light there is still haze, but less.
    check(row[w / 2 + 100] > 0.05f * peak2 && row[w / 2 + 100] < peak2,
          "haze falls off with distance", row[w / 2 + 100]);
    if (argc > 1) write_ppm(std::string(argv[1]) + "/haze.ppm", fx, w, h, true);

    // Starburst with a six-blade iris.
    cfg.haze_gain = 0.0f;
    cfg.starburst_gain = 1.0f;
    cfg.starburst_scale = 0.2f;
    cfg.aperture_blades = 6;
    fx.compute(cfg, fr, lights);
    check(!fx.has_haze() && fx.has_starburst(), "starburst only when haze gain is 0", 0);
    row.assign(w, 0.0f);
    fx.add_starburst_row(1, h / 2, 0, w, row.data());
    float sb_peak = 0.0f;
    for (float v : row) sb_peak = std::max(sb_peak, v);
    check(sb_peak > 0.1f && sb_peak < 1.2f, "starburst centre is bright", sb_peak);

    // Spikes: along a spike (horizontal for a flat-sided hexagon at 0 deg,
    // whose edges are vertical at the sides) there's more light than
    // between spikes at the same distance.
    std::vector<float> psf;
    starburst_psf(6, 0.0f, 256, psf);
    const int n = 256, cc = n / 2, d = 40;
    float best = 0.0f, worst = 1e9f;
    for (int a = 0; a < 360; a += 5) {
        const float t = a * 3.14159265f / 180.0f;
        const int x = cc + (int)std::lround(d * std::cos(t));
        const int y = cc + (int)std::lround(d * std::sin(t));
        const float v = psf[(size_t)y * n + x];
        best = std::max(best, v);
        worst = std::min(worst, v);
    }
    check(best > 20.0f * worst, "six blades give spikes", best / std::max(worst, 1e-12f));
    if (argc > 1) write_ppm(std::string(argv[1]) + "/starburst.ppm", fx, w, h, false);

    // Nothing to draw without lights.
    fx.compute(cfg, fr, {});
    check(fx.empty(), "no lights, nothing drawn", 0);

    std::printf("%s\n", failures ? "FAILED" : "all passed");
    return failures ? 1 : 0;
}
