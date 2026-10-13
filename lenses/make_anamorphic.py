#!/usr/bin/env python3
"""
make_anamorphic.py - Turn a spherical .lens into a front-anamorphic one.

Puts a Galilean cylindrical afocal attachment in front of the lens, the
way Iscorama-style adapters and most classic scope lenses (Hawk, the
Bausch & Lomb CinemaScope attachments) are built:

    negative cylinder  ->  air gap  ->  positive cylinder  ->  taking lens

Both cylinders are `cyl_y` (axis vertical), so they only bend light in the
horizontal plane: the lens sees a horizontal field S times wider than its
own, and the picture is squeezed S times horizontally.  The vertical axis
is untouched.  The cylinders' reflections give the horizontal anamorphic
streak.

The air gap is solved with a paraxial trace so the attachment is exactly
afocal: the taking lens stays focused at infinity and its focal length is
unchanged vertically.

Usage:
    python3 make_anamorphic.py BASE.lens --squeeze 2 -o OUT.lens
    python3 make_anamorphic.py BASE.lens --squeeze 1.33 --coating 0 -o OUT.lens

Options:
    --squeeze S        squeeze ratio (1.33, 1.5, 1.8, 2 ...)
    --coating N        AR layers on the attachment's surfaces (0 = uncoated,
                       the default: bright anamorphic streak; 1 = single
                       coated, a subtler streak)
    --glass NAME       bk7 (default, clean) or sf6 (low Abbe, rainbow ghosts)
    --name TEXT        lens name (default: "<base name> Anamorphic <S>x")
"""

import argparse
import math
import os
import re
import sys

GLASSES = {
    'bk7': (1.51680, 64.2),    # crown, little colour in the ghosts
    'sf6': (1.80518, 25.4),    # heavy flint, strong rainbow separation
}


def read_lens(path):
    """(name, focal_length, surface lines, rows) of a .lens file.  Rows are
    (radius, thickness, ior, abbe, semi_ap, rest) for the paraxial check;
    radius is None for the stop and 0 for flat."""
    name, focal, lines, rows = '', 0.0, [], []
    in_surfaces = False
    with open(path, encoding='utf-8', errors='replace') as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith('#'):
                if in_surfaces and line:
                    lines.append(raw.rstrip('\n'))
                continue
            if not in_surfaces:
                if line.startswith('name:'):
                    name = line[5:].strip()
                elif line.startswith('focal_length:'):
                    focal = float(line[13:].split()[0])
                elif line.startswith('surfaces:'):
                    in_surfaces = True
                continue
            tokens = line.split('#')[0].split()
            if len(tokens) < 6:
                continue
            r = tokens[0]
            if r.lower() == 'stop':
                radius = None
            elif r.lower() == 'inf':
                radius = 0.0
            else:
                radius = float(r)
            kind = tokens[6] if len(tokens) > 6 else 'sph'
            rows.append((radius, float(tokens[1]), float(tokens[2]), float(tokens[3]),
                         float(tokens[4]), kind))
            lines.append(raw.rstrip('\n'))
    if not rows:
        raise ValueError('%s: no surfaces' % path)
    return name, focal, lines, rows


def curvature_x(radius, kind):
    """Curvature in the horizontal plane (the plane the cylinders bend)."""
    if radius is None or radius == 0.0:
        return 0.0
    if kind in ('cyl_x', 'cylinder_x'):
        return 0.0               # curves in YZ only
    return 1.0 / radius


def paraxial(rows, axis_kind=curvature_x, y0=1.0, u0=0.0):
    """Trace a paraxial ray (height y, angle u) through the rows; returns
    the (y, u) after the last surface, before its thickness."""
    y, u, n = y0, u0, 1.0
    for i, (radius, t, ior, _abbe, _sa, kind) in enumerate(rows):
        c = axis_kind(radius, kind)
        n2 = ior
        # refraction: n2 u2 = n u - y c (n2 - n)
        u = (n * u - y * c * (n2 - n)) / n2
        n = n2
        if i < len(rows) - 1:
            y = y + t * u
    return y, u


