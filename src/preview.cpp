// ============================================================================
// preview.cpp — live flare preview for the Lens Browser window.
//
// Renders a single point source through the loaded lens, then blurs,
// tone-maps and converts to 8-bit RGBA for display.  It runs on the CPU with
// trace_ghost_ray() (three wavelengths, stratified pupil grid) and fills the
// mesh of traced rays, so ghosts look smooth at modest ray counts.  Keeping
// it off the GPU means no CUDA dependency and no contention with the node.
// ============================================================================

#include "preview.h"

#include "lens.h"
#include "ghost.h"
#include "trace.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <string>
#include <thread>
#include <vector>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

namespace {

// Brightness scale that makes exposure 0 look like a sensible default with
// the node's default Source Intensity (8) and Flare Gain (10).
constexpr float kDisplayScale = 0.02f;

struct Hit { float x, y, v; bool ok; };

struct Tri { int a, b, c; };

inline float longest_edge(const Hit& a, const Hit& b, const Hit& c)
{
    return std::sqrt(std::max({ (a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y),
                                (b.x - c.x) * (b.x - c.x) + (b.y - c.y) * (b.y - c.y),
                                (c.x - a.x) * (c.x - a.x) + (c.y - a.y) * (c.y - a.y) }));
}

// Triangles longer than this many times a ghost's median are treated as
// spanning a discontinuity.
constexpr float kMaxStretch = 3.0f;

// Each render thread keeps a full-size canvas, so cap the thread count to
// keep memory modest on many-core machines.
constexpr int kMaxThreads = 8;

// Image buffer for one render thread, interleaved rgb.
struct Canvas
{
    int w = 0, h = 0;
    std::vector<float> buf;

    void reset(int w_, int h_) { w = w_; h = h_; buf.assign((size_t)w * h * 3, 0.0f); }

    // Bilinear splat of value v into channel c at (px, py).
    void splat(float px, float py, int c, float v)
    {
        const float lx = px - 0.5f, ly = py - 0.5f;
        const int   x0 = (int)std::floor(lx), y0 = (int)std::floor(ly);
        const float fx = lx - x0, fy = ly - y0;
        const float wts[4] = { (1 - fx) * (1 - fy), fx * (1 - fy),
                               (1 - fx) * fy,       fx * fy };
        for (int k = 0; k < 4; ++k)
        {
            const int xi = x0 + (k & 1), yi = y0 + (k >> 1);
            if (xi < 0 || xi >= w || yi < 0 || yi >= h) continue;
            buf[((size_t)yi * w + xi) * 3 + c] += v * wts[k];
        }
    }

    // Spread energy e evenly over triangle abc (pixel coordinates).
    void triangle(const Hit& a, const Hit& b, const Hit& d, int c, float e)
    {
        const float area2 = (b.x - a.x) * (d.y - a.y) - (b.y - a.y) * (d.x - a.x);
        const float area  = 0.5f * std::fabs(area2);
        if (area < 2.0f)
        {
            // Small (or folded, at a caustic): a point at the centroid.
            splat((a.x + b.x + d.x) / 3.0f, (a.y + b.y + d.y) / 3.0f, c, e);
            return;
        }
        const int x0 = std::max(0, (int)std::floor(std::min({ a.x, b.x, d.x })));
        const int x1 = std::min(w - 1, (int)std::ceil(std::max({ a.x, b.x, d.x })));
        const int y0 = std::max(0, (int)std::floor(std::min({ a.y, b.y, d.y })));
        const int y1 = std::min(h - 1, (int)std::ceil(std::max({ a.y, b.y, d.y })));
        if (x0 > x1 || y0 > y1) return;
        const float density = e / area;
        const float sgn = area2 > 0.0f ? 1.0f : -1.0f;
        for (int y = y0; y <= y1; ++y)
        {
            const float py = y + 0.5f;
            float* row = &buf[(size_t)y * w * 3];
            for (int x = x0; x <= x1; ++x)
            {
                const float px = x + 0.5f;
                const float e0 = sgn * ((b.x - a.x) * (py - a.y) - (b.y - a.y) * (px - a.x));
                const float e1 = sgn * ((d.x - b.x) * (py - b.y) - (d.y - b.y) * (px - b.x));
                const float e2 = sgn * ((a.x - d.x) * (py - d.y) - (a.y - d.y) * (px - d.x));
                if (e0 >= 0.0f && e1 >= 0.0f && e2 >= 0.0f)
                    row[x * 3 + c] += density;
            }
        }
    }
};

struct Preview
{
    LensSystem lens;
    bool       lens_ok = false;

