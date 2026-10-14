// ============================================================================
// ghost_mesh_test.cpp — CPU check of Smooth ghost rendering (ghost_mesh.h).
//
// Renders a lens's ghosts with point splatting (as the CUDA kernel does in
// Points mode) and with the Smooth mesh fill, using the same helpers as the
// CUDA Smooth kernels, then checks that:
//   - both modes put the same total light in the frame (within a few %),
//   - Smooth is much less grainy inside large ghosts,
//   - cutting clipped edges beforehand, as the CUDA kernels do
//     (mesh_cut_find_kernel, mesh_cut_kernel), draws exactly the same image
//     as bisecting them while drawing.
//
//   flaresim_ghost_mesh_test [lens] [out_prefix w h points_grid smooth_grid]
//
// With out_prefix it also writes <prefix>_points.pfm and <prefix>_smooth.pfm
// (linear RGB) for a visual comparison.
// ============================================================================

#include "ghost.h"
#include "ghost_mesh.h"
#include "lens.h"
#include "trace.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <thread>
#include <vector>

namespace {

struct Scene
{
    LensSystem lens;
    std::vector<GhostPair> pairs;
    std::vector<float> boosts;
    float shw = 0, shh = 0;
    int w = 0, h = 0;
    Vec3f dir;
    float front_R = 0, start_z = 0;
    float lambdas[3] = { 650.0f, 550.0f, 450.0f };
    float gain = 1000.0f;
};

struct Image
{
    int w, h;
    std::vector<float> c[3];
    Image(int w_, int h_) : w(w_), h(h_)
    {
        for (auto& ch : c) ch.assign((size_t)w * h, 0.0f);
    }
    void add(const Image& o)
    {
        for (int k = 0; k < 3; ++k)
            for (size_t i = 0; i < c[k].size(); ++i) c[k][i] += o.c[k][i];
    }
};

bool pixel_of(const Scene& sc, const TraceResult& tr, float& px, float& py)
{
    if (!tr.valid) return false;
    px = (tr.position.x / (2.0f * sc.shw) + 0.5f) * sc.w;
    py = (tr.position.y / (2.0f * sc.shh) + 0.5f) * sc.h;
    return std::isfinite(px) && std::isfinite(py) && std::fabs(px) < 1e7f && std::fabs(py) < 1e7f;
}

Ray pupil_ray(const Scene& sc, float u, float v)
{
    Ray r;
    r.origin = Vec3f(u * sc.front_R, v * sc.front_R, sc.start_z);
    r.dir = sc.dir;
    return r;
}

template <class Fn>
void for_pairs(const Scene& sc, Image& out, Fn fn)
{
    const int nt = std::max(1, std::min(8, (int)std::thread::hardware_concurrency()));
    std::vector<Image> parts(nt, Image(sc.w, sc.h));
    std::vector<std::thread> th;
    for (int t = 0; t < nt; ++t)
        th.emplace_back([&, t] {
            for (size_t p = t; p < sc.pairs.size(); p += nt) fn(p, parts[t]);
        });
    for (auto& x : th) x.join();
    for (auto& p : parts) out.add(p);
}

// Point mode: regular grid of cell centres, as ghost_kernel with Pupil
// Jitter Off and 3 spectral samples.
Image render_points(const Scene& sc, int n)
{
    std::vector<std::pair<float, float>> grid;
    for (int k = 0; k < n * n; ++k)
    {
        const float u = ((k % n) + 0.5f) / n * 2.0f - 1.0f;
        const float v = ((k / n) + 0.5f) / n * 2.0f - 1.0f;
        if (u * u + v * v <= 1.0f) grid.push_back({ u, v });
    }
    const float ray_weight = 1.0f / grid.size();
    Image out(sc.w, sc.h);
    for_pairs(sc, out, [&](size_t p, Image& img) {
        for (int c = 0; c < 3; ++c)
        {
            auto add = [&](int idx, float v) { img.c[c][idx] += v; };
            for (const auto& g : grid)
            {
                const TraceResult tr = trace_ghost_ray(pupil_ray(sc, g.first, g.second), sc.lens,
                                                       sc.pairs[p].surf_a, sc.pairs[p].surf_b,
                                                       sc.lambdas[c]);
                float px, py;
                if (!pixel_of(sc, tr, px, py)) continue;
                const float e = tr.weight * ray_weight * sc.gain * sc.boosts[p];
                ghost_mesh::splat_point(px, py, e, sc.w, sc.h, add);
            }
        }
    });
    return out;
}

// Smooth mode: the lattice and fill used by the CUDA Smooth kernels.  With
// precut, clipped lattice edges are bisected first into a table indexed by
// edge_slot, as the CUDA kernels do, instead of while drawing.
Image render_smooth(const Scene& sc, int n, bool precut = false)
{
    using namespace ghost_mesh;
    const int m = lattice_size(n);
    const Iris ir = make_iris(0, 0.0f);
    const float inv_area = 1.0f / iris_area(ir);
    Image out(sc.w, sc.h);
    for_pairs(sc, out, [&](size_t p, Image& img) {
        std::vector<Vertex> verts((size_t)m * m);
        for (int c = 0; c < 3; ++c)
        {
            for (int j = 0; j < m; ++j)
                for (int i = 0; i < m; ++i)
                {
                    Vertex& vx = verts[(size_t)j * m + i];
                    vx.w = -1.0f;
                    float u, v;
                    if (!vertex_uv(i, j, n, 0.5f, 0.5f, ir, u, v)) continue;
                    const TraceResult tr = trace_ghost_ray(pupil_ray(sc, u, v), sc.lens,
                                                           sc.pairs[p].surf_a, sc.pairs[p].surf_b,
                                                           sc.lambdas[c]);
                    if (!pixel_of(sc, tr, vx.x, vx.y)) continue;
                    vx.w = tr.weight;
                }
            uint32_t hist[kEdgeBins] = {};
            for (int j = 0; j + 1 < m; ++j)
                for (int i = 0; i + 1 < m; ++i)
                {
                    int tri[2][3];
                    cell_triangles(i, j, m, tri);
                    for (auto& t : tri)
                    {
                        const Vertex &a = verts[t[0]], &b = verts[t[1]], &d = verts[t[2]];
                        if (a.w < 0 || b.w < 0 || d.w < 0) continue;
                        ++hist[edge_bin(longest_edge(a.x, a.y, b.x, b.y, d.x, d.y))];
                    }
                }
            const float max_edge = max_edge_from_hist(hist);
            if (max_edge <= 0.0f) continue;
            const float scale = sc.gain * sc.boosts[p];
            auto add = [&](int idx, float v) { img.c[c][idx] += v; };
            auto trace = [&](float u, float v, Vertex& out) {
                const TraceResult tr = trace_ghost_ray(pupil_ray(sc, u, v), sc.lens,
                                                       sc.pairs[p].surf_a, sc.pairs[p].surf_b,
                                                       sc.lambdas[c]);
                if (!pixel_of(sc, tr, out.x, out.y)) return false;
                out.w = tr.weight;
                return true;
            };
            struct Cut { float uv[2]; Vertex vx; };
            std::vector<Cut> cuts;
            if (precut)
            {
                cuts.assign((size_t)m * m * 3, Cut{});
                for (int k = 0; k < m * m; ++k)
                {
                    const int i = k % m, j = k / m;
                    const int next[3] = { i + 1 < m ? k + 1 : -1, j + 1 < m ? k + m : -1,
                                          (i + 1 < m && j + 1 < m) ? k + m + 1 : -1 };
                    float ua[2], ub[2];
                    if (!vertex_uv(i, j, n, 0.5f, 0.5f, ir, ua[0], ua[1])) continue;
                    for (int d = 0; d < 3; ++d)
                    {
                        const int kn = next[d];
                        if (kn < 0 || (verts[kn].w >= 0) == (verts[k].w >= 0)) continue;
                        if (!vertex_uv(kn % m, kn / m, n, 0.5f, 0.5f, ir, ub[0], ub[1])) continue;
                        Cut& c = cuts[(size_t)k * 3 + d];
                        if (verts[k].w >= 0) edge_cut(ua, ub, trace, c.uv, c.vx, verts[k]);
                        else                 edge_cut(ub, ua, trace, c.uv, c.vx, verts[kn]);
                    }
                }
            }
            auto raster = [&](const FillTri& t) { raster_triangle(t, sc.w, sc.h, add); };
            for (int j = 0; j + 1 < m; ++j)
                for (int i = 0; i + 1 < m; ++i)
                {
                    int tri[2][3];
                    cell_triangles(i, j, m, tri);
                    for (auto& t : tri)
                    {
                        float uv[3][2];
                        bool in_lattice[3];
                        Vertex vx[3];
                        for (int q = 0; q < 3; ++q)
                        {
                            in_lattice[q] = vertex_uv(t[q] % m, t[q] / m, n, 0.5f, 0.5f, ir,
                                                      uv[q][0], uv[q][1]);
                            vx[q] = verts[t[q]];
                        }
                        if (precut)
                        {
                            auto cut = [&](int qa, int qc, float* uv_cut, Vertex& v_cut) {
                                const Cut& c = cuts[edge_slot(t[qa], t[qc], m)];
                                uv_cut[0] = c.uv[0];
                                uv_cut[1] = c.uv[1];
                                v_cut = c.vx;
                            };
                            draw_triangle_cut(uv, in_lattice, vx, scale, inv_area, max_edge,
                                              sc.w, sc.h, cut, add, raster);
                        }
                        else
                            draw_triangle(uv, in_lattice, vx, scale, inv_area, max_edge,
                                          sc.w, sc.h, trace, add);
                    }
                }
        }
    });
    return out;
}

double total(const Image& img)
{
    double s = 0;
    for (const auto& ch : img.c)
        for (float v : ch) s += v;
    return s;
}

// Grain: mean relative difference between each pixel and the mean of its
// 5x5 neighbourhood, over pixels lit above 10% of the median lit level and
// away from strong gradients (so ghost rims don't count as grain).
double grain(const Image& img)
{
    const int w = img.w, h = img.h;
    std::vector<float> lum((size_t)w * h);
    for (size_t i = 0; i < lum.size(); ++i) lum[i] = img.c[0][i] + img.c[1][i] + img.c[2][i];
    std::vector<float> lit;
    for (float v : lum) if (v > 0) lit.push_back(v);
    if (lit.empty()) return 0;
    std::nth_element(lit.begin(), lit.begin() + lit.size() / 2, lit.end());
    const float floor_v = 0.1f * lit[lit.size() / 2];
    double sum = 0;
    long n = 0;
    for (int y = 3; y < h - 3; ++y)
        for (int x = 3; x < w - 3; ++x)
        {
            double m = 0;
            float lo = 1e30f, hi = 0;
            for (int dy = -2; dy <= 2; ++dy)
                for (int dx = -2; dx <= 2; ++dx)
                {
                    const float v = lum[(size_t)(y + dy) * w + x + dx];
                    m += v;
                    lo = std::min(lo, v);
                    hi = std::max(hi, v);
                }
            m /= 25.0;
            if (m < floor_v) continue;
            // 5x5 smooth of the neighbourhood's own smooth: skip edges, where
            // the 9x9 mean differs a lot from the 5x5 mean.
            double big = 0;
            for (int dy = -3; dy <= 3; ++dy)
                for (int dx = -3; dx <= 3; ++dx) big += lum[(size_t)(y + dy) * w + x + dx];
            big /= 49.0;
            if (std::fabs(big - m) > 0.05 * m) continue;
            sum += std::fabs(lum[(size_t)y * w + x] - m) / m;
            ++n;
        }
    return n ? sum / n : 0;
}

void write_pfm(const Image& img, const std::string& path)
{
    FILE* f = std::fopen(path.c_str(), "wb");
    if (!f) return;
    std::fprintf(f, "PF\n%d %d\n-1.0\n", img.w, img.h);
    for (int y = 0; y < img.h; ++y)
        for (int x = 0; x < img.w; ++x)
            for (int c = 0; c < 3; ++c)
                std::fwrite(&img.c[c][(size_t)y * img.w + x], 4, 1, f);
    std::fclose(f);
}

} // namespace

