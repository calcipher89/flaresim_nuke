// ============================================================================
// ghost_mesh.h — Smooth ghost rendering (Ghost Render: Smooth).
//
// Point splatting adds every traced ray to the image as a dot, so a ghost
// spread over a large area gets only a few rays per pixel and looks grainy.
// Smooth mode traces the pupil grid as a lattice instead: rays on
// neighbouring grid points form triangles on the sensor, and each triangle's
// share of the light is spread evenly over it.  Large defocused ghosts come
// out as clean discs at modest ray counts; the ray grid then only sets how
// finely the ghost's shape and rim follow the optics.
//
// Shared by the CUDA kernels (ghost_cuda.cu) and the CPU test
// (ghost_mesh_test.cpp), so the two cannot drift apart.
// ============================================================================
#pragma once

#include <math.h>
#include <stdint.h>

#if defined(__CUDACC__)
#  define FS_MESH_HD __host__ __device__ __forceinline__
// The drawing templates take callables that are device-only in the CUDA
// kernels and host-only in the CPU test.
#  define FS_MESH_EXEC_CHECK _Pragma("nv_exec_check_disable")
#else
#  define FS_MESH_HD inline
#  define FS_MESH_EXEC_CHECK
#endif

namespace ghost_mesh {

constexpr float kPi = 3.14159265358979f;

// Smooth mode traces (n + 3)² rays per ghost and wavelength; past this the
// lattice gets very large for no visible gain.
constexpr int kMaxGrid = 4096;

// Triangles under this many pixels go to their centroid as a point.
constexpr float kMinFillArea = 0.5f;

// A triangle whose longest edge is more than this many times its ghost's
// median is treated as spanning a discontinuity (a ray path changing, or a
// clipped edge), so its light goes to its corners instead of smearing a
// streak across the frame.
constexpr float kMaxStretch = 16.0f;

// Edge-length histogram for that median: quarter-octave bins from 1/16 px.
constexpr int kEdgeBins = 96;

// Triangles covering at most this many pixels of bounding box are filled by
// their own thread; bigger ones are shared out across the warp.
constexpr int kLaneFillPixels = 32;

// Vertices per side of the lattice for a ray grid of n cells.  One spare
// ring each side, so the lattice covers the iris for any sub-cell offset.
FS_MESH_HD int lattice_size(int n) { return n + 3; }

// ---- Iris -----------------------------------------------------------------

struct Iris
{
    int   blades;     // 0 = round
    float rot;        // radians
    float apothem;    // polygon inscribed in the unit circle
    float sector;     // radians per blade
};

FS_MESH_HD Iris make_iris(int blades, float rotation_deg)
{
    Iris ir;
    ir.blades  = blades >= 3 ? blades : 0;
    ir.rot     = rotation_deg * (kPi / 180.0f);
    ir.apothem = ir.blades ? cosf(kPi / ir.blades) : 1.0f;
    ir.sector  = ir.blades ? 2.0f * kPi / ir.blades : 1.0f;
    return ir;
}

// Distance from the pupil centre to the iris edge in the direction (u, v).
FS_MESH_HD float iris_radius(const Iris& ir, float u, float v)
{
    if (!ir.blades) return 1.0f;
    float s = fmodf(atan2f(v, u) - ir.rot, ir.sector);
    if (s < 0.0f) s += ir.sector;
    return ir.apothem / cosf(s - ir.sector * 0.5f);
}

// Area of the iris in pupil units (the unit disc for a round iris).
FS_MESH_HD float iris_area(const Iris& ir)
{
    if (!ir.blades) return kPi;
    return ir.blades * sinf(kPi / ir.blades) * ir.apothem;
}

// Pupil position of lattice vertex (i, j) for a ray grid of n cells, with
// the lattice shifted by (off_u, off_v) cells (each in [0, 1)).  Vertices
// just outside the iris are pulled onto its edge, so the ghost's rim follows
// the iris instead of stepping along grid cells.  Returns false for vertices
// too far out to touch any cell inside.
FS_MESH_HD bool vertex_uv(int i, int j, int n, float off_u, float off_v,
                          const Iris& ir, float& u, float& v)
{
    const float d = 2.0f / n;
    u = -1.0f - d + (i + off_u) * d;
    v = -1.0f - d + (j + off_v) * d;
    const float r = sqrtf(u * u + v * v);
    if (r < 1e-12f) return true;
    const float rb = iris_radius(ir, u, v);
    if (r <= rb) return true;
    if (r > rb + 1.5f * d) return false;
    const float k = rb / r;
    u *= k;
    v *= k;
    return true;
}

// ---- Stretch test -----------------------------------------------------------

FS_MESH_HD int edge_bin(float len)
{
    const float l = fmaxf(len, 1.0f / 16.0f);
    const int b = (int)floorf((log2f(l) + 4.0f) * 4.0f);
    return b < 0 ? 0 : (b >= kEdgeBins ? kEdgeBins - 1 : b);
}

// Longest edge a triangle may have, from its ghost's edge histogram.
// Returns 0 when the histogram is empty.
FS_MESH_HD float max_edge_from_hist(const uint32_t* hist)
{
    uint32_t total = 0;
    for (int b = 0; b < kEdgeBins; ++b) total += hist[b];
    if (!total) return 0.0f;
    uint32_t acc = 0;
    int b = 0;
    for (; b < kEdgeBins; ++b)
    {
        acc += hist[b];
        if (2 * acc >= total) break;
    }
    const float median = exp2f((b + 1) * 0.25f - 4.0f);   // top of the bin
    return fmaxf(4.0f, kMaxStretch * median);
}

FS_MESH_HD float longest_edge(float ax, float ay, float bx, float by, float cx, float cy)
{
    const float ab = (bx - ax) * (bx - ax) + (by - ay) * (by - ay);
    const float bc = (cx - bx) * (cx - bx) + (cy - by) * (cy - by);
    const float ca = (ax - cx) * (ax - cx) + (ay - cy) * (ay - cy);
    return sqrtf(fmaxf(ab, fmaxf(bc, ca)));
}

// ---- Drawing ----------------------------------------------------------------
// Pixel coordinates: pixel x covers [x, x + 1), its centre is x + 0.5.
// add(index, value) adds value to pixel index (y * w + x).

// Bilinear splat, the same weights as point mode.
FS_MESH_EXEC_CHECK
template <class Add>
FS_MESH_HD void splat_point(float px, float py, float e, int w, int h, Add& add)
{
    const float lx = px - 0.5f, ly = py - 0.5f;
    if (!(lx > -2.0f && ly > -2.0f && lx < (float)w + 1.0f && ly < (float)h + 1.0f))
        return;
    const int   x0 = (int)floorf(lx), y0 = (int)floorf(ly);
    const float fx = lx - x0, fy = ly - y0;
    const float wts[4] = { (1 - fx) * (1 - fy), fx * (1 - fy), (1 - fx) * fy, fx * fy };
    for (int k = 0; k < 4; ++k)
    {
        const int xi = x0 + (k & 1), yi = y0 + (k >> 1);
        if (xi < 0 || xi >= w || yi < 0 || yi >= h) continue;
        add(yi * w + xi, e * wts[k]);
    }
}

// A triangle to fill at constant density (light per pixel).
struct FillTri { float ax, ay, bx, by, cx, cy, density; };

// Rows of the frame a fill triangle touches, clipped to [0, h).  False when
// none.
FS_MESH_HD bool tri_rows(const FillTri& t, int h, int& y0, int& y1)
{
    const float fy0 = fmaxf(0.0f, floorf(fminf(t.ay, fminf(t.by, t.cy))));
    const float fy1 = fminf((float)(h - 1), ceilf(fmaxf(t.ay, fmaxf(t.by, t.cy))));
    if (!(fy0 <= fy1)) return false;
    y0 = (int)fy0;
    y1 = (int)fy1;
    return true;
}

// The pixels of row y whose centres are inside the triangle (edges
// included), clipped to [0, w): solved from the three edge functions, so
// filling costs the triangle's area rather than its bounding box.
FS_MESH_HD bool row_span(const FillTri& t, int y, int w, int& xs, int& xe)
{
    const float py = y + 0.5f;
    const float area2 = (t.bx - t.ax) * (t.cy - t.ay) - (t.by - t.ay) * (t.cx - t.ax);
    const float sgn = area2 > 0.0f ? 1.0f : -1.0f;
    const float xs_[3] = { t.ax, t.bx, t.cx }, ys_[3] = { t.ay, t.by, t.cy };
    float lo = -1.0e30f, hi = 1.0e30f;
    for (int k = 0; k < 3; ++k)
    {
        const int   n  = k == 2 ? 0 : k + 1;
        const float dx = xs_[n] - xs_[k], dy = ys_[n] - ys_[k];
        // Inside where a * px + b >= 0.
        const float a = -sgn * dy;
        const float b = sgn * (dx * (py - ys_[k]) + dy * xs_[k]);
        if (a > 0.0f)      lo = fmaxf(lo, -b / a);
        else if (a < 0.0f) hi = fminf(hi, -b / a);
        else if (b < 0.0f) return false;
    }
    // px = x + 0.5
    const float fxs = ceilf(fmaxf(lo - 0.5f, -1.0f));
    const float fxe = floorf(fminf(hi - 0.5f, (float)w));
    if (!(fxs <= fxe)) return false;
    xs = fxs < 0.0f ? 0 : (int)fxs;
    xe = fxe > (float)(w - 1) ? w - 1 : (int)fxe;
    return xs <= xe;
}

// Fill a triangle, one row at a time.
FS_MESH_EXEC_CHECK
template <class Add>
FS_MESH_HD void raster_triangle(const FillTri& t, int w, int h, Add& add)
{
    int y0, y1;
    if (!tri_rows(t, h, y0, y1)) return;
    for (int y = y0; y <= y1; ++y)
    {
        int xs, xe;
        if (!row_span(t, y, w, xs, xe)) continue;
        for (int x = xs; x <= xe; ++x) add(y * w + x, t.density);
    }
}

// Spread energy e evenly over triangle abc.  Small, folded or stretched
// triangles are drawn as points through add; the rest go to raster(FillTri)
// to be filled (raster_triangle, or the CUDA kernel's warp-wide fill).
FS_MESH_EXEC_CHECK
template <class Add, class Raster>
FS_MESH_HD void fill_triangle(float ax, float ay, float bx, float by, float cx, float cy,
                              float e, float max_edge, int w, int h, Add& add, Raster& raster)
{
    if (longest_edge(ax, ay, bx, by, cx, cy) > max_edge)
    {
        splat_point(ax, ay, e / 3.0f, w, h, add);
        splat_point(bx, by, e / 3.0f, w, h, add);
        splat_point(cx, cy, e / 3.0f, w, h, add);
        return;
    }
    const float area2 = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax);
    const float area  = 0.5f * fabsf(area2);
    if (area < kMinFillArea)
    {
        // Small, or a thin sliver where the ghost folds over at a caustic:
        // points spread through the triangle, about one per pixel of its
        // longest edge, so a sliver draws a line instead of a dot.
        const float len = longest_edge(ax, ay, bx, by, cx, cy);
        const int n = len <= 1.5f ? 1 : (int)fminf(ceilf(len), 64.0f);
        if (n == 1)
        {
            splat_point((ax + bx + cx) / 3.0f, (ay + by + cy) / 3.0f, e, w, h, add);
            return;
        }
        const float en = e / n;
        for (int t = 0; t < n; ++t)
        {
            const float r1 = sqrtf((t + 0.5f) / n);
            float r2 = (t + 0.5f) * 0.6180340f;
            r2 -= floorf(r2);
            const float wa = 1.0f - r1, wb = r1 * (1.0f - r2), wc = r1 * r2;
            splat_point(wa * ax + wb * bx + wc * cx, wa * ay + wb * by + wc * cy, en, w, h, add);
        }
        return;
    }
    // Entirely off the frame: nothing to fill.
    if (fmaxf(ax, fmaxf(bx, cx)) < 0.0f || fminf(ax, fminf(bx, cx)) > (float)w ||
        fmaxf(ay, fmaxf(by, cy)) < 0.0f || fminf(ay, fminf(by, cy)) > (float)h)
        return;
    const FillTri t = { ax, ay, bx, by, cx, cy, e / area };
    raster(t);
}