    // ghost pair filter cache, keyed on sensor size
    std::vector<GhostPair> pairs;
    std::vector<float>     boosts;
    float                  pairs_shw = -1.0f;
    float                  pairs_shh = -1.0f;

    std::vector<FspSurface> surfs;        // per-surface settings, may be short
    int                     hl_a = -1, hl_b = -1;   // highlighted ghost(s)
    int                     enabled_pairs = 0;      // pairs drawn by the last render

    std::vector<Canvas> canvases;         // one per render thread

    std::vector<float> r, g, b, tmp;      // current pass
    std::vector<float> acc_r, acc_g, acc_b;  // sum of passes
    int                passes = 0;
    std::string        error;
};

// Separable box blur, O(w*h) per pass.
void box_blur(std::vector<float>& buf, std::vector<float>& tmp,
              int w, int h, int radius, int passes)
{
    if (radius < 1 || passes < 1) return;
    tmp.resize(buf.size());
    const float inv = 1.0f / (2 * radius + 1);
    for (int p = 0; p < passes; ++p)
    {
        for (int y = 0; y < h; ++y)
        {
            const float* src = &buf[(size_t)y * w];
            float*       dst = &tmp[(size_t)y * w];
            float acc = 0.0f;
            for (int x = -radius; x <= radius; ++x)
                acc += src[std::clamp(x, 0, w - 1)];
            for (int x = 0; x < w; ++x)
            {
                dst[x] = acc * inv;
                acc += src[std::min(x + radius + 1, w - 1)];
                acc -= src[std::max(x - radius, 0)];
            }
        }
        for (int x = 0; x < w; ++x)
        {
            float acc = 0.0f;
            for (int y = -radius; y <= radius; ++y)
                acc += tmp[(size_t)std::clamp(y, 0, h - 1) * w + x];
            for (int y = 0; y < h; ++y)
            {
                buf[(size_t)y * w + x] = acc * inv;
                acc += tmp[(size_t)std::min(y + radius + 1, h - 1) * w + x];
                acc -= tmp[(size_t)std::max(y - radius, 0) * w + x];
            }
        }
    }
}

uint32_t wang_hash(uint32_t s)
{
    s = (s ^ 61u) ^ (s >> 16u);
    s *= 9u;
    s ^= s >> 4u;
    s *= 0x27d4eb2du;
    s ^= s >> 15u;
    return s;
}

// Pupil samples on an n x n grid in [-1, 1]², one stratified sample per
// cell.  Cells outside the round or polygonal iris are marked !inside but
// kept, so a sample's grid neighbours are at k ± 1 and k ± n.
struct PupilSample { float u, v; bool inside; };

std::vector<PupilSample> pupil_grid(int n, int blades, float rot_deg, uint32_t seed)
{
    std::vector<PupilSample> out((size_t)n * n);
    const bool  polygonal  = blades >= 3;
    const float apothem    = polygonal ? std::cos((float)M_PI / blades) : 1.0f;
    const float sector_ang = polygonal ? 2.0f * (float)M_PI / blades : 1.0f;
    const float rot        = rot_deg * (float)M_PI / 180.0f;
    for (int k = 0; k < n * n; ++k)
    {
        const float ju = wang_hash((uint32_t)k + seed) / 4294967296.0f;
        const float jv = wang_hash((uint32_t)(k + n * n) + seed) / 4294967296.0f;
        const float u = ((k % n) + ju) / n * 2.0f - 1.0f;
        const float v = ((k / n) + jv) / n * 2.0f - 1.0f;
        const float r2 = u * u + v * v;
        bool inside = r2 <= 1.0f;
        if (inside && polygonal)
        {
            float sector = std::fmod(std::atan2(v, u) - rot, sector_ang);
            if (sector < 0.0f) sector += sector_ang;
            inside = std::sqrt(r2) * std::cos(sector - sector_ang * 0.5f) <= apothem;
        }
        out[k] = { u, v, inside };
    }
    return out;
}

// How one ghost looks after the per-surface settings of its two surfaces.
struct PairStyle
{
    bool  on = true;
    float gain = 1.0f;
    float col[3] = { 1.0f, 1.0f, 1.0f };
    float ox = 0.0f, oy = 0.0f;           // fraction of the image width
    float scale = 1.0f;
};

PairStyle pair_style(const Preview& pv, const GhostPair& pair)
{
    PairStyle st;
    for (int s : { pair.surf_a, pair.surf_b })
    {
        if (s < 0 || s >= (int)pv.surfs.size()) continue;
        const FspSurface& f = pv.surfs[s];
        st.on     = st.on && f.enabled;
        st.gain  *= std::max(f.gain, 0.0f);
        st.col[0] *= std::max(f.r, 0.0f);
        st.col[1] *= std::max(f.g, 0.0f);
        st.col[2] *= std::max(f.b, 0.0f);
        st.ox    += f.offset_x;
        st.oy    += f.offset_y;
        st.scale *= f.scale;
    }
    return st;
}

// Brightness of ghosts left out of the highlight.
constexpr float kDimmed = 0.12f;

bool highlighted(const Preview& pv, const GhostPair& pair)
{
    if (pv.hl_a < 0) return true;
    if (pv.hl_b < 0) return pair.surf_a == pv.hl_a || pair.surf_b == pv.hl_a;
    return (pair.surf_a == pv.hl_a && pair.surf_b == pv.hl_b) ||
           (pair.surf_a == pv.hl_b && pair.surf_b == pv.hl_a);
}

// Everything a ghost trace needs that is the same for every pair.
struct TraceSetup
{
    std::vector<PupilSample> grid;
    int   n = 0;
    float ray_weight = 0.0f;
    float front_R = 0.0f, start_z = 0.0f;
    Vec3f dir;
    float lambdas[3];
    float colour[3];
    float shw = 0.0f, shh = 0.0f;
    int   w = 0, h = 0;
};

bool make_setup(const Preview& pv, const BrightPixel& src, const GhostConfig& cfg,
                float shw, float shh, int w, int h, TraceSetup& ts)
{
    ts.n    = cfg.ray_grid;
    ts.grid = pupil_grid(ts.n, cfg.aperture_blades, cfg.aperture_rotation_deg,
                         (uint32_t)cfg.pupil_jitter_seed * 1000003u);
    int n_inside = 0;
    for (const auto& s : ts.grid) n_inside += s.inside;
    if (!n_inside) return false;
    ts.ray_weight = 1.0f / n_inside;
    ts.front_R    = pv.lens.surfaces[0].semi_aperture;
    ts.start_z    = pv.lens.surfaces[0].z - 20.0f;

    float bx = std::tan(src.angle_x), by = std::tan(src.angle_y);
    const float inv = 1.0f / std::sqrt(bx * bx + by * by + 1.0f);
    ts.dir = Vec3f(bx * inv, by * inv, inv);
    for (int c = 0; c < 3; ++c) ts.lambdas[c] = cfg.wavelengths[c];
    ts.colour[0] = src.r; ts.colour[1] = src.g; ts.colour[2] = src.b;
    ts.shw = shw; ts.shh = shh; ts.w = w; ts.h = h;
    return true;
}

// Trace one ghost at one wavelength and build its mesh: rays on
// neighbouring pupil grid points form triangles on the sensor.  Each grid
// cell carries one ray's worth of energy, split over its two triangles; a
// cell with a corner missing (iris edge, vignetting) keeps the triangle it
// still has.  Returns the longest edge a triangle may have before it counts
// as spanning a discontinuity (a fold or a clipped edge), or 0 when no
// triangle was made.
float trace_mesh(const Preview& pv, const TraceSetup& ts, const GhostPair& pair, int c,
                 float value_scale, const PairStyle& st,
                 std::vector<Hit>& hits, std::vector<Tri>& tris, std::vector<float>& edges)
{
    const int n = ts.n;
    const float cx = ts.w * 0.5f, cy = ts.h * 0.5f;
    hits.resize(ts.grid.size());
    for (size_t k = 0; k < ts.grid.size(); ++k)
    {
        Hit& hit = hits[k];
        hit.ok = false;
        if (!ts.grid[k].inside) continue;
        Ray ray;
        ray.origin = Vec3f(ts.grid[k].u * ts.front_R, ts.grid[k].v * ts.front_R, ts.start_z);
        ray.dir    = ts.dir;
        const TraceResult tr = trace_ghost_ray(ray, pv.lens, pair.surf_a, pair.surf_b,
                                               ts.lambdas[c]);
        if (!tr.valid) continue;
        const float px = (tr.position.x / (2.0f * ts.shw) + 0.5f) * ts.w;
        const float py = (tr.position.y / (2.0f * ts.shh) + 0.5f) * ts.h;
        // Same per-pair transform as the node: scale about the frame
        // centre, then offset.
        hit.x = cx + (px - cx) * st.scale + st.ox * ts.w;
        hit.y = cy + (py - cy) * st.scale + st.oy * ts.w;
        hit.v = tr.weight * value_scale;
        hit.ok = std::isfinite(hit.x) && std::isfinite(hit.y) &&
                 std::fabs(hit.x) < 1e5f && std::fabs(hit.y) < 1e5f &&
                 hit.v > 1e-14f;
    }
    tris.clear();
    for (int j = 0; j + 1 < n; ++j)
        for (int i = 0; i + 1 < n; ++i)
        {
            const int k = j * n + i;
            if (hits[k].ok && hits[k + 1].ok && hits[k + n + 1].ok)
                tris.push_back({ k, k + 1, k + n + 1 });
            if (hits[k].ok && hits[k + n + 1].ok && hits[k + n].ok)
                tris.push_back({ k, k + n + 1, k + n });
        }
    if (tris.empty()) return 0.0f;
    edges.resize(tris.size());
    for (size_t ti = 0; ti < tris.size(); ++ti)
        edges[ti] = longest_edge(hits[tris[ti].a], hits[tris[ti].b], hits[tris[ti].c]);
    std::vector<float> sorted_edges(edges);
    std::nth_element(sorted_edges.begin(), sorted_edges.begin() + sorted_edges.size() / 2,
                     sorted_edges.end());
    return std::max(4.0f, kMaxStretch * sorted_edges[sorted_edges.size() / 2]);
}

// CPU ghost render: the same ray setup as the node's CUDA kernel, with the
// three classic wavelengths.  Each ghost's mesh is filled, so a defocused
// ghost comes out as a smooth disc instead of a cloud of dots, even with a
// small ray grid.  Pairs are split across threads.
void render_cpu(Preview& pv, const BrightPixel& src, const GhostConfig& cfg,
                float shw, float shh, int w, int h)
{
    TraceSetup ts;
    if (!make_setup(pv, src, cfg, shw, shh, w, h, ts)) return;

    std::vector<PairStyle> styles(pv.pairs.size());
    pv.enabled_pairs = 0;
    for (size_t pi = 0; pi < pv.pairs.size(); ++pi)
    {
        styles[pi] = pair_style(pv, pv.pairs[pi]);
        pv.enabled_pairs += styles[pi].on;
    }

    const int n_threads = std::max(1, std::min({ (int)std::thread::hardware_concurrency(),
                                                 (int)pv.pairs.size(), kMaxThreads }));
    if ((int)pv.canvases.size() < n_threads) pv.canvases.resize(n_threads);

    auto work = [&](int t)
    {
        Canvas& cv = pv.canvases[t];
        cv.reset(w, h);
        std::vector<Hit> hits;
        std::vector<Tri> tris;
        std::vector<float> edges;
        for (size_t pi = t; pi < pv.pairs.size(); pi += n_threads)
        {
            const PairStyle& st = styles[pi];
            if (!st.on || st.gain <= 0.0f) continue;
            const float scale = ts.ray_weight * cfg.gain * pv.boosts[pi] * st.gain *
                                (highlighted(pv, pv.pairs[pi]) ? 1.0f : kDimmed);
            for (int c = 0; c < 3; ++c)
            {
                const float colour = ts.colour[c] * st.col[c];
                if (colour <= 0.0f) continue;
                const float max_edge = trace_mesh(pv, ts, pv.pairs[pi], c, scale * colour,
                                                  st, hits, tris, edges);
                if (max_edge <= 0.0f) continue;
                // Stretched triangles would smear a streak across the
                // frame, so their energy goes to their corners instead.
                for (size_t ti = 0; ti < tris.size(); ++ti)
                {
                    const Hit& a = hits[tris[ti].a];
                    const Hit& b = hits[tris[ti].b];
                    const Hit& d = hits[tris[ti].c];
                    const float e = (a.v + b.v + d.v) / 6.0f;
                    if (edges[ti] <= max_edge)
                        cv.triangle(a, b, d, c, e);
                    else
                    {
                        cv.splat(a.x, a.y, c, e / 3.0f);
                        cv.splat(b.x, b.y, c, e / 3.0f);
                        cv.splat(d.x, d.y, c, e / 3.0f);
                    }
                }
            }
        }
    };

    std::vector<std::thread> threads;
    for (int t = 1; t < n_threads; ++t) threads.emplace_back(work, t);
    work(0);
    for (auto& th : threads) th.join();

    for (int t = 0; t < n_threads; ++t)
    {
        const float* b = pv.canvases[t].buf.data();
        for (size_t i = 0; i < (size_t)w * h; ++i)
        {
            pv.r[i] += b[i * 3 + 0];
            pv.g[i] += b[i * 3 + 1];
            pv.b[i] += b[i * 3 + 2];
        }
    }
}

inline bool in_triangle(const Hit& a, const Hit& b, const Hit& d, float px, float py)
{
    const float area2 = (b.x - a.x) * (d.y - a.y) - (b.y - a.y) * (d.x - a.x);
    const float sgn = area2 > 0.0f ? 1.0f : -1.0f;
    const float e0 = sgn * ((b.x - a.x) * (py - a.y) - (b.y - a.y) * (px - a.x));
    const float e1 = sgn * ((d.x - b.x) * (py - b.y) - (d.y - b.y) * (px - b.x));
    const float e2 = sgn * ((a.x - d.x) * (py - d.y) - (a.y - d.y) * (px - d.x));
    return e0 >= 0.0f && e1 >= 0.0f && e2 >= 0.0f;
}

// Average brightness each ghost puts into a small disc around (qx, qy)
// (y-up pixels), using the same meshes as render_cpu.
std::vector<float> ghost_light_at(const Preview& pv, const TraceSetup& ts,
                                  const GhostConfig& cfg, float qx, float qy, float radius)
{
    std::vector<float> light(pv.pairs.size(), 0.0f);
    // Sample points on a 5 x 5 grid inside the disc.
    std::vector<std::pair<float, float>> pts;
    for (int j = -2; j <= 2; ++j)
        for (int i = -2; i <= 2; ++i)
            if (i * i + j * j <= 5)
                pts.push_back({ qx + i * radius / 2.0f, qy + j * radius / 2.0f });
    const float disc_area = (float)M_PI * radius * radius;
    const float r2 = radius * radius;
    auto in_disc = [&](float x, float y) {
        return (x - qx) * (x - qx) + (y - qy) * (y - qy) <= r2;
    };

    std::vector<Hit> hits;
    std::vector<Tri> tris;
    std::vector<float> edges;
    for (size_t pi = 0; pi < pv.pairs.size(); ++pi)
    {
        const PairStyle st = pair_style(pv, pv.pairs[pi]);
        if (!st.on || st.gain <= 0.0f) continue;
        const float scale = ts.ray_weight * cfg.gain * pv.boosts[pi] * st.gain;
        for (int c = 0; c < 3; ++c)
        {
            const float colour = ts.colour[c] * st.col[c];
            if (colour <= 0.0f) continue;
            const float max_edge = trace_mesh(pv, ts, pv.pairs[pi], c, scale * colour,
                                              st, hits, tris, edges);
            if (max_edge <= 0.0f) continue;
            float sum = 0.0f;
            for (size_t ti = 0; ti < tris.size(); ++ti)
            {
                const Hit& a = hits[tris[ti].a];
                const Hit& b = hits[tris[ti].b];
                const Hit& d = hits[tris[ti].c];
                const float e = (a.v + b.v + d.v) / 6.0f;
                if (std::max({ a.x, b.x, d.x }) < qx - radius ||
                    std::min({ a.x, b.x, d.x }) > qx + radius ||
                    std::max({ a.y, b.y, d.y }) < qy - radius ||
                    std::min({ a.y, b.y, d.y }) > qy + radius)
                    continue;
                const float area = 0.5f * std::fabs((b.x - a.x) * (d.y - a.y) -
                                                    (b.y - a.y) * (d.x - a.x));
                if (edges[ti] > max_edge)
                {
                    for (const Hit* hp : { &a, &b, &d })
                        if (in_disc(hp->x, hp->y)) sum += e / 3.0f / disc_area;
                }
                else if (area < 2.0f)
                {
                    if (in_disc((a.x + b.x + d.x) / 3.0f, (a.y + b.y + d.y) / 3.0f))
                        sum += e / disc_area;
                }
                else
                {
                    int inside = 0;
                    for (const auto& q : pts) inside += in_triangle(a, b, d, q.first, q.second);
                    sum += e / area * inside / (float)pts.size();
                }
            }
            light[pi] += sum;
        }
    }
    return light;
}

// Shared by fsp_render and fsp_pick_ghosts: the light source and ghost
// settings for a render of this size, matching FlareSim::do_compute().
struct Optics
{
    BrightPixel src;
    GhostConfig cfg;
    float shw = 0.0f, shh = 0.0f;
};

Optics make_optics(Preview& pv, const FspParams* p, int w, int h)
{
    Optics o;
    const float fov_h      = std::clamp(p->fov_h_deg, 1.0f, 170.0f) * (float)M_PI / 180.0f;
    const float tan_half_h = std::tan(fov_h * 0.5f);
    const float tan_half_v = tan_half_h * (float)h / (float)w;

    // Source position: top-left pixel origin in, y-up optics inside.
    const float ndc_x = (p->src_x - w * 0.5f) / w;
    const float ndc_y = ((h - p->src_y) - h * 0.5f) / h;
    const float si    = std::max(p->source_intensity, 0.0f) * 1000.0f;
    o.src.angle_x = std::atan(ndc_x * 2.0f * tan_half_h);
    o.src.angle_y = std::atan(ndc_y * 2.0f * tan_half_v);
    o.src.r = std::max(p->src_r, 0.0f) * si;
    o.src.g = std::max(p->src_g, 0.0f) * si;
    o.src.b = std::max(p->src_b, 0.0f) * si;

    if (!pv.lens_ok) return o;
    o.shw = pv.lens.focal_length * tan_half_h;
    o.shh = pv.lens.focal_length * tan_half_v;

    GhostConfig& cfg = o.cfg;
    cfg.ray_grid              = std::clamp(p->ray_grid, 4, 256);
    cfg.gain                  = std::max(p->flare_gain, 0.0f) * 1000.0f;
    cfg.aperture_blades       = p->aperture_blades;
    cfg.aperture_rotation_deg = p->aperture_rotation;
    cfg.spectral_jitter       = 1;
    cfg.spectral_jitter_seed  = p->seed;
    cfg.pupil_jitter          = 1;   // stratified, so passes average out
    cfg.pupil_jitter_seed     = p->seed;

    if (o.shw != pv.pairs_shw || o.shh != pv.pairs_shh)
    {
        filter_ghost_pairs(pv.lens, o.shw, o.shh, cfg, pv.pairs, pv.boosts);
        pv.pairs_shw = o.shw;
        pv.pairs_shh = o.shh;
    }
    return o;
}

inline unsigned char to_srgb8(float v)
{
    v = std::clamp(v, 0.0f, 1.0f);
    v = (v <= 0.0031308f) ? v * 12.92f : 1.055f * std::pow(v, 1.0f / 2.4f) - 0.055f;
    return (unsigned char)std::lround(v * 255.0f);
}

} // namespace