int main(int argc, char** argv)
{
    std::string lens_path = argc > 1 ? argv[1] : FLARESIM_TEST_LENS;
    const std::string prefix = argc > 2 ? argv[2] : "";
    Scene sc;
    sc.w = argc > 3 ? std::atoi(argv[3]) : 480;
    sc.h = argc > 4 ? std::atoi(argv[4]) : 330;
    const int n_points = argc > 5 ? std::atoi(argv[5]) : 256;
    const int n_smooth = argc > 6 ? std::atoi(argv[6]) : 96;

    if (!sc.lens.load(lens_path.c_str()))
    {
        std::fprintf(stderr, "could not load %s\n", lens_path.c_str());
        return 1;
    }
    const float fov_h = 40.0f * 3.14159265f / 180.0f;
    const float thh = std::tan(fov_h * 0.5f);
    sc.shw = sc.lens.focal_length * thh;
    sc.shh = sc.shw * sc.h / sc.w;
    // Light a little up and right of centre, as in a typical shot.
    const float ax = std::atan(0.30f * 2.0f * thh), ay = std::atan(0.12f * 2.0f * thh * sc.h / sc.w);
    float bx = std::tan(ax), by = std::tan(ay);
    const float inv = 1.0f / std::sqrt(bx * bx + by * by + 1.0f);
    sc.dir = Vec3f(bx * inv, by * inv, inv);
    sc.front_R = sc.lens.surfaces[0].semi_aperture;
    sc.start_z = sc.lens.surfaces[0].z - 20.0f;

    GhostConfig cfg;
    filter_ghost_pairs(sc.lens, sc.shw, sc.shh, cfg, sc.pairs, sc.boosts);
    if (sc.pairs.empty())
    {
        std::fprintf(stderr, "no ghosts for %s\n", lens_path.c_str());
        return 1;
    }

    const Image pts = render_points(sc, n_points);
    const Image smo = render_smooth(sc, n_smooth);
    const double tp = total(pts), ts = total(smo);
    const double gp = grain(pts), gs = grain(smo);
    std::printf("%zu ghosts, %dx%d\n", sc.pairs.size(), sc.w, sc.h);
    std::printf("points grid %4d: total %.4g  grain %.4f\n", n_points, tp, gp);
    std::printf("smooth grid %4d: total %.4g  grain %.4f\n", n_smooth, ts, gs);
    if (!prefix.empty())
    {
        write_pfm(pts, prefix + "_points.pfm");
        write_pfm(smo, prefix + "_smooth.pfm");
    }

    int fails = 0;

    // The lattice must cover exactly the iris: summed triangle pupil areas
    // equal the iris area for round and bladed irises at any offset.
    for (int blades : { 0, 5, 6, 9 })
        for (float off : { 0.5f, 0.13f, 0.91f })
        {
            using namespace ghost_mesh;
            const int n = 64, m = lattice_size(n);
            const Iris ir = make_iris(blades, 17.0f);
            double sum = 0;
            for (int j = 0; j + 1 < m; ++j)
                for (int i = 0; i + 1 < m; ++i)
                {
                    int tri[2][3];
                    cell_triangles(i, j, m, tri);
                    for (auto& t : tri)
                    {
                        float uv[3][2];
                        bool ok = true;
                        for (int q = 0; q < 3; ++q)
                            ok = vertex_uv(t[q] % m, t[q] / m, n, off, 1.0f - off, ir,
                                           uv[q][0], uv[q][1]) && ok;
                        const float wts[3] = { 1, 1, 1 };
                        if (ok) sum += triangle_share(uv, wts, 1.0f);
                    }
                }
            const double r = sum / iris_area(ir);
            if (!(r > 0.995 && r < 1.005))
            {
                std::printf("FAIL: lattice covers %.4f of a %d-blade iris (offset %.2f)\n",
                            r, blades, off);
                ++fails;
            }
        }

    // Edges cut beforehand: the same image, pixel for pixel.
    {
        const Image pre = render_smooth(sc, n_smooth, true);
        size_t differ = 0;
        for (int c = 0; c < 3; ++c)
            for (size_t i = 0; i < smo.c[c].size(); ++i)
                differ += pre.c[c][i] != smo.c[c][i];
        std::printf("edges cut beforehand: %zu pixel values differ\n", differ);
        if (differ)
        {
            std::printf("FAIL: cutting edges beforehand changed the image\n");
            ++fails;
        }
    }

    const double ratio = ts / tp;
    if (!(ratio > 0.95 && ratio < 1.05))
    {
        std::printf("FAIL: smooth/points light ratio %.3f, expected 1 +/- 0.05\n", ratio);
        ++fails;
    }
    if (!(gs < 0.5 * gp))
    {
        std::printf("FAIL: smooth grain %.4f not under half of points grain %.4f\n", gs, gp);
        ++fails;
    }
    if (!fails) std::printf("PASS (light ratio %.3f)\n", ratio);
    return fails ? 1 : 0;
}