// One traced lattice vertex: pixel position and Fresnel weight, w < 0 when
// the ray was lost (vignetted, outside the iris, or off the lens).
struct Vertex { float x, y, w; };

// The two triangles of lattice cell (i, j): corner indices into the
// lattice (row-major, m vertices per side).
FS_MESH_HD void cell_triangles(int i, int j, int m, int tri[2][3])
{
    const int k = j * m + i;
    tri[0][0] = k; tri[0][1] = k + 1;     tri[0][2] = k + m + 1;
    tri[1][0] = k; tri[1][1] = k + m + 1; tri[1][2] = k + m;
}

// Share of the ghost's light for a triangle: its pupil area as a fraction of
// the iris, times the mean ray weight of its corners.
FS_MESH_HD float triangle_share(const float uv[3][2], const float wts[3], float inv_iris_area)
{
    const float a = 0.5f * fabsf((uv[1][0] - uv[0][0]) * (uv[2][1] - uv[0][1]) -
                                 (uv[1][1] - uv[0][1]) * (uv[2][0] - uv[0][0]));
    return a * inv_iris_area * (wts[0] + wts[1] + wts[2]) * (1.0f / 3.0f);
}

// Draw one lattice triangle.  uv: pupil positions of its corners, in_lattice:
// whether each corner is inside the iris (vertex_uv), vx: the traced
// corners.  A triangle with every corner traced is filled.  One cut by a
// lens edge (some corners lost to vignetting) is trimmed to where the light
// stops, found by bisecting its edges with extra rays, so clipped ghost
// edges are smooth instead of stepping along grid cells.
// trace(u, v, Vertex& out) traces one ray and returns false when it is lost.
constexpr int kEdgeSearchSteps = 6;