extern "C" {

FSP_API int fsp_api_version(void) { return FSP_API_VERSION; }

FSP_API void* fsp_create(void) { return new Preview(); }

FSP_API void fsp_destroy(void* handle) { delete static_cast<Preview*>(handle); }

FSP_API int fsp_load_lens(void* handle, const char* path)
{
    Preview& pv = *static_cast<Preview*>(handle);
    pv.error.clear();
    pv.pairs.clear();
    pv.boosts.clear();
    pv.pairs_shw = pv.pairs_shh = -1.0f;
    pv.surfs.clear();
    pv.hl_a = pv.hl_b = -1;
    pv.enabled_pairs = 0;
    pv.lens = LensSystem();
    pv.lens_ok = path && pv.lens.load(path) && !pv.lens.surfaces.empty();
    if (!pv.lens_ok)
    {
        pv.error = std::string("Could not load lens: ") + (path ? path : "(null)");
        return 0;
    }
    return pv.lens.num_surfaces();
}

FSP_API int fsp_num_passes(void* handle)
{
    return static_cast<Preview*>(handle)->passes;
}

FSP_API int fsp_num_pairs(void* handle)
{
    return static_cast<Preview*>(handle)->enabled_pairs;
}

FSP_API void fsp_set_surfaces(void* handle, const FspSurface* surfaces, int n)
{
    Preview& pv = *static_cast<Preview*>(handle);
    pv.surfs.assign(surfaces, surfaces + std::max(0, surfaces ? n : 0));
}

FSP_API void fsp_set_highlight(void* handle, int surf_a, int surf_b)
{
    Preview& pv = *static_cast<Preview*>(handle);
    pv.hl_a = surf_a;
    pv.hl_b = surf_a < 0 ? -1 : surf_b;
}

FSP_API int fsp_surface_info(void* handle, int index, FspSurfaceInfo* out)
{
    const Preview& pv = *static_cast<const Preview*>(handle);
    if (!out || !pv.lens_ok || index < 0 || index >= pv.lens.num_surfaces()) return -1;
    const Surface& s = pv.lens.surfaces[index];
    out->radius        = s.radius;
    out->radius_y      = s.radius_y;
    out->thickness     = s.thickness;
    out->ior           = s.ior;
    out->abbe_v        = s.abbe_v;
    out->semi_aperture = s.semi_aperture;
    out->z             = s.z;
    out->coating       = s.coating;
    out->is_stop       = s.is_stop ? 1 : 0;
    out->surface_type  = s.surface_type;
    return 0;
}

FSP_API float fsp_sensor_z(void* handle)
{
    const Preview& pv = *static_cast<const Preview*>(handle);
    return pv.lens_ok ? pv.lens.sensor_z : 0.0f;
}

FSP_API int fsp_pick_ghosts(void* handle, const FspParams* p, int w, int h,
                            float x, float y, int* out_a, int* out_b,
                            float* out_share, int max_out)
{
    Preview& pv = *static_cast<Preview*>(handle);
    pv.error.clear();
    if (!p || w <= 0 || h <= 0 || max_out <= 0 || !out_a || !out_b || !out_share)
    {
        pv.error = "Bad arguments";
        return 0;
    }
    if (!pv.lens_ok) return 0;
    const Optics o = make_optics(pv, p, w, h);
    if (pv.pairs.empty()) return 0;
    TraceSetup ts;
    if (!make_setup(pv, o.src, o.cfg, o.shw, o.shh, w, h, ts)) return 0;

    // A few pixels, widened by the ghost blur, so small ghosts are easy
    // to hit.
    const float diag   = std::sqrt((float)w * w + (float)h * h);
    const float blur   = p->ghost_blur_passes > 0 ? std::max(p->ghost_blur, 0.0f) * diag *
                                                    std::sqrt((float)p->ghost_blur_passes)
                                                  : 0.0f;
    const float radius = std::max(3.0f, 0.004f * diag) + blur;
    const std::vector<float> light = ghost_light_at(pv, ts, o.cfg, x, h - y, radius);

    float total = 0.0f;
    for (float v : light) total += v;
    if (total <= 0.0f) return 0;
    std::vector<int> order(light.size());
    for (size_t i = 0; i < order.size(); ++i) order[i] = (int)i;
    std::sort(order.begin(), order.end(), [&](int a, int b) { return light[a] > light[b]; });
    int n = 0;
    for (int i : order)
    {
        const float share = light[i] / total;
        if (n >= max_out || share < 0.01f) break;
        out_a[n] = pv.pairs[i].surf_a;
        out_b[n] = pv.pairs[i].surf_b;
        out_share[n] = share;
        ++n;
    }
    return n;
}

FSP_API const char* fsp_last_error(void* handle)
{
    return static_cast<Preview*>(handle)->error.c_str();
}

FSP_API int fsp_render(void* handle, const FspParams* p,
                       int w, int h, unsigned char* out_rgba)
{
    Preview& pv = *static_cast<Preview*>(handle);
    pv.error.clear();
    if (!p || !out_rgba || w <= 0 || h <= 0) { pv.error = "Bad arguments"; return -1; }

    const size_t npx = (size_t)w * h;
    pv.r.assign(npx, 0.0f);
    pv.g.assign(npx, 0.0f);
    pv.b.assign(npx, 0.0f);

    const Optics o = make_optics(pv, p, w, h);
    if (pv.lens_ok)
    {
        if (!pv.pairs.empty())
            render_cpu(pv, o.src, o.cfg, o.shw, o.shh, w, h);
        else
            pv.enabled_pairs = 0;

        if (p->ghost_blur > 0.0f && p->ghost_blur_passes > 0)
        {
            const float diag   = std::sqrt((float)w * w + (float)h * h);
            const int   radius = std::max(1, (int)std::lround(p->ghost_blur * diag));
            box_blur(pv.r, pv.tmp, w, h, radius, p->ghost_blur_passes);
            box_blur(pv.g, pv.tmp, w, h, radius, p->ghost_blur_passes);
            box_blur(pv.b, pv.tmp, w, h, radius, p->ghost_blur_passes);
        }
    }

    // Accumulate passes.
    if (!p->accumulate || pv.passes == 0 || pv.acc_r.size() != npx)
    {
        pv.acc_r.assign(npx, 0.0f);
        pv.acc_g.assign(npx, 0.0f);
        pv.acc_b.assign(npx, 0.0f);
        pv.passes = 0;
    }
    for (size_t i = 0; i < npx; ++i)
    {
        pv.acc_r[i] += pv.r[i];
        pv.acc_g[i] += pv.g[i];
        pv.acc_b[i] += pv.b[i];
    }
    ++pv.passes;

    // Tone-map to 8-bit.  The flare buffers are y-up; the output is top-down.
    const float k    = kDisplayScale * std::pow(2.0f, p->exposure) / pv.passes;
    const float diag = std::sqrt((float)w * w + (float)h * h);
    const float core = std::max(1.5f, 0.004f * diag);
    const float halo = 0.03f * diag;
    const float sx = p->src_x, sy = h - p->src_y;   // source in y-up pixels
    const float cr = std::max(p->src_r, 0.0f);
    const float cg = std::max(p->src_g, 0.0f);
    const float cb = std::max(p->src_b, 0.0f);

    for (int y = 0; y < h; ++y)
    {
        unsigned char* row = out_rgba + (size_t)(h - 1 - y) * w * 4;
        for (int x = 0; x < w; ++x)
        {
            const size_t i = (size_t)y * w + x;
            float vr = pv.acc_r[i] * k, vg = pv.acc_g[i] * k, vb = pv.acc_b[i] * k;
            if (p->draw_source)
            {
                const float dx = x + 0.5f - sx, dy = y + 0.5f - sy;
                const float d2 = dx * dx + dy * dy;
                const float glow = 4.0f * std::exp(-d2 / (2.0f * core * core))
                                 + 0.15f * std::exp(-d2 / (2.0f * halo * halo));
                vr += cr * glow; vg += cg * glow; vb += cb * glow;
            }
            row[x * 4 + 0] = to_srgb8(1.0f - std::exp(-vr));
            row[x * 4 + 1] = to_srgb8(1.0f - std::exp(-vg));
            row[x * 4 + 2] = to_srgb8(1.0f - std::exp(-vb));
            row[x * 4 + 3] = 255;
        }
    }
    return 0;
}

} // extern "C"
