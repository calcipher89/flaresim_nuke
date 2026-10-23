// ============================================================================
// ghost_streaks.h — the thin lines Ghost Overrides "streak=" draws through
// single ghosts
//
// An anamorphic lens draws a horizontal streak through the light, and a
// fainter one through each small, focused ghost.  The ghost renderer only
// follows light that bounces twice, so these come from here: each ghost
// that asks for one is traced with a small pupil grid at the three
// wavelengths, and its centre and light give the line (drawn by
// FlareExtras).  A ghost's streak follows it as the light moves, and
// carries a share of its light (streak gain 1 = as much light as the
// ghost), so it brightens and dims with it.
//
// CPU only, shared by both nodes and the Lens Browser preview library.
// ============================================================================
#pragma once

#include "extras.h"   // GhostStreak
#include "ghost.h"
#include "lens.h"

#include <vector>

namespace flaresim {

// A ghost that draws a streak, as the renderer draws the ghost.
struct StreakGhost
{
    GhostPair pair;
    float boost = 1.0f;               // area boost x surface and override gains
    float col[3] = { 1.0f, 1.0f, 1.0f };   // surfaces x override colour
    float sx = 1.0f, sy = 1.0f;       // scale about the frame centre
    float ox = 0.0f, oy = 0.0f;       // offset, buffer pixels, +y up
    float gain = 0.0f;                // the streak's share of the ghost's light
    float length = 0.15f;             // fraction of the diagonal
    float thickness = 0.001f;         // fraction of the diagonal
    float tint[3] = { 1.0f, 1.0f, 1.0f };
};

// Where the ghosts land, mapped as the ghost renderer maps them.
struct StreakFrame
{
    float sensor_half_w = 1.0f, sensor_half_h = 1.0f;   // mm
    int   fmt_w = 0, fmt_h = 0;
    int   fmt_x0_in_buf = 0, fmt_y0_in_buf = 0;
    float diag = 0.0f;                // length the fractions use, pixels
};

// Brightest lights that get streaks.
constexpr int kMaxStreakLights = 32;

// One streak per ghost per light (the brightest kMaxStreakLights lights).
// cfg gives the gain, wavelengths and iris, as for the ghost render.
std::vector<GhostStreak> trace_ghost_streaks(const LensSystem& lens,
                                             const std::vector<StreakGhost>& ghosts,
                                             const std::vector<BrightPixel>& lights,
                                             const GhostConfig& cfg,
                                             const StreakFrame& frame);

} // namespace flaresim