FS_MESH_EXEC_CHECK
template <class Trace>
FS_MESH_HD void edge_cut(const float* uv_in, const float* uv_out, Trace& trace,
                         float* uv_cut, Vertex& v_cut, const Vertex& v_in)
{
    float lo = 0.0f, hi = 1.0f;
    uv_cut[0] = uv_in[0]; uv_cut[1] = uv_in[1];
    v_cut = v_in;
    for (int s = 0; s < kEdgeSearchSteps; ++s)
    {
        const float t = 0.5f * (lo + hi);
        const float u = uv_in[0] + (uv_out[0] - uv_in[0]) * t;
        const float v = uv_in[1] + (uv_out[1] - uv_in[1]) * t;
        Vertex hit;
        if (trace(u, v, hit))
        {
            lo = t;
            uv_cut[0] = u; uv_cut[1] = v;
            v_cut = hit;
        }
        else
            hi = t;
    }
}

// The lattice edge between vertices ka and kb (row-major, m per side), as
// an index into a per-mesh array of 3 slots per vertex: slot 0 is the edge
// to the right neighbour, 1 the one below, 2 the diagonal (k, k + m + 1).
// These are the only edges cell_triangles makes.
FS_MESH_HD int edge_slot(int ka, int kb, int m)
{
    const int lo = ka < kb ? ka : kb, d = (ka < kb ? kb : ka) - lo;
    return lo * 3 + (d == 1 ? 0 : (d == m ? 1 : 2));
}

