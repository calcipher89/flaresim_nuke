// ghost_overrides_test.cpp — checks for the Ghost Overrides text (ghost_overrides.h).
#include "ghost_overrides.h"

#include <cmath>
#include <cstdio>

using namespace flaresim;

static int failures = 0;
static void check(bool ok, const char* what)
{
    std::printf("%s %s\n", ok ? "ok  " : "FAIL", what);
    if (!ok) ++failures;
}
static bool near(float a, float b) { return std::fabs(a - b) < 1e-6f; }

int main()
{
    int bad = -1;
    auto list = parse_ghost_overrides(
        "# Hawk APL 50\n"
        "3-12 gain=2.5 color=1,0.6,0.9 scale=0.8 offset=10,-5\n"
        "  9-4 off  \n"
        "\n"
        "2-5 tint=0.5;7-8 gain=0\r\n", &bad);
    check(bad == 0, "four good lines, no bad ones");
    check(list.size() == 4, "four ghosts");

    const GhostOverride* g = find_ghost_override(list, 12, 3);
    check(g && g->a == 3 && g->b == 12, "found by either surface order");
    check(g && near(g->gain, 2.5f) && near(g->col[1], 0.6f) && near(g->col[2], 0.9f),
          "gain and colour");
    check(g && near(g->scale, 0.8f) && near(g->ox, 10.0f) && near(g->oy, -5.0f),
          "scale and offset");
    check(g && g->on, "on by default");

    g = find_ghost_override(list, 4, 9);
    check(g && !g->on && g->a == 4 && g->b == 9, "off, surfaces sorted");
    g = find_ghost_override(list, 2, 5);
    check(g && near(g->col[0], 0.5f) && near(g->col[1], 0.5f) && near(g->col[2], 0.5f),
          "single colour value tints grey");
    g = find_ghost_override(list, 7, 8);
    check(g && near(g->gain, 0.0f), "gain 0 kept, separated by ';' and CRLF");
    check(find_ghost_override(list, 1, 2) == nullptr, "no override for other ghosts");

    // Bad lines are skipped and counted; the good ones still count.
    list = parse_ghost_overrides(
        "3-3 gain=2\n"            // same surface twice
        "3-x gain=2\n"            // not a number
        "3-4 gain=-1\n"           // negative gain
        "3-4 scale=0\n"           // zero scale
        "3-4 color=1,2\n"         // two colour values
        "3-4 offset=1\n"          // one offset value
        "3-4 glow=2\n"            // unknown setting
        "3-4 gain=1,2\n"          // two gains
        "5-6 gain=3\n"
        "5-6 gain=4\n", &bad);    // named twice: last wins
    check(bad == 8, "eight bad lines");
    check(list.size() == 1 && near(list[0].gain, 4.0f), "last of a repeated ghost wins");

    list = parse_ghost_overrides("", &bad);
    check(list.empty() && bad == 0, "empty text");
    list = parse_ghost_overrides("1-2 GAIN=2 Colour=0.5,0.5,1 OFF", &bad);
    check(bad == 0 && list.size() == 1 && !list[0].on && near(list[0].col[2], 1.0f),
          "case-insensitive, no trailing newline");

    std::printf(failures ? "%d FAILED\n" : "all passed\n", failures);
    return failures ? 1 : 0;
}
