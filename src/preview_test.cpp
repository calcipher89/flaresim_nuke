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
    const int all_pairs = fsp_num_pairs(pv);

    // Picking: the brightest lit pixel away from the source belongs to
    // some ghost.
    int best = -1, best_v = 0;
    for (int y = 0; y < h; ++y)
        for (int x = 0; x < w; ++x)
        {
            const unsigned char* px = &rgba[((size_t)y * w + x) * 4];
            const int v = px[0] + px[1] + px[2];
            if (v > best_v) { best_v = v; best = y * w + x; }
        }
    int pa[8], pb[8];
    float share[8];
    const int picked = best < 0 ? 0 : fsp_pick_ghosts(pv, &p, w, h, (float)(best % w) + 0.5f,
                                                      (float)(best / w) + 0.5f, pa, pb, share, 8);
    std::printf("picked %d ghosts at the brightest pixel", picked);
    if (picked) std::printf(", top: surfaces %d + %d (%.0f%%)", pa[0], pb[0], share[0] * 100.0f);
    std::printf("\n");
    if (picked <= 0) return 1;

    // Turning off one of the picked ghost's surfaces drops it, and every
    // other ghost off that surface.
    std::vector<FspSurface> surfs(surfaces);
    for (auto& s : surfs) s = FspSurface{ 1, 1.0f, 1.0f, 1.0f, 1.0f, 0.0f, 0.0f, 1.0f };
    surfs[pa[0]].enabled = 0;
    fsp_set_surfaces(pv, surfs.data(), (int)surfs.size());
    p.accumulate = 0;
    if (fsp_render(pv, &p, w, h, rgba.data()) < 0) return 1;
    const int fewer = fsp_num_pairs(pv);
    int pa2[8], pb2[8];
    float share2[8];
    const int picked2 = fsp_pick_ghosts(pv, &p, w, h, (float)(best % w) + 0.5f,
                                        (float)(best / w) + 0.5f, pa2, pb2, share2, 8);
    for (int i = 0; i < picked2; ++i)
        if (pa2[i] == pa[0] || pb2[i] == pa[0]) { std::fprintf(stderr, "disabled surface still picked\n"); return 1; }
    std::printf("surface %d off: %d of %d ghosts left\n", pa[0], fewer, all_pairs);
    if (fewer >= all_pairs) return 1;

    FspSurfaceInfo info;
    if (fsp_surface_info(pv, 0, &info) != 0 || info.semi_aperture <= 0.0f) return 1;
    if (fsp_surface_info(pv, surfaces, &info) != -1) return 1;
    if (fsp_sensor_z(pv) <= 0.0f) return 1;
    fsp_destroy(pv);

    if (fsp_num_passes(pv = fsp_create()) != 0) return 1;
    fsp_destroy(pv);
    return lit > 100 ? 0 : 1;
}
