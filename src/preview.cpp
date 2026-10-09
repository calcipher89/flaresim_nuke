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

// CPU ghost render: the same ray setup as the node's CUDA kernel, with the
// three classic wavelengths.  Rays on neighbouring pupil grid points form a
// mesh on the sensor and each cell's energy is spread over its area, so a
// defocused ghost comes out as a smooth disc instead of a cloud of dots,
// even with a small ray grid.  Pairs are split across threads.
void render_cpu(Preview& pv, const BrightPixel& src, const GhostConfig& cfg,
                float shw, float shh, int w, int h)
{
    const int n = cfg.ray_grid;
    const auto grid = pupil_grid(n, cfg.aperture_blades, cfg.aperture_rotation_deg,
                                 (uint32_t)cfg.pupil_jitter_seed * 1000003u);
    int n_inside = 0;
    for (const auto& s : grid) n_inside += s.inside;
    if (!n_inside) return;
    const float ray_weight = 1.0f / n_inside;
    const float front_R    = pv.lens.surfaces[0].semi_aperture;
    const float start_z    = pv.lens.surfaces[0].z - 20.0f;

    float bx = std::tan(src.angle_x), by = std::tan(src.angle_y);
    const float inv = 1.0f / std::sqrt(bx * bx + by * by + 1.0f);
    const Vec3f dir(bx * inv, by * inv, inv);

    const float lambdas[3] = { cfg.wavelengths[0], cfg.wavelengths[1], cfg.wavelengths[2] };
    const float colour[3]  = { src.r, src.g, src.b };

    const int n_threads = std::max(1, std::min({ (int)std::thread::hardware_concurrency(),
                                                 (int)pv.pairs.size(), kMaxThreads }));
    if ((int)pv.canvases.size() < n_threads) pv.canvases.resize(n_threads);

    auto work = [&](int t)
    {
        Canvas& cv = pv.canvases[t];
        cv.reset(w, h);
        std::vector<Hit> hits(grid.size());
        std::vector<Tri> tris;
        std::vector<float> edges;
        for (size_t pi = t; pi < pv.pairs.size(); pi += n_threads)
        {
            const GhostPair& pair = pv.pairs[pi];
            const float scale = ray_weight * cfg.gain * pv.boosts[pi];
            for (int c = 0; c < 3; ++c)
            {
                if (colour[c] <= 0.0f) continue;
                for (size_t k = 0; k < grid.size(); ++k)
                {
                    Hit& hit = hits[k];
                    hit.ok = false;
                    if (!grid[k].inside) continue;
                    Ray ray;
                    ray.origin = Vec3f(grid[k].u * front_R, grid[k].v * front_R, start_z);
                    ray.dir    = dir;
                    const TraceResult tr = trace_ghost_ray(ray, pv.lens, pair.surf_a,
                                                           pair.surf_b, lambdas[c]);
                    if (!tr.valid) continue;
                    hit.x = (tr.position.x / (2.0f * shw) + 0.5f) * w;
                    hit.y = (tr.position.y / (2.0f * shh) + 0.5f) * h;
                    hit.v = tr.weight * scale * colour[c];
                    hit.ok = std::isfinite(hit.x) && std::isfinite(hit.y) &&
                             std::fabs(hit.x) < 1e5f && std::fabs(hit.y) < 1e5f &&
                             hit.v > 1e-14f;
                }
                // Each grid cell carries one ray's worth of energy, split
                // over its two triangles.  A cell with a corner missing (iris
                // edge, vignetting) keeps the triangle it still has.  Cells
                // stretched far beyond the ghost's typical cell size span a
                // discontinuity in the mapping (a fold or a clipped edge) and
                // would smear a streak across the frame, so their energy goes
                // to their corners instead.
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
                if (tris.empty()) continue;
                edges.resize(tris.size());
                for (size_t ti = 0; ti < tris.size(); ++ti)
                    edges[ti] = longest_edge(hits[tris[ti].a], hits[tris[ti].b],
                                             hits[tris[ti].c]);
                std::vector<float> sorted_edges(edges);
                std::nth_element(sorted_edges.begin(),
                                 sorted_edges.begin() + sorted_edges.size() / 2,
                                 sorted_edges.end());
                const float max_edge = std::max(4.0f, kMaxStretch * sorted_edges[sorted_edges.size() / 2]);
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
    return (int)static_cast<Preview*>(handle)->pairs.size();
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

    // Optics, matching FlareSim::do_compute().
    const float fov_h      = std::clamp(p->fov_h_deg, 1.0f, 170.0f) * (float)M_PI / 180.0f;
    const float tan_half_h = std::tan(fov_h * 0.5f);
    const float tan_half_v = tan_half_h * (float)h / (float)w;

    // Source position: top-left pixel origin in, y-up optics inside.
    const float ndc_x = (p->src_x - w * 0.5f) / w;
    const float ndc_y = ((h - p->src_y) - h * 0.5f) / h;
    const float si    = std::max(p->source_intensity, 0.0f) * 1000.0f;
    BrightPixel src;
    src.angle_x = std::atan(ndc_x * 2.0f * tan_half_h);
    src.angle_y = std::atan(ndc_y * 2.0f * tan_half_v);
    src.r = std::max(p->src_r, 0.0f) * si;
    src.g = std::max(p->src_g, 0.0f) * si;
    src.b = std::max(p->src_b, 0.0f) * si;

    if (pv.lens_ok)
    {
        const float shw = pv.lens.focal_length * tan_half_h;
        const float shh = pv.lens.focal_length * tan_half_v;

        GhostConfig cfg;
        cfg.ray_grid              = std::clamp(p->ray_grid, 4, 256);
        cfg.gain                  = std::max(p->flare_gain, 0.0f) * 1000.0f;
        cfg.aperture_blades       = p->aperture_blades;
        cfg.aperture_rotation_deg = p->aperture_rotation;
        cfg.spectral_jitter       = 1;
        cfg.spectral_jitter_seed  = p->seed;
        cfg.pupil_jitter          = 1;   // stratified, so passes average out
        cfg.pupil_jitter_seed     = p->seed;

        if (shw != pv.pairs_shw || shh != pv.pairs_shh)
        {
            filter_ghost_pairs(pv.lens, shw, shh, cfg, pv.pairs, pv.boosts);
            pv.pairs_shw = shw;
            pv.pairs_shh = shh;
        }

        if (!pv.pairs.empty())
        {
            render_cpu(pv, src, cfg, shw, shh, w, h);
        }

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
