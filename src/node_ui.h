// node_ui.h — shared node settings for FlareSim and FlareSim3D:
// Quality presets (instead of a raw Ray Grid), the output bbox (Clip To)
// and the Show Advanced knob list.

#pragma once

#include "DDImage/Box.h"
#include "DDImage/Format.h"
#include "DDImage/Knob.h"
#include "DDImage/Knobs.h"
#include "DDImage/Op.h"

#include <algorithm>
#include <cmath>
#include <cstring>
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

// ---- Viewer Quality ----
// A lower Quality used only while looking at the node in Nuke's Viewer.
// Renders without a GUI (nuke -x / -t, the farm, Frame Server) ignore it,
// and FlareSimPlus_Preview.py sets the hidden render_full knob while a
// Write renders inside the GUI, so every written frame is at full Quality.
enum ViewerQuality { kViewerSame = 0, kViewerLow, kViewerMedium, kViewerHigh };

static const char* const kViewerQualityNames[] = {
    "Same as Render", "Low", "Medium", "High", nullptr
};

static const char* const kViewerQualityTooltip =
    "Quality used in the Viewer only, for faster interaction and playback.  "
    "Renders always use Quality: farm and command-line renders, and Write "
    "renders started from this Nuke.  Never higher than Quality.  While it "
    "is on, the node shows '(viewer: Low)' or similar in the Node Graph.";

// Ray grid for the Viewer: the Viewer Quality preset, but never more than
// the render's own grid.
inline int viewer_ray_grid(int render_grid, int viewer_quality, int fmt_w)
{
    if (viewer_quality <= kViewerSame) return render_grid;
    const int q = std::min(viewer_quality, (int)kViewerHigh) - kViewerLow + kQualityLow;
    return std::min(render_grid, quality_ray_grid(q, fmt_w));
}

// ---- Ghost Render ----
// Points: every ray is a dot (the original renderer).  Smooth: each ghost is
// drawn as a filled mesh of rays (ghost_mesh.h).  The knob defaults to
// Points so scripts saved before Smooth existed render as before; nodes made
// from the menu start on Smooth (menu.py).
enum GhostRender { kRenderPoints = 0, kRenderSmooth };

static const char* const kGhostRenderNames[] = { "Points", "Smooth", nullptr };

static const char* const kGhostRenderTooltip =
    "How each ghost is drawn.\n"
    "Smooth: rays on neighbouring grid points are joined into a mesh and "
    "filled, so large soft ghosts come out clean even at Medium quality.  "
    "Quality then sets how finely edges and fine detail follow the optics.  "
    "Pupil Jitter and Spectral Jitter are not used; raise Spectral Samples "
    "for smoother colour fringes.\n"
    "Points: every ray is a dot.  Large ghosts look grainy unless the ray "
    "grid is very high.  Scripts saved before Smooth existed use Points.";

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

// ---- Inputs through Dot and NoOp nodes ----
// A Dot or NoOp only passes its input on, so artists put them in camera and
// axis pipes to tidy the node graph.  These follow such a chain to the op
// behind it.

inline bool is_passthrough(const DD::Image::Op* op)
{
    const char* cls = op ? op->Class() : nullptr;
    return cls && (std::strcmp(cls, "Dot") == 0 || std::strcmp(cls, "NoOp") == 0);
}

// The T (CameraOp, AxisOp, ...) at op, or behind a chain of Dots and NoOps.
template <typename T>
inline T* through_passthrough(DD::Image::Op* op)
{
    for (int i = 0; op && i < 64; ++i) {
        if (T* t = dynamic_cast<T*>(op)) return t;
        if (!is_passthrough(op) || op->inputs() < 1) return nullptr;
        op = op->input(0);
    }
    return nullptr;
}

// For test_input(): a T, or a Dot/NoOp that may lead to one.
template <typename T>
inline bool accepts_through_passthrough(DD::Image::Op* op)
{
    return dynamic_cast<T*>(op) != nullptr || is_passthrough(op);
}

} // namespace flaresim