// Draw one lattice triangle, with the cut points of its clipped edges from
// cut(qa, qc, uv_cut, v_cut): corner qa traced, corner qc lost, giving the
// pupil position and traced vertex where the light stops along that edge
// (edge_cut, or a lookup of cuts made beforehand).
FS_MESH_EXEC_CHECK
template <class Cut, class Add, class Raster>
FS_MESH_HD void draw_triangle_cut(const float uv[3][2], const bool in_lattice[3],
                                  const Vertex vx[3], float scale, float inv_iris_area,
                                  float max_edge, int w, int h, Cut& cut, Add& add,
                                  Raster& raster)
{
    if (!in_lattice[0] || !in_lattice[1] || !in_lattice[2]) return;
    int ok[3], lost[3], n_ok = 0, n_lost = 0;
    for (int q = 0; q < 3; ++q)
    {
        if (vx[q].w >= 0.0f) ok[n_ok++] = q;
        else lost[n_lost++] = q;
    }
    if (n_ok == 0) return;

    auto fill = [&](const float* ua, const float* ub, const float* uc,
                    const Vertex& a, const Vertex& b, const Vertex& c) {
        const float tuv[3][2] = { { ua[0], ua[1] }, { ub[0], ub[1] }, { uc[0], uc[1] } };
        const float wts[3] = { a.w, b.w, c.w };
        const float e = scale * triangle_share(tuv, wts, inv_iris_area);
        if (e > 0.0f)
            fill_triangle(a.x, a.y, b.x, b.y, c.x, c.y, e, max_edge, w, h, add, raster);
    };

    if (n_ok == 3)
    {
        fill(uv[0], uv[1], uv[2], vx[0], vx[1], vx[2]);
        return;
    }
    if (n_ok == 2)
    {
        // Corners A, B kept, C lost: the quad A, B, cut(B→C), cut(A→C).
        const int a = ok[0], b = ok[1], c = lost[0];
        float ua[2], ub[2];
        Vertex va, vb;
        cut(a, c, ua, va);
        cut(b, c, ub, vb);
        fill(uv[a], uv[b], ub, vx[a], vx[b], vb);
        fill(uv[a], ub, ua, vx[a], vb, va);
        return;
    }
    // Corner A kept, B and C lost: the triangle A, cut(A→B), cut(A→C).
    const int a = ok[0], b = lost[0], c = lost[1];
    float ub[2], uc[2];
    Vertex vb, vc;
    cut(a, b, ub, vb);
    cut(a, c, uc, vc);
    fill(uv[a], ub, uc, vx[a], vb, vc);
}

