// ============================================================================
// ghost_overrides.h — art direction for single ghosts
//
// The Surfaces tab sets each surface's gain, tint, offset and scale, and a
// ghost (it bounces off two surfaces, A and B) gets the product of both.
// That moves every ghost that shares a surface.  Ghost Overrides set one
// ghost on its own, on top of its surfaces' settings:
//
//     3-12 gain=2.5 color=1,0.6,0.9 scale=0.8 offset=10,-5
//     4-9 off
//
// One ghost per line (or separated by ';'), named by its two surfaces as
// the Lens Browser shows them.  Every setting is optional:
//
//     gain=g          multiplies the ghost's brightness
//     color=r,g,b     multiplies its colour (a single value tints grey)
//     scale=s         grows or shrinks it about the frame centre
//     offset=x,y      shifts it, in the node's pixels, +y up
//     off             leaves it out ("on" puts it back)
//
// Lines starting with '#' are comments.  A line that can't be read is
// skipped and reported, so a typo never stops the render.
//
// Header only: used by both nodes and the Lens Browser preview library.
// ============================================================================
#pragma once

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <string>
#include <vector>

namespace flaresim {

struct GhostOverride
{
    int   a = -1, b = -1;              // surfaces, a < b
    bool  on = true;
    float gain = 1.0f;
    float col[3] = { 1.0f, 1.0f, 1.0f };
    float scale = 1.0f;
    float ox = 0.0f, oy = 0.0f;        // node pixels, +y up
};

namespace detail {

inline std::string trim(const std::string& s)
{
    size_t i = 0, j = s.size();
    while (i < j && std::isspace((unsigned char)s[i])) ++i;
    while (j > i && std::isspace((unsigned char)s[j - 1])) --j;
    return s.substr(i, j - i);
}

inline std::string lower(std::string s)
{
    for (char& c : s) c = (char)std::tolower((unsigned char)c);
    return s;
}

// Reads 1..max comma-separated numbers.  Returns how many, 0 on error.
inline int read_floats(const std::string& s, float* out, int max)
{
    int n = 0;
    size_t pos = 0;
    for (;;) {
        size_t end = s.find(',', pos);
        if (end == std::string::npos) end = s.size();
        const std::string part = trim(s.substr(pos, end - pos));
        char* stop = nullptr;
        const float v = std::strtof(part.c_str(), &stop);
        if (part.empty() || *stop != '\0' || n == max || !(v == v)) return 0;
        out[n++] = v;
        if (end == s.size()) return n;
        pos = end + 1;
    }
}

// "3-12", "3,12" or "3 12" style ghost name already split off: "3-12".
inline bool read_pair(const std::string& s, int& a, int& b)
{
    const size_t dash = s.find_first_of("-+/");
    if (dash == std::string::npos || dash == 0 || dash + 1 >= s.size()) return false;
    char* stop = nullptr;
    const std::string sa = s.substr(0, dash), sb = s.substr(dash + 1);
    const long va = std::strtol(sa.c_str(), &stop, 10);
    if (*stop != '\0') return false;
    const long vb = std::strtol(sb.c_str(), &stop, 10);
    if (*stop != '\0') return false;
    if (va < 0 || vb < 0 || va == vb || va > 10000 || vb > 10000) return false;
    a = (int)std::min(va, vb);
    b = (int)std::max(va, vb);
    return true;
}

} // namespace detail

// Read the overrides text.  Lines that can't be read are skipped and, when
// bad_lines is given, counted there.  A ghost named twice: the last wins.
inline std::vector<GhostOverride> parse_ghost_overrides(const std::string& text,
                                                       int* bad_lines = nullptr)
{
    std::vector<GhostOverride> out;
    int bad = 0;
    size_t pos = 0;
    while (pos <= text.size()) {
        size_t end = text.find_first_of("\n;", pos);
        if (end == std::string::npos) end = text.size();
        std::string line = detail::trim(text.substr(pos, end - pos));
        pos = end + 1;
        if (line.empty() || line[0] == '#') {
            if (end == text.size()) break;
            continue;
        }
        // Split on whitespace.
        std::vector<std::string> words;
        size_t i = 0;
        while (i < line.size()) {
            while (i < line.size() && std::isspace((unsigned char)line[i])) ++i;
            size_t j = i;
            while (j < line.size() && !std::isspace((unsigned char)line[j])) ++j;
            if (j > i) words.push_back(line.substr(i, j - i));
            i = j;
        }
        GhostOverride g;
        bool ok = !words.empty() && detail::read_pair(words[0], g.a, g.b);
        for (size_t w = 1; ok && w < words.size(); ++w) {
            const std::string word = detail::lower(words[w]);
            const size_t eq = word.find('=');
            if (eq == std::string::npos) {
                if (word == "off")      g.on = false;
                else if (word == "on")  g.on = true;
                else ok = false;
                continue;
            }
            const std::string key = word.substr(0, eq), val = word.substr(eq + 1);
            float v[3];
            if (key == "gain") {
                ok = detail::read_floats(val, v, 1) == 1 && v[0] >= 0.0f;
                if (ok) g.gain = v[0];
            } else if (key == "color" || key == "colour" || key == "tint") {
                const int n = detail::read_floats(val, v, 3);
                ok = (n == 1 || n == 3);
                if (ok) for (int c = 0; c < 3; ++c) g.col[c] = std::max(v[n == 1 ? 0 : c], 0.0f);
            } else if (key == "scale") {
                ok = detail::read_floats(val, v, 1) == 1 && v[0] > 0.0f;
                if (ok) g.scale = v[0];
            } else if (key == "offset") {
                ok = detail::read_floats(val, v, 2) == 2;
                if (ok) { g.ox = v[0]; g.oy = v[1]; }
            } else {
                ok = false;
            }
        }
        if (ok) {
            auto same = std::find_if(out.begin(), out.end(), [&](const GhostOverride& o)
                                     { return o.a == g.a && o.b == g.b; });
            if (same != out.end()) *same = g;
            else out.push_back(g);
        } else {
            ++bad;
        }
        if (end == text.size()) break;
    }
    if (bad_lines) *bad_lines = bad;
    return out;
}

// The override for the ghost off surfaces a and b (either order), or null.
inline const GhostOverride* find_ghost_override(const std::vector<GhostOverride>& list,
                                                int a, int b)
{
    if (a > b) std::swap(a, b);
    for (const GhostOverride& g : list)
        if (g.a == a && g.b == b) return &g;
    return nullptr;
}

} // namespace flaresim
