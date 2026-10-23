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

// Sum of channel c over the whole frame.
static double total(const FlareExtras& fx, int c, int w, int h, bool haze)
{
    std::vector<float> row;
    double t = 0.0;
    for (int y = 0; y < h; ++y) {
        row.assign(w, 0.0f);
        if (haze) fx.add_haze_row(c, y, 0, w, row.data());
        else      fx.add_starburst_row(c, y, 0, w, row.data());
        for (float v : row) t += v;
    }
    return t;
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

    // Haze Shape: the same total light, gathered closer to the light, with
    // a long faint tail.
    {
        cfg.haze_radius = 0.15f;
        fx.compute(cfg, fr, lights);
        const double soft_total = total(fx, 1, w, h, true);
        row.assign(w, 0.0f);
        fx.add_haze_row(1, h / 2, 0, w, row.data());
        const float soft_peak = row[w / 2], soft_far = row[w / 2 + 250];
        cfg.haze_shape = 1.0f;
        fx.compute(cfg, fr, lights);
        const double shaped_total = total(fx, 1, w, h, true);
        row.assign(w, 0.0f);
        fx.add_haze_row(1, h / 2, 0, w, row.data());
        // (A little more stays in frame: the tighter glows lose less off
        // the frame edges.)
        check(shaped_total / soft_total > 0.97 && shaped_total / soft_total < 1.08,
              "Haze Shape keeps about the same total",
              shaped_total / soft_total);
        check(row[w / 2] > 3.0f * soft_peak, "Haze Shape is brighter near the light",
              row[w / 2] / soft_peak);
        check(row[w / 2 + 250] > 0.0f && row[w / 2 + 250] < soft_far,
              "Haze Shape keeps a fainter tail", row[w / 2 + 250] / soft_far);
        cfg.haze_shape = 0.0f;

        // Haze Tint multiplies each channel.
        const float tint[3] = { 1.0f, 0.5f, 0.0f };
        for (int c = 0; c < 3; ++c) cfg.haze_tint[c] = tint[c];
        fx.compute(cfg, fr, lights);
        const double r = total(fx, 0, w, h, true), g = total(fx, 1, w, h, true),
                     b = total(fx, 2, w, h, true);
        check(std::fabs(r / soft_total - 1.0) < 1e-3 && std::fabs(g / soft_total - 0.5) < 1e-3 && b == 0.0,
              "Haze Tint colours the haze", g / soft_total);
        for (int c = 0; c < 3; ++c) cfg.haze_tint[c] = 1.0f;
    }

    // Starburst with a six-blade iris.
    cfg.haze_gain = 0.0f;
    cfg.starburst_gain = 8.0f;   // x 1/8: the light at its own brightness
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

    // Starburst Fringe 0: every colour the same size, so a white light
    // gives white spikes.  At 1, red spreads wider than blue.
    {
        const double r1 = total(fx, 0, w, h, false), b1 = total(fx, 2, w, h, false);
        row.assign(w, 0.0f);
        fx.add_starburst_row(1, h / 2, 0, w, row.data());
        const float crisp_peak = row[w / 2];
        std::vector<float> rr(w, 0.0f), bb(w, 0.0f);
        fx.add_starburst_row(0, h / 2, 0, w, rr.data());
        fx.add_starburst_row(2, h / 2, 0, w, bb.data());
        const int far_x = w / 2 + 60;
        check(rr[far_x] > bb[far_x], "Fringe 1: red reaches further than blue",
              rr[far_x] / std::max(bb[far_x], 1e-12f));
        cfg.starburst_fringe = 0.0f;
        fx.compute(cfg, fr, lights);
        rr.assign(w, 0.0f); bb.assign(w, 0.0f);
        fx.add_starburst_row(0, h / 2, 0, w, rr.data());
        fx.add_starburst_row(2, h / 2, 0, w, bb.data());
        float diff = 0.0f;
        for (int x = 0; x < w; ++x) diff = std::max(diff, std::fabs(rr[x] - bb[x]));
        check(diff < 1e-6f, "Fringe 0: no colour fringes", diff);
        cfg.starburst_fringe = 1.0f;

        // Softness blurs the spikes: a lower peak, about the same total.
        cfg.starburst_softness = 0.01f;
        fx.compute(cfg, fr, lights);
        row.assign(w, 0.0f);
        fx.add_starburst_row(1, h / 2, 0, w, row.data());
        check(row[w / 2] < 0.8f * crisp_peak, "Softness lowers the peak", row[w / 2] / crisp_peak);
        const double soft_r = total(fx, 0, w, h, false);
        check(soft_r / r1 > 0.9 && soft_r / r1 < 1.1, "Softness keeps about the same total", soft_r / r1);
        cfg.starburst_softness = 0.0f;

        // Tint multiplies each channel.
        cfg.starburst_tint[2] = 0.0f;
        cfg.starburst_tint[0] = 0.5f;
        fx.compute(cfg, fr, lights);
        const double r2 = total(fx, 0, w, h, false), b2 = total(fx, 2, w, h, false);
        check(std::fabs(r2 / r1 - 0.5) < 1e-3 && b2 == 0.0 && b1 > 0.0, "Starburst Tint colours the spikes",
              r2 / r1);
        for (int c = 0; c < 3; ++c) cfg.starburst_tint[c] = 1.0f;
    }

    // Dirt light: wide, reaches the far side of the frame, peaks at
    // kDirtRefPeak (0.25) at Gain 1 and the default spread.
    cfg.starburst_gain = 0.0f;
    cfg.dirt_gain = 1.0f;
    fx.compute(cfg, fr, lights);
    check(fx.has_dirt() && !fx.has_starburst(), "dirt light only", 0);
    row.assign(w, 0.0f);
    fx.add_dirt_light_row(1, h / 2, 0, w, row.data());
    float dpeak = 0.0f;
    for (float v : row) dpeak = std::max(dpeak, v);
    check(std::fabs(dpeak - 0.25f) < 0.04f, "dirt light peaks at a quarter", dpeak);
    check(row[w / 2 + 300] > 0.2f * dpeak, "dirt light reaches far", row[w / 2 + 300]);

    // Dirt: one speck, defocused into a blob; the blob keeps its total and
    // is round-ish, wider than the speck.
    {
        std::vector<float> img[3];
        for (int c = 0; c < 3; ++c) {
            img[c].assign((size_t)w * h, 0.0f);
            img[c][(size_t)(h / 2) * w + w / 2 + 100] = 1.0f;
        }
        DirtLook look;
        look.softness = 0.01f;   // ~11 px at this size
        look.scatter = 0.5f;
        fx.set_dirt(look, fr, img);
        check(fx.has_dirt_image() && fx.has_scatter(), "dirt image and scatter", 0);
        double total = 0.0;
        int lit = 0;
        for (int y = 0; y < h; ++y) {
            const float* d = fx.dirt_row(1, y);
            for (int x = 0; x < w; ++x) { total += d[x]; lit += d[x] > 1e-4f; }
        }
        check(std::fabs(total - 1.0) < 0.05, "defocus keeps the speck's total", (float)total);
        check(lit > 200, "defocus spreads the speck into a blob", (float)lit);
        std::vector<float> sc(w, 0.0f);
        fx.add_scatter_row(1, h / 2, 0, w, sc.data());
        const float near = sc[w / 2 + 100], far = sc[w / 2 + 100 + 60];
        check(near > 0.0f && far > 0.1f * near && far < near, "scatter is a wide soft veil", far / std::max(near, 1e-12f));
        std::vector<float> light(w, 0.0f);
        fx.add_dirt_light_row(1, h / 2, 0, w, light.data());
        const float speck = fx.dirt_row(1, h / 2)[w / 2 + 100] * light[w / 2 + 100];
        check(near < speck, "veil is fainter than the lit speck", near / std::max(speck, 1e-12f));
    }
    cfg.dirt_gain = 0.0f;

    // Nothing to draw without lights.
    fx.compute(cfg, fr, {});
    check(fx.empty(), "no lights, nothing drawn", 0);

    std::printf("%s\n", failures ? "FAILED" : "all passed");
    return failures ? 1 : 0;
}