def block(squeeze, front_sa, glass, coating):
    """The attachment's four surface rows, gap solved for afocality.

    Element 1: plano-concave, curved front (cyl_y), flat back.
    Element 2: plano-convex, flat front, curved back (cyl_y).
    Focal lengths F1 = -k, F2 = S k (thin-lens), sized so the curved
    surfaces keep their semi-aperture under 0.65 |R|."""
    n, abbe = glass
    # The rear element must pass the taking lens's front aperture; the
    # front element sees a beam S times narrower (Galilean, used reversed).
    rear_sa = max(1.15 * front_sa, 8.0)
    front_sa_blk = max(rear_sa / squeeze * 1.6, 8.0)
    # |R| = (n - 1) |F|; need sa <= 0.65 |R| on both curved surfaces.
    k = max(front_sa_blk / (0.65 * (n - 1)),
            rear_sa / (0.65 * (n - 1) * squeeze))
    k = math.ceil(k)
    r1 = -(n - 1) * k                 # plano-concave front: F = R/(n-1) = -k
    r2 = -(n - 1) * squeeze * k       # plano-convex back:  F = -R/(n-1) = S k

    def sag(r, h):
        return abs(r) - math.sqrt(r * r - h * h)

    t1 = 3.0                                          # thin centre, edge = t1 + sag
    t2 = round(2.5 + sag(r2, rear_sa), 2)             # centre thick enough for the edge

    def rows_for(gap):
        return [
            (r1, t1, n, abbe, front_sa_blk, 'cyl_y'),
            (0.0, gap, 1.0, 0.0, front_sa_blk, 'sph'),
            (0.0, t2, n, abbe, rear_sa, 'sph'),
            (r2, 0.0, 1.0, 0.0, rear_sa, 'cyl_y'),
        ]

    # The exit angle of a ray entering parallel is linear in the gap: solve
    # for zero (afocal) from two traces.
    g0, g1 = 10.0, 200.0
    u0 = paraxial(rows_for(g0))[1]
    u1 = paraxial(rows_for(g1))[1]
    gap = g0 - u0 * (g1 - g0) / (u1 - u0)
    if gap <= 1.0:
        raise ValueError('squeeze %.2f: no room for the air gap' % squeeze)
    rows = rows_for(round(gap, 3))
    # Angular magnification: a ray entering at angle u leaves at m u.
    _y, u_out = paraxial(rows, y0=0.0, u0=1e-3)
    mag = u_out / 1e-3
    lines = [
        '# --- Front anamorphic attachment: %.2gx squeeze, cylinder axes vertical ---' % squeeze,
        '  %-10.3f %-10.3f %-8.5f %-6.1f %-9.2f %-8d cyl_y     # plano-concave cylinder, F = -%d'
        % (r1, t1, n, abbe, front_sa_blk, coating, k),
        '  %-10s %-10.3f %-8.5f %-6.1f %-9.2f %-8d           # flat back, air gap (solved: afocal)'
        % ('0', round(gap, 3), 1.0, 0.0, front_sa_blk, coating),
        '  %-10s %-10.3f %-8.5f %-6.1f %-9.2f %-8d           # flat front of the positive cylinder'
        % ('0', t2, n, abbe, rear_sa, coating),
        '  %-10.3f %-10.3f %-8.5f %-6.1f %-9.2f %-8d cyl_y     # plano-convex cylinder, F = +%d'
        % (r2, 15.0, 1.0, 0.0, rear_sa, coating, round(squeeze * k)),
        '# --- Taking lens ---',
    ]
    return lines, mag, gap


def make(base_path, squeeze, out_path, coating=0, glass='bk7', name=None):
    base_name, focal, base_lines, rows = read_lens(base_path)
    # Many converted prescriptions carry the clear diameter, not the
    # radius, in semi_ap; never size the attachment past what the first
    # curved surface can physically have.
    r0, sa0 = rows[0][0], rows[0][4]
    front_sa = min(sa0, 0.8 * abs(r0)) if r0 else sa0
    lines, mag, gap = block(squeeze, front_sa, GLASSES[glass], coating)
    if abs(abs(1.0 / mag) - squeeze) > 0.02 * squeeze:
        raise ValueError('squeeze came out %.3f, wanted %.3f' % (1.0 / abs(mag), squeeze))
    label = '%g' % squeeze
    name = name or '%s Anamorphic %sx' % (base_name or os.path.splitext(
        os.path.basename(base_path))[0], label)
    out = [
        '# %s' % name,
        '#',
        '# Front anamorphic made by lenses/make_anamorphic.py from',
        '#   %s' % os.path.basename(base_path),
        '# A Galilean cylindrical attachment (%s glass, coating %d) in front of the'
        % (glass.upper(), coating),
        '# original lens.  Horizontal field %.3gx wider (squeeze %.3fx paraxial), vertical'
        % (squeeze, 1.0 / abs(mag)),
        '# unchanged; focal_length below is the vertical (taking lens) focal length.',
        '# Not a real product: a plausible design for flare work, not a prescription.',
        '',
        'name: %s' % name,
        'focal_length: %g' % focal,
        '',
        'surfaces:',
        '# radius     thickness   ior      abbe   semi_ap   coating  type',
    ] + lines + base_lines
    with open(out_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(out) + '\n')
    return name, 1.0 / abs(mag), gap


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('base')
    ap.add_argument('--squeeze', type=float, required=True)
    ap.add_argument('--coating', type=int, default=0)
    ap.add_argument('--glass', choices=sorted(GLASSES), default='bk7')
    ap.add_argument('--name')
    ap.add_argument('-o', '--output', required=True)
    a = ap.parse_args(argv)
    name, s, gap = make(a.base, a.squeeze, a.output, a.coating, a.glass, a.name)
    print('%s: squeeze %.3fx, air gap %.1f mm -> %s' % (name, s, gap, a.output))


if __name__ == '__main__':
    sys.exit(main())
