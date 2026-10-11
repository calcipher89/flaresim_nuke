// ============================================================================
// layers.h — haze and starburst in the node's output, and the AOV layers
//
// Shared by FlareSim and FlareSim3D.  RGB is always the whole flare
// (ghosts + haze + starburst) and alpha its luminance.  With Output Layers
// on, the parts also come out on their own (haze and starburst only while
// they are switched on):
//
//   flare.rgb      the ghosts
//   haze.rgb       the haze
//   starburst.rgb  the starburst
// ============================================================================
#pragma once

#include "DDImage/Channel.h"
#include "DDImage/ChannelSet.h"
#include "DDImage/Row.h"

#include "extras.h"

#include <algorithm>
#include <string>
#include <vector>

namespace flaresim {

struct OutputLayers
{
    DD::Image::Channel flare[3];
    DD::Image::Channel haze[3];
    DD::Image::Channel starburst[3];
};

inline const OutputLayers& output_layers()
{
    static const OutputLayers layers = [] {
        using DD::Image::getChannel;
        OutputLayers l;
        const char* rgb[3] = { "red", "green", "blue" };
        for (int c = 0; c < 3; ++c) {
            l.flare[c]     = getChannel((std::string("flare.") + rgb[c]).c_str());
            l.haze[c]      = getChannel((std::string("haze.") + rgb[c]).c_str());
            l.starburst[c] = getChannel((std::string("starburst.") + rgb[c]).c_str());
        }
        return l;
    }();
    return layers;
}

// Which layers the node outputs.  Haze and starburst only get a layer
// while they are switched on.
struct LayerSet
{
    bool flare = false, haze = false, starburst = false;
    LayerSet() = default;
    LayerSet(bool layers_on, bool haze_on, bool starburst_on)
        : flare(layers_on), haze(layers_on && haze_on),
          starburst(layers_on && starburst_on) {}
};

// Add the layers to the node's output channels.
template <class Info>
inline void turn_on_layers(Info& info, const LayerSet& on)
{
    const OutputLayers& l = output_layers();
    for (int c = 0; c < 3; ++c) {
        if (on.flare)     info.turn_on(l.flare[c]);
        if (on.haze)      info.turn_on(l.haze[c]);
        if (on.starburst) info.turn_on(l.starburst[c]);
    }
}

// Take the layers the node writes out of a set of channels (what's left
// passes through from the input).
inline void remove_layers(DD::Image::ChannelSet& set, const LayerSet& on)
{
    const OutputLayers& l = output_layers();
    for (int c = 0; c < 3; ++c) {
        if (on.flare)     set -= l.flare[c];
        if (on.haze)      set -= l.haze[c];
        if (on.starburst) set -= l.starburst[c];
    }
}

// After the ghosts are in RGBA (and flare.rgb) of row [x, r): write
// haze.rgb and starburst.rgb, and add haze and starburst to RGB and alpha.
//   buf_y   the row inside the node's buffer, or -1 when there's nothing
//           cached for it (the layers are then black)
//   x_off   buffer column 0 in image coordinates
inline void finish_row(const FlareExtras& fx, const LayerSet& on, int buf_y,
                       int x, int r, int x_off,
                       DD::Image::ChannelMask channels, DD::Image::Row& row)
{
    using namespace DD::Image;
    const OutputLayers& l = output_layers();
    static const Channel rgb[3] = { Chan_Red, Chan_Green, Chan_Blue };
    const int n = r - x;
    if (n <= 0) return;
    const bool have = buf_y >= 0 && !fx.empty();

    for (int c = 0; c < 3; ++c) {
        if (on.haze && channels.contains(l.haze[c])) {
            float* dst = row.writable(l.haze[c]);
            std::fill(dst + x, dst + r, 0.0f);
            if (have) fx.add_haze_row(c, buf_y, x - x_off, r - x_off, dst + x);
        }
        if (on.starburst && channels.contains(l.starburst[c])) {
            float* dst = row.writable(l.starburst[c]);
            std::fill(dst + x, dst + r, 0.0f);
            if (have) fx.add_starburst_row(c, buf_y, x - x_off, r - x_off, dst + x);
        }
    }

    const bool want_rgba = channels.contains(Chan_Red) || channels.contains(Chan_Green) ||
                           channels.contains(Chan_Blue) || channels.contains(Chan_Alpha);
    if (!have || !want_rgba) return;
    std::vector<float> extra[3];
    for (int c = 0; c < 3; ++c) {
        extra[c].assign(n, 0.0f);
        fx.add_haze_row(c, buf_y, x - x_off, r - x_off, extra[c].data());
        fx.add_starburst_row(c, buf_y, x - x_off, r - x_off, extra[c].data());
        if (channels.contains(rgb[c])) {
            float* dst = row.writable(rgb[c]);
            for (int i = 0; i < n; ++i) dst[x + i] += extra[c][i];
        }
    }
    if (channels.contains(Chan_Alpha)) {
        float* a = row.writable(Chan_Alpha);
        for (int i = 0; i < n; ++i) {
            const float lum = 0.2126f * extra[0][i] + 0.7152f * extra[1][i] + 0.0722f * extra[2][i];
            a[x + i] = std::min(1.0f, a[x + i] + std::max(lum, 0.0f));
        }
    }
}

} // namespace flaresim
