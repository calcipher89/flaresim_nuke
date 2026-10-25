// ============================================================================
// extras_knobs.h — the finer haze and starburst knobs both nodes share
//
// Haze Aspect, Starburst Reach, and the Rays and Ring under Starburst
// (see extras.h).  They are all behind Show Advanced, and Rays and Ring
// draw only while Starburst is on (they come out in starburst.rgb).
// ============================================================================
#pragma once

#include "DDImage/Knobs.h"

#include "extras.h"
#include "node_ui.h"

namespace flaresim {

struct ExtrasDetailKnobs
{
    float haze_aspect     = 1.0f;
    float haze_core_tint[3] = { 1.0f, 1.0f, 1.0f };
    float starburst_reach = 0.0f;
    float rays_gain       = 0.0f;
    int   rays_count      = 48;
    float rays_length     = 0.25f;
    float rays_width      = 0.3f;
    float rays_spread     = 360.0f;
    float rays_angle      = -90.0f;
    int   rays_seed       = 1;
    float rays_tint[3]    = { 1.0f, 1.0f, 1.0f };
    float ring_gain       = 0.0f;
    float ring_radius     = 0.1f;
    float ring_aspect     = 1.0f;
    float ring_width      = 0.08f;
    float ring_rainbow    = 1.0f;
    float ring_arc        = 360.0f;
    float ring_angle      = -90.0f;
    float ring_tint[3]    = { 1.0f, 1.0f, 1.0f };
    float ring_streaks      = 0.0f;
    int   ring_streak_count = 120;
    float ring_streak_width = 0.4f;
    float ring_jitter       = 0.0f;

    // Knobs that follow the Haze and Starburst checkboxes.
    static const char* const* haze_names()
    {
        static const char* const n[] = { "haze_aspect", "haze_core_tint", nullptr };
        return n;
    }
    static const char* const* starburst_names()
    {
        static const char* const n[] = {
            "starburst_reach", "rays_gain", "rays_count", "rays_length", "rays_width",
            "rays_spread", "rays_angle", "rays_seed", "rays_tint", "ring_gain", "ring_radius",
            "ring_aspect", "ring_width", "ring_rainbow", "ring_arc", "ring_angle", "ring_tint",
            "ring_streaks", "ring_streak_count", "ring_streak_width", "ring_jitter", nullptr };
        return n;
    }

    // After Haze Tint.
    void haze_knobs(DD::Image::Knob_Callback f, AdvancedKnobs& adv)
    {
        using namespace DD::Image;
        adv.add(f, Float_knob(f, &haze_aspect, "haze_aspect", "Haze Aspect"));
        SetRange(f, 0.25, 4.0);
        Tooltip(f, "Width over height of the haze.  1 = round; 2 = twice as wide as it is "
                   "tall, the oval glow of an anamorphic lens.  The haze keeps its light.");
        adv.add(f, Color_knob(f, haze_core_tint, "haze_core_tint", "Haze Core Tint"));
        Tooltip(f, "Colours the haze close to the light, on top of Haze Tint: the tighter "
                   "glows Haze Shape adds.  The wide tail keeps Haze Tint alone.  Many "
                   "lenses glow warmer near the light than in the tail.  No effect at "
                   "Haze Shape 0.");
    }