// draw_triangle_cut bisecting clipped edges on the spot with trace.
FS_MESH_EXEC_CHECK
template <class Trace, class Add, class Raster>
FS_MESH_HD void draw_triangle(const float uv[3][2], const bool in_lattice[3], const Vertex vx[3],
                              float scale, float inv_iris_area, float max_edge,
                              int w, int h, Trace& trace, Add& add, Raster& raster)
{
    auto cut = [&](int qa, int qc, float* uv_cut, Vertex& v_cut) {
        edge_cut(uv[qa], uv[qc], trace, uv_cut, v_cut, vx[qa]);
    };
    draw_triangle_cut(uv, in_lattice, vx, scale, inv_iris_area, max_edge, w, h, cut, add,
                      raster);
}

// draw_triangle filling on the spot with raster_triangle (the CPU path).
FS_MESH_EXEC_CHECK
template <class Trace, class Add>
FS_MESH_HD void draw_triangle(const float uv[3][2], const bool in_lattice[3], const Vertex vx[3],
                              float scale, float inv_iris_area, float max_edge,
                              int w, int h, Trace& trace, Add& add)
{
    auto raster = [&](const FillTri& t) { raster_triangle(t, w, h, add); };
    draw_triangle(uv, in_lattice, vx, scale, inv_iris_area, max_edge, w, h, trace, add, raster);
}

} // namespace ghost_mesh
