// node_ui.h — shared node settings for FlareSim and FlareSim3D:
// Quality presets (instead of a raw Ray Grid), the output bbox (Clip To)
// and the Show Advanced knob list.

#pragma once

#include "DDImage/Box.h"
#include "DDImage/Format.h"
#include "DDImage/Knob.h"
#include "DDImage/Knobs.h"

#include <algorithm>
#include <cmath>
#include <vector>

namespace flaresim {

// ---- Quality ----
// The ray grid follows the format width, so a preset looks the same on an
// HD and a 4K plate.  Custom uses the Ray Grid knob as typed.
enum Quality { kQualityLow = 0, kQualityMedium, kQualityHigh, kQualityUltra, kQualityCustom };

static const char* const kQualityNames[] = {
    "Low", "Medium", "High", "Ultra", "Custom", nullptr
};

// Ray Grid's default.  A script saved before Quality existed with any other
// Ray Grid keeps it (see effective_ray_grid).
static const int kDefaultRayGrid = 64;

// Grid size for a preset: about 128 / 256 / 512 / 1024 on a 1920 plate.
inline int quality_ray_grid(int quality, int fmt_w)
{
    static const float kPerPixel[] = { 1.0f / 15.0f, 2.0f / 15.0f, 4.0f / 15.0f, 8.0f / 15.0f };
    const int q = std::min(std::max(quality, 0), (int)kQualityUltra);
    const int w = fmt_w > 0 ? fmt_w : 1920;
    const int n = (int)std::lround(w * kPerPixel[q] / 8.0f) * 8;
    return std::min(std::max(n, 32), 2048);
}

// The ray grid to render with.  A preset is used unless Ray Grid was
// changed from its default, which only Custom (or a script saved before
// Quality existed) does.
inline int effective_ray_grid(int quality, int ray_grid, int fmt_w)
{
    if (quality == kQualityCustom || ray_grid != kDefaultRayGrid)
        return std::max(ray_grid, 1);
    return quality_ray_grid(quality, fmt_w);
}

// ---- Clip To ----
enum ClipTo { kClipBBox = 0, kClipFormat, kClipOverscan };

static const char* const kClipNames[] = {
    "BBox", "Format", "Format + Overscan", nullptr
};

// Output bbox for a Clip To mode.  `box` comes in as the input's bbox.
inline void clip_output_box(DD::Image::Box& box, const DD::Image::Format& fmt,
                            int mode, int over_w, int over_h)
{
    if (mode == kClipFormat)
        box.set(fmt.x(), fmt.y(), fmt.r(), fmt.t());
    else if (mode == kClipOverscan)
        box.set(fmt.x() - std::max(over_w, 0), fmt.y() - std::max(over_h, 0),
                fmt.r() + std::max(over_w, 0), fmt.t() + std::max(over_h, 0));
}

// ---- Show Advanced ----
// Knobs hidden until Show Advanced is ticked.  Filled while the knobs are
// made; hidden knobs still work, so looks and the Lens Browser drive them.
struct AdvancedKnobs
{
    std::vector<DD::Image::Knob*> knobs;

    // Wrap a knob-making call: adv_.add(f, Int_knob(f, ...)).  knobs() runs
    // many times; each knob is kept once.
    void add(DD::Image::Knob_Callback, DD::Image::Knob* k)
    {
        if (k && std::find(knobs.begin(), knobs.end(), k) == knobs.end())
            knobs.push_back(k);
    }

    void show(bool on) const
    {
        for (DD::Image::Knob* k : knobs)
            k->visible(on);
    }
};

} // namespace flaresim