    // After Starburst Softness.
    void starburst_knobs(DD::Image::Knob_Callback f, AdvancedKnobs& adv)
    {
        using namespace DD::Image;
        adv.add(f, Float_knob(f, &starburst_reach, "starburst_reach", "Starburst Reach"));
        SetRange(f, 0.0, 2.0);
        Tooltip(f, "How far the spikes stay bright.  0 = as in a real lens: a bright core "
                   "and spikes that fade fast.  1 = long thin spikes that fade slowly, "
                   "next to a small core; 2 = about even along their length.  Lower "
                   "Starburst Gain as you raise it.");

        adv.add(f, Float_knob(f, &rays_gain, "rays_gain", "Rays Gain"));
        SetRange(f, 0.0, 10.0);
        Tooltip(f, "Thin rays of random lengths fanning out from the brightest lights "
                   "(up to 8), as scratches, edges and haze catch the light.  0 = off.  "
                   "At 1 a ray's brightest point is a hundredth of the light.  They come "
                   "out with the starburst (starburst.rgb).");
        adv.add(f, Int_knob(f, &rays_count, "rays_count", "Rays Count"));
        SetRange(f, 1, 200);
        Tooltip(f, "How many rays.");
        adv.add(f, Float_knob(f, &rays_length, "rays_length", "Rays Length"));
        SetRange(f, 0.02, 1.0);
        Tooltip(f, "How far the longest rays reach, as a fraction of the image diagonal.  "
                   "Each ray gets a random length up to this.");
        adv.add(f, Float_knob(f, &rays_width, "rays_width", "Rays Width"));
        SetRange(f, 0.05, 3.0);
        Tooltip(f, "How wide each ray is, in degrees: rays widen away from the light.");
        adv.add(f, Float_knob(f, &rays_spread, "rays_spread", "Rays Spread"));
        SetRange(f, 0.0, 360.0);
        Tooltip(f, "The fan the rays fill, in degrees.  360 = all round the light.");
        adv.add(f, Float_knob(f, &rays_angle, "rays_angle", "Rays Angle"));
        SetRange(f, -180.0, 180.0);
        Tooltip(f, "Which way the fan points, in degrees: 0 = right, 90 = up, -90 = down.  "
                   "Only matters when Rays Spread is under 360.");
        adv.add(f, Int_knob(f, &rays_seed, "rays_seed", "Rays Seed"));
        Tooltip(f, "Picks another random set of rays.");
        adv.add(f, Color_knob(f, rays_tint, "rays_tint", "Rays Tint"));
        Tooltip(f, "Colours the rays, on top of the light's own colour.");

        adv.add(f, Float_knob(f, &ring_gain, "ring_gain", "Ring Gain"));
        SetRange(f, 0.0, 10.0);
        Tooltip(f, "A soft rainbow ring around the brightest lights (up to 8), red outside "
                   "and blue inside.  0 = off.  At 1 it peaks at a hundredth of the light.  "
                   "It comes out with the starburst (starburst.rgb).");
        adv.add(f, Float_knob(f, &ring_radius, "ring_radius", "Ring Radius"));
        SetRange(f, 0.01, 0.5);
        Tooltip(f, "Size of the ring, as a fraction of the image diagonal.");
        adv.add(f, Float_knob(f, &ring_aspect, "ring_aspect", "Ring Aspect"));
        SetRange(f, 0.25, 4.0);
        Tooltip(f, "Width over height of the ring: 1 = a circle, 2 = an oval twice as "
                   "wide as it is tall, as an anamorphic lens draws it.");
        adv.add(f, Float_knob(f, &ring_width, "ring_width", "Ring Width"));
        SetRange(f, 0.01, 0.5);
        Tooltip(f, "Thickness of the ring, as a fraction of its radius.");
        adv.add(f, Float_knob(f, &ring_rainbow, "ring_rainbow", "Ring Rainbow"));
        SetRange(f, 0.0, 2.0);
        Tooltip(f, "How far the colours split: 1 = red, green and blue rings side by "
                   "side, as a lens splits them by wavelength; 0 = one white ring.");
        adv.add(f, Float_knob(f, &ring_arc, "ring_arc", "Ring Arc"));
        SetRange(f, 0.0, 360.0);
        Tooltip(f, "How much of the ring is drawn, in degrees (360 = all of it).  Its "
                   "ends fade out.");
        adv.add(f, Float_knob(f, &ring_angle, "ring_angle", "Ring Angle"));
        SetRange(f, -180.0, 180.0);
        Tooltip(f, "Middle of the drawn arc, in degrees: 0 = right, 90 = above the light, "
                   "-90 = below.  Only matters when Ring Arc is under 360.");
        adv.add(f, Color_knob(f, ring_tint, "ring_tint", "Ring Tint"));
        Tooltip(f, "Colours the ring, on top of the light's own colour.");
        adv.add(f, Float_knob(f, &ring_streaks, "ring_streaks", "Ring Streaks"));
        SetRange(f, 0.0, 1.0);
        Tooltip(f, "Breaks the ring into fine streaks pointing at the light, each with the "
                   "ring's colours, as some anamorphic lenses draw it.  0 = a smooth ring, "
                   "1 = only streaks.  The ring keeps its overall brightness.  Rays Seed "
                   "picks another set.");
        adv.add(f, Int_knob(f, &ring_streak_count, "ring_streak_count", "Ring Streak Count"));
        Tooltip(f, "How many streaks go round the whole ring.");
        adv.add(f, Float_knob(f, &ring_streak_width, "ring_streak_width", "Ring Streak Width"));
        SetRange(f, 0.05, 3.0);
        Tooltip(f, "Width of the streaks, in degrees round the ring.");
        adv.add(f, Float_knob(f, &ring_jitter, "ring_jitter", "Ring Jitter"));
        SetRange(f, 0.0, 3.0);
        Tooltip(f, "Spectral noise: each colour of the ring moves in and out by its own "
                   "random amount round the ring, so the rainbow's colours drift and break "
                   "up instead of running as even bands.  In ring widths; about one patch "
                   "per streak (Ring Streak Count).  0 = even bands.  Rays Seed picks "
                   "another pattern.");
    }

    // starburst_on: the Starburst checkbox (Rays and Ring follow it).
    void apply(ExtrasConfig& xc, bool starburst_on) const
    {
        xc.haze_aspect     = haze_aspect;
        for (int c = 0; c < 3; ++c) xc.haze_core_tint[c] = haze_core_tint[c];
        xc.starburst_reach = starburst_reach;
        xc.rays_gain       = starburst_on ? rays_gain : 0.0f;
        xc.rays_count      = rays_count;
        xc.rays_length     = rays_length;
        xc.rays_width      = rays_width;
        xc.rays_spread     = rays_spread;
        xc.rays_angle      = rays_angle;
        xc.rays_seed       = rays_seed;
        xc.ring_gain       = starburst_on ? ring_gain : 0.0f;
        xc.ring_radius     = ring_radius;
        xc.ring_aspect     = ring_aspect;
        xc.ring_width      = ring_width;
        xc.ring_rainbow    = ring_rainbow;
        xc.ring_arc        = ring_arc;
        xc.ring_angle      = ring_angle;
        xc.ring_streaks      = ring_streaks;
        xc.ring_streak_count = ring_streak_count;
        xc.ring_streak_width = ring_streak_width;
        xc.ring_jitter       = ring_jitter;
        for (int c = 0; c < 3; ++c) { xc.rays_tint[c] = rays_tint[c]; xc.ring_tint[c] = ring_tint[c]; }
    }
};

} // namespace flaresim
