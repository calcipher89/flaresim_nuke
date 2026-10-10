// occlusion.h — occlusion matte for FlareSim and FlareSim3D.
//
// A light behind a foreground object should not flare.  The matte input
// says where the foreground is; the node measures how much of a small disc
// around the light the matte covers and dims that light's flare by the
// same amount, so a light sliding behind an edge fades out instead of
// popping off.
//
// Mask mode reads the matte the other way round (white lets the light
// through), which is what the input did in the original FlareSim: limit
// which lights in the plate may flare.

#pragma once

#include "DDImage/Iop.h"
#include "DDImage/Row.h"

#include <algorithm>
#include <cmath>

namespace flaresim {

enum MatteMode { kMatteOcclude = 0, kMatteMask = 1 };

static const char* const kMatteModes[] = {
    "Occlude", "Mask", nullptr
};

// Average matte alpha over a disc of `radius` pixels centred on (cx, cy).
// Samples outside the matte's bbox count as 0.  At most 15x15 samples, so
// it stays cheap for many Auto Detect sources.
inline float matte_disc_coverage(DD::Image::Iop* matte, float cx, float cy, float radius)
{
    using namespace DD::Image;
    const int x0 = matte->info().x(), y0 = matte->info().y();
    const int x1 = matte->info().r(), y1 = matte->info().t();
    radius = std::max(radius, 0.5f);
    const int n = std::min(15, std::max(1, (int)std::ceil(radius * 2.0f)));
    const float step = 2.0f * radius / n;

    const int rx0 = std::max(x0, (int)std::floor(cx - radius));
    const int rx1 = std::min(x1, (int)std::ceil(cx + radius) + 1);
    Row row(rx0, std::max(rx1, rx0 + 1));

    float sum = 0.0f;
    int   cnt = 0;
    for (int j = 0; j < n; ++j)
    {
        const float sy = cy - radius + (j + 0.5f) * step;
        const int   iy = (int)std::floor(sy);
        bool row_loaded = false;
        for (int i = 0; i < n; ++i)
        {
            const float sx = cx - radius + (i + 0.5f) * step;
            const float dx = sx - cx, dy = sy - cy;
            if (dx * dx + dy * dy > radius * radius) continue;
            ++cnt;
            const int ix = (int)std::floor(sx);
            if (iy < y0 || iy >= y1 || ix < rx0 || ix >= rx1) continue;
            if (!row_loaded) {
                matte->get(iy, rx0, rx1, Mask_Alpha, row);
                row_loaded = true;
            }
            sum += std::min(std::max(row[Chan_Alpha][ix], 0.0f), 1.0f);
        }
    }
    return cnt ? sum / cnt : 0.0f;
}

// How much of a light at (cx, cy) gets through: 1 = all, 0 = none.
// Lights outside both the format and the matte's bbox are left alone (the
// matte has nothing there).  A matte that covers the overscan of an
// undistorted plate also works on lights out there.
inline float matte_visibility(DD::Image::Iop* matte, int mode, float cx, float cy,
                              float radius,
                              int fmt_x0, int fmt_y0, int fmt_w, int fmt_h)
{
    if (!matte) return 1.0f;
    const bool in_format = cx >= fmt_x0 && cx < fmt_x0 + fmt_w &&
                           cy >= fmt_y0 && cy < fmt_y0 + fmt_h;
    const bool in_matte  = cx >= matte->info().x() && cx < matte->info().r() &&
                           cy >= matte->info().y() && cy < matte->info().t();
    if (!in_format && !in_matte)
        return 1.0f;
    const float cov = matte_disc_coverage(matte, cx, cy, radius);
    return mode == kMatteMask ? cov : 1.0f - cov;
}

// Ask for the matte's alpha over its whole bbox, wherever the lights are.
inline void request_matte(DD::Image::Iop* matte, int count)
{
    if (matte)
        matte->request(matte->info().x(), matte->info().y(),
                       matte->info().r(), matte->info().t(),
                       DD::Image::Mask_Alpha, count);
}

} // namespace flaresim
