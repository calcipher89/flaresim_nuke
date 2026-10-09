// preview_test.cpp — checks the Lens Browser's preview library renders a
// flare: run with the path to a .lens file.

#include "preview.h"

#include <cstdio>
#include <vector>

int main(int argc, char** argv)
{
    if (argc < 2) { std::fprintf(stderr, "usage: %s <lens file>\n", argv[0]); return 2; }
    if (fsp_api_version() != FSP_API_VERSION) { std::fprintf(stderr, "API version mismatch\n"); return 1; }

    void* pv = fsp_create();
    const int surfaces = fsp_load_lens(pv, argv[1]);
    if (surfaces <= 0) { std::fprintf(stderr, "%s\n", fsp_last_error(pv)); return 1; }

    const int w = 320, h = 180;
    std::vector<unsigned char> rgba((size_t)w * h * 4);
    FspParams p = {};
    p.src_x = w * 0.7f; p.src_y = h * 0.3f;
    p.src_r = p.src_g = p.src_b = 1.0f;
    p.source_intensity = 8.0f; p.flare_gain = 10.0f; p.fov_h_deg = 40.0f;
    p.ray_grid = 32; p.ghost_blur = 0.003f; p.ghost_blur_passes = 3;
    p.draw_source = 0;

    for (int pass = 0; pass < 3; ++pass)
    {
        p.accumulate = pass > 0;
        p.seed = pass;
        if (fsp_render(pv, &p, w, h, rgba.data()) < 0)
        {
            std::fprintf(stderr, "render failed: %s\n", fsp_last_error(pv));
            return 1;
        }
    }

    long lit = 0;
    for (size_t i = 0; i < rgba.size(); i += 4)
        lit += (rgba[i] | rgba[i + 1] | rgba[i + 2]) > 8;
    std::printf("surfaces %d, ghost pairs %d, passes %d, lit pixels %ld\n",
                surfaces, fsp_num_pairs(pv), fsp_num_passes(pv), lit);
    fsp_destroy(pv);

    if (fsp_num_passes(pv = fsp_create()) != 0) return 1;
    fsp_destroy(pv);
    return lit > 100 ? 0 : 1;
}
