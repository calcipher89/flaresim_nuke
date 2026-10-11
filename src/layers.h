// ============================================================================
// layers.h — haze and starburst in the node's output, and the AOV layers
//
// Shared by FlareSim and FlareSim3D.  RGB is always the whole flare
// (ghosts + haze + starburst) and alpha its luminance.  With Output Layers
// on, the parts also come out on their own:
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

// Add the layers to the node's output channels.
template <class Info>
inline void turn_on_layers(Info& info)
{
    const OutputLayers& l = output_layers();
    for (int c = 0; c < 3; ++c) {
        info.turn_on(l.flare[c]);
        info.turn_on(l.haze[c]);
        info.turn_on(l.starburst[c]);
    }
}

inline void remove_layers(DD::Image::ChannelSet& set)
{
    const OutputLayers& l = output_layers();
    for (int c = 0; c < 3; ++c) {
        set -= l.flare[c];
        set -= l.haze[c];
        set -= l.starburst[c];
    }
}

// After the ghosts are in RGBA of row [x, r): copy them to flare.rgb, write
// haze.rgb and starburst.rgb, and add haze and starburst to RGB and alpha.
//   buf_y   the row inside the node's buffer, or -1 when there's nothing
//           cached for it (the layers are then black)
//   x_off   buffer column 0 in image coordinates
inline void finish_row(const FlareExtras& fx, bool layers_on, int buf_y,
                       int x, int r, int x_off,
                       DD::Image::ChannelMask channels, DD::Image::Row& row)
{
    using namespace DD::Image;
    const OutputLayers& l = output_layers();
    static const Channel rgb[3] = { Chan_Red, Chan_Green, Chan_Blue };
    const int n = r - x;
    if (n <= 0) return;
    const bool have = buf_y >= 0 && !fx.empty();

    if (layers_on) {
        for (int c = 0; c < 3; ++c) {
            if (channels.contains(l.flare[c])) {
                float* dst = row.writable(l.flare[c]);
                if (channels.contains(rgb[c])) {
                    const float* src = row[rgb[c]];
                    std::copy(src + x, src + r, dst + x);
                } else {
                    std::fill(dst + x, dst + r, 0.0f);
                }
            }
            if (channels.contains(l.haze[c])) {
                float* dst = row.writable(l.haze[c]);
                std::fill(dst + x, dst + r, 0.0f);
                if (have) fx.add_haze_row(c, buf_y, x - x_off, r - x_off, dst + x);
            }
            if (channels.contains(l.starburst[c])) {
                float* dst = row.writable(l.starburst[c]);
                std::fill(dst + x, dst + r, 0.0f);
                if (have) fx.add_starburst_row(c, buf_y, x - x_off, r - x_off, dst + x);
            }
        }
    }

    if (!have) return;
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
