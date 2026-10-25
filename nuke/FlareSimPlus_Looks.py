"""
FlareSimPlus_Looks.py — save and apply FlareSim "looks".

A look is a JSON file holding a lens and the knobs that shape the flare:
gain, ray grid, aperture, spectral, highlight, blur and per-surface
overrides.  It does not store anything shot-specific (source position,
threshold, camera FOV), so a look can be applied to any plate.

Looks are read from, in order (an earlier folder wins on a name clash):
  1. ~/.nuke/FlareSim/looks           — your own saved looks
  2. each folder in FLARESIM_LOOKS_PATH — shared studio looks
  3. looks/ next to this file          — starter looks shipped with FlareSim
"""

import json
import os
import re
import sys

import nuke

import FlareSimPlus_Log as _log


LOOK_VERSION = 1

# Node classes that carry a lens_file knob.
FLARESIM_CLASSES = ('FlareSimPlus', 'FlareSimPlus3D')

_HERE = os.path.dirname(os.path.abspath(__file__))
_PLUGIN_EXT = {'win32': '.dll', 'darwin': '.dylib'}.get(sys.platform, '.so')


def plugin_dirs():
    """Folders holding the FlareSimPlus plugin, best first.

    Usually just the folder of this file, but a facility install may keep
    the .py files in nuke/ and the compiled files in a per-version folder
    below it (nuke/15.1/EL9.x86_64/), like the original FlareSim does.
    The loaded plugin wins, then plugins found on the plugin path, with
    ones inside this install ahead of any other copy."""
    want = 'FlareSimPlus' + _PLUGIN_EXT
    found = []
    for flags in ((), (nuke.ALL,)):
        try:
            paths = nuke.plugins(*flags)
        except Exception:
            paths = []
        for p in paths:
            if os.path.basename(p) == want:
                d = os.path.dirname(os.path.abspath(p))
                if d not in found:
                    found.append(d)
    here = os.path.normcase(_HERE) + os.sep
    found.sort(key=lambda d: not (os.path.normcase(d) + os.sep).startswith(here))
    return found


def install_path(name):
    """name next to this file, or else next to the plugin; the first that
    exists, or the path next to this file when neither does."""
    for d in [_HERE] + plugin_dirs():
        path = os.path.join(d, name)
        if os.path.exists(path):
            return path
    return os.path.join(_HERE, name)


BUNDLED_LOOKS_DIR = install_path('looks')
BUNDLED_LENS_ROOT = install_path('lenses')
USER_LOOKS_DIR = os.path.join(os.path.expanduser('~'), '.nuke', 'FlareSim', 'looks')
USER_LENS_DIR = os.path.join(os.path.expanduser('~'), '.nuke', 'FlareSim', 'lenses')

# Knobs a look stores.  Shot-specific knobs (source mode, Source XY,
# threshold, camera/FOV, seeds) are deliberately left out.
LOOK_KNOBS = (
    'flare_gain',
    'res_independent',
    'res_ref_width',
    'pupil_jitter',
    'aperture_blades',
    'aperture_rotation',
    'spectral_samples',
    'spectral_jitter',
    'spectral_jitter_scale',
    'highlight_compress',
    'highlight_metric',
    'highlight_clip',
    'highlight_knee',
    'ghost_blur',
    'ghost_blur_passes',
    'haze_enable',
    'haze_gain',
    'haze_radius',
    'haze_passes',
    'haze_shape',
    'haze_tint',
    'haze_aspect',
    'haze_core_tint',
    'starburst_enable',
    'starburst_gain',
    'starburst_scale',
    'starburst_tint',
    'starburst_fringe',
    'starburst_softness',
    'starburst_reach',
    'rays_gain',
    'rays_count',
    'rays_length',
    'rays_width',
    'rays_spread',
    'rays_angle',
    'rays_seed',
    'rays_tint',
    'ring_gain',
    'ring_radius',
    'ring_aspect',
    'ring_width',
    'ring_rainbow',
    'ring_arc',
    'ring_angle',
    'ring_tint',
    'ring_streaks',
    'ring_streak_count',
    'ring_streak_width',
    'ring_jitter',
    'dirt_gain',
    'dirt_spread',
    'dirt_softness',
    'dirt_scatter',
    'dirt_ghosts',
)

# Render settings a look no longer carries.  Older look files still list
# them; applying a look leaves the node's Quality alone.
SKIPPED_KNOBS = ('ray_grid', 'viewer_quality', 'render_full')

# Knobs added after looks were first saved, with their defaults.  A look
# that doesn't list one puts it back to the default, so an older look still
# looks the way it did when it was saved.
LATER_KNOB_DEFAULTS = {
    'haze_shape': 0.0,
    'haze_tint': [1.0, 1.0, 1.0],
    'starburst_tint': [1.0, 1.0, 1.0],
    'starburst_fringe': 1.0,
    'starburst_softness': 0.0,
    'haze_aspect': 1.0,
    'haze_core_tint': [1.0, 1.0, 1.0],
    'starburst_reach': 0.0,
    'rays_gain': 0.0,
    'rays_count': 48,
    'rays_length': 0.25,
    'rays_width': 0.3,
    'rays_spread': 360.0,
    'rays_angle': -90.0,
    'rays_seed': 1,
    'rays_tint': [1.0, 1.0, 1.0],
    'ring_gain': 0.0,
    'ring_radius': 0.1,
    'ring_aspect': 1.0,
    'ring_width': 0.08,
    'ring_rainbow': 1.0,
    'ring_arc': 360.0,
    'ring_angle': -90.0,
    'ring_tint': [1.0, 1.0, 1.0],
    'ring_streaks': 0.0,
    'ring_streak_count': 120,
    'ring_streak_width': 0.4,
    'ring_jitter': 0.0,
}

# Per-surface override knobs (Surfaces tab) and their defaults.
MAX_SURFS = 50
SURF_KNOBS = (
    ('surf_%d', True),
    ('surf_gain_%d', 1.0),
    ('surf_color_%d', [1.0, 1.0, 1.0]),
    ('surf_offx_%d', 0.0),
    ('surf_offy_%d', 0.0),
    ('surf_scale_%d', 1.0),
)

# Settings for single ghosts (Surfaces tab > Ghost Overrides), as text.
GHOST_KNOB = 'ghost_overrides'
GHOST_DEFAULT = {'on': True, 'gain': 1.0, 'color': (1.0, 1.0, 1.0),
                 'scale': 1.0, 'offx': 0.0, 'offy': 0.0,
                 # scale up (None = the same as across), own blur (None =
                 # the node's Ghost Blur), streak (gain, length, thickness,
                 # edge) with its colour.
                 'scale_y': None, 'blur': None, 'streak': None,
                 'streak_color': (1.0, 1.0, 1.0)}
# Streak length and thickness when the text leaves them out (as the node).
STREAK_LENGTH = 0.15
STREAK_THICKNESS = 0.001
STREAK_EDGE = 2.0


def _ghost_floats(text, count):
    parts = text.split(',')
    if not 1 <= len(parts) <= count:
        return None
    try:
        values = [float(p) for p in parts]
    except ValueError:
        return None
    return values if all(v == v for v in values) else None


def parse_ghost_overrides(text):
    """{(a, b): settings} from Ghost Overrides text (the rules of the
    node's ghost_overrides.h).  Lines that can't be read are skipped."""
    ghosts = {}
    for line in (text or '').replace(';', '\n').splitlines():
        words = line.split()
        if not words or words[0].startswith('#'):
            continue
        name = words[0]
        sep = next((i for i, ch in enumerate(name) if ch in '-+/'), -1)
        try:
            a, b = int(name[:sep]), int(name[sep + 1:])
        except ValueError:
            continue
        if sep <= 0 or a < 0 or b < 0 or a == b:
            continue
        g = dict(GHOST_DEFAULT)
        ok = True
        for word in (w.lower() for w in words[1:]):
            key, eq, val = word.partition('=')
            if not eq:
                if word in ('on', 'off'):
                    g['on'] = word == 'on'
                else:
                    ok = False
                continue
            colour_keys = ('color', 'colour', 'tint')
            streak_colour_keys = ('streak_color', 'streak_colour', 'streak_tint')
            v = _ghost_floats(val, 4 if key == 'streak' else
                              3 if key in colour_keys + streak_colour_keys else
                              2 if key in ('offset', 'scale') else 1)
            if v is None:
                ok = False
            elif key == 'gain' and len(v) == 1 and v[0] >= 0.0:
                g['gain'] = v[0]
            elif key in colour_keys and len(v) in (1, 3):
                g['color'] = tuple(max(c, 0.0) for c in (v * 3 if len(v) == 1 else v))
            elif key == 'scale' and all(c > 0.0 for c in v):
                g['scale'] = v[0]
                g['scale_y'] = v[1] if len(v) == 2 else None
            elif key == 'offset' and len(v) == 2:
                g['offx'], g['offy'] = v
            elif key == 'blur' and len(v) == 1 and v[0] >= 0.0:
                g['blur'] = v[0]
            elif (key == 'streak' and v[0] >= 0.0 and all(c > 0.0 for c in v[1:3])
                  and (len(v) < 4 or 1.0 <= v[3] <= 64.0)):
                g['streak'] = (v[0],
                               v[1] if len(v) > 1 else STREAK_LENGTH,
                               v[2] if len(v) > 2 else STREAK_THICKNESS,
                               v[3] if len(v) > 3 else STREAK_EDGE)
            elif key in streak_colour_keys and len(v) in (1, 3):
                g['streak_color'] = tuple(max(c, 0.0) for c in (v * 3 if len(v) == 1 else v))
            else:
                ok = False
        if ok:
            ghosts[(min(a, b), max(a, b))] = g
    return ghosts


def _has_streak(g):
    return bool(g.get('streak')) and g['streak'][0] > 0.0


def ghost_changed(g):
    """True when a ghost's settings differ from the defaults."""
    return (not g['on'] or abs(g['gain'] - 1.0) > 1e-6 or abs(g['scale'] - 1.0) > 1e-6
            or abs(g['offx']) > 1e-6 or abs(g['offy']) > 1e-6
            or any(abs(c - 1.0) > 1e-6 for c in g['color'])
            or g.get('scale_y') is not None or g.get('blur') is not None
            or _has_streak(g))


def _num(v):
    return ('%.4f' % v).rstrip('0').rstrip('.') or '0'


def _num6(v):
    # Small fractions of the diagonal (blur, streak thickness) need more places.
    return ('%.6f' % v).rstrip('0').rstrip('.') or '0'


def format_ghost_overrides(ghosts):
    """Ghost Overrides text for {(a, b): settings}, one ghost per line,
    leaving out ghosts and settings at their defaults."""
    lines = []
    for (a, b) in sorted(ghosts):
        g = ghosts[(a, b)]
        if not ghost_changed(g):
            continue
        words = ['%d-%d' % (a, b)]
        if abs(g['gain'] - 1.0) > 1e-6:
            words.append('gain=' + _num(g['gain']))
        if any(abs(c - 1.0) > 1e-6 for c in g['color']):
            words.append('color=' + ','.join(_num(c) for c in g['color']))
        if g.get('scale_y') is not None:
            words.append('scale=%s,%s' % (_num(g['scale']), _num(g['scale_y'])))
        elif abs(g['scale'] - 1.0) > 1e-6:
            words.append('scale=' + _num(g['scale']))
        if abs(g['offx']) > 1e-6 or abs(g['offy']) > 1e-6:
            words.append('offset=%s,%s' % (_num(g['offx']), _num(g['offy'])))
        if g.get('blur') is not None:
            words.append('blur=' + _num6(g['blur']))
        if _has_streak(g):
            gain, length, thick = g['streak'][:3]
            edge = g['streak'][3] if len(g['streak']) > 3 else STREAK_EDGE
            words.append('streak=%s,%s,%s' % (_num(gain), _num6(length), _num6(thick))
                         + (',' + _num(edge) if abs(edge - STREAK_EDGE) > 1e-6 else ''))
            colour = g.get('streak_color') or (1.0, 1.0, 1.0)
            if any(abs(c - 1.0) > 1e-6 for c in colour):
                words.append('streak_color=' + ','.join(_num(c) for c in colour))
        if not g['on']:
            words.append('off')
        lines.append(' '.join(words))
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Knob value helpers
# ---------------------------------------------------------------------------

def _knob_value(knob):
    """Current value of a knob as a JSON-friendly value."""
    cls = knob.Class()
    if cls == 'Enumeration_Knob':
        return int(knob.getValue())
    if cls == 'Boolean_Knob':
        return bool(knob.value())
    if cls == 'Int_Knob':
        return int(knob.value())
    if 'String' in cls:
        return str(knob.value())
    size = knob.arraySize() if hasattr(knob, 'arraySize') else 1
    if size > 1:
        return [float(knob.getValue(i)) for i in range(size)]
    return float(knob.getValue())


def _set_knob_value(knob, value):
    """Set a knob from a value written by _knob_value()."""
    if hasattr(knob, 'isAnimated') and knob.isAnimated():
        knob.clearAnimated()
    if isinstance(value, list):
        for i, v in enumerate(value):
            knob.setValue(v, i)
    else:
        knob.setValue(value)


def _same(a, b):
    if isinstance(a, list) or isinstance(b, list):
        a = a if isinstance(a, list) else [a]
        b = b if isinstance(b, list) else [b]
        if len(a) != len(b):
            # A colour knob may report a single value when all channels match.
            a, b = (a * len(b), b) if len(a) == 1 else (a, b * len(a))
        return all(abs(float(x) - float(y)) < 1e-6 for x, y in zip(a, b))
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    if isinstance(a, str) or isinstance(b, str):
        return str(a).strip() == str(b).strip()
    return abs(float(a) - float(b)) < 1e-6


# ---------------------------------------------------------------------------
# Lens paths
# ---------------------------------------------------------------------------

def lens_to_look(path):
    """Store bundled lenses relative to the lens library, others as-is."""
    if not path:
        return ''
    norm = os.path.normcase(os.path.abspath(path))
    root = os.path.normcase(os.path.abspath(BUNDLED_LENS_ROOT))
    if norm.startswith(root + os.sep):
        return os.path.relpath(os.path.abspath(path), BUNDLED_LENS_ROOT).replace('\\', '/')
    return path.replace('\\', '/')


def lens_search_roots():
    """Lens folders on this machine, in the order the node searches them:
    FLARESIM_LENS_PATH, the bundled library, then imported lenses."""
    roots = [d.strip() for d in os.environ.get('FLARESIM_LENS_PATH', '').split(os.pathsep)
             if d.strip()]
    roots.append(BUNDLED_LENS_ROOT)
    roots.append(USER_LENS_DIR)
    return roots


def _find_by_name(root, name, ignore_case):
    want = name.lower() if ignore_case else name
    for folder, _dirs, files in os.walk(root):
        for f in files:
            if (f.lower() if ignore_case else f) == want:
                return os.path.join(folder, f).replace('\\', '/')
    return ''


_resolved = {}


def resolve_lens(value):
    """Turn a stored lens path (a node's lens_file or a look's lens entry)
    into a path on this machine, or '' if missing.

    Same search as the node (src/lens_path.cpp), so scripts from Windows,
    moved installs and lenses imported on another machine still find the
    lens when the same file is in one of lens_search_roots()."""
    if not value:
        return ''
    if os.path.isfile(value):
        return value.replace('\\', '/')
    hit = _resolved.get(value)
    if hit and os.path.isfile(hit):
        return hit
    found = _search_lens(value)
    if found:
        _resolved[value] = found
        _log.debug('lens', '%s found at %s', value, found)
    else:
        _log.debug('lens', '%s not found in %s', value, lens_search_roots())
    return found


def _search_lens(value):
    norm = value.replace('\\', '/')
    roots = lens_search_roots()
    pos = norm.lower().rfind('/lenses/')
    if pos >= 0:
        tail = norm[pos + len('/lenses/'):]
        for root in roots:
            candidate = os.path.join(root, tail)
            if os.path.isfile(candidate):
                return candidate.replace('\\', '/')
    if not os.path.isabs(norm) and ':' not in norm:
        for root in roots:
            candidate = os.path.join(root, norm)
            if os.path.isfile(candidate):
                return candidate.replace('\\', '/')
    name = norm.rsplit('/', 1)[-1]
    if not name:
        return ''
    for ignore_case in (False, True):
        for root in roots:
            if os.path.isdir(root):
                found = _find_by_name(root, name, ignore_case)
                if found:
                    return found
    return ''


# ---------------------------------------------------------------------------
# Camera input
# ---------------------------------------------------------------------------

# The input a Camera connects to, per node class.
CAMERA_INPUT = {'FlareSimPlus': 2, 'FlareSimPlus3D': 1}
# Nodes that only pass their input on; followed to the node behind them.
PASSTHROUGH_CLASSES = ('Dot', 'NoOp')


def upstream(node, index):
    """The node on input `index`, followed through Dot and NoOp nodes."""
    n = node.input(index)
    for _ in range(64):
        if n is None or n.Class() not in PASSTHROUGH_CLASSES:
            return n
        n = n.input(0)
    return None


def camera_node(node):
    """The Camera the node takes its field of view from, or None (no camera
    connected, or Use Camera turned off)."""
    try:
        index = CAMERA_INPUT.get(node.Class())
        if index is None:
            return None
        knobs = node.knobs()
        if 'use_camera' in knobs and not knobs['use_camera'].value():
            return None
        cam = upstream(node, index)
        return cam if cam is not None and 'focal' in cam.knobs() else None
    except Exception:
        return None


def camera_values(node):
    """(camera name, focal, horizontal aperture, vertical aperture) in mm
    at the current frame, or None."""
    cam = camera_node(node)
    if cam is None:
        return None
    try:
        k = cam.knobs()
        hap = float(k['haperture'].value()) if 'haperture' in k else 0.0
        vap = float(k['vaperture'].value()) if 'vaperture' in k else 0.0
        return cam.name(), float(k['focal'].value()), hap, vap
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Finding looks
# ---------------------------------------------------------------------------

def look_dirs():
    """Folders searched for looks, highest priority first, with a label."""
    dirs = [(USER_LOOKS_DIR, 'Mine')]
    for d in os.environ.get('FLARESIM_LOOKS_PATH', '').split(os.pathsep):
        if d.strip():
            dirs.append((d.strip(), 'Studio'))
    dirs.append((BUNDLED_LOOKS_DIR, 'Starter'))
    return dirs


def read_look(path):
    with open(path, encoding='utf-8') as fh:
        look = json.load(fh)
    look.setdefault('name', os.path.splitext(os.path.basename(path))[0])
    look['_path'] = path
    return look


def list_looks():
    """All looks found, sorted by name.  Each is a dict with '_source' set."""
    looks = {}
    for folder, source in look_dirs():
        if not os.path.isdir(folder):
            continue
        for fname in sorted(os.listdir(folder)):
            if not fname.lower().endswith('.json'):
                continue
            try:
                look = read_look(os.path.join(folder, fname))
            except (OSError, ValueError) as e:
                nuke.warning('FlareSim: skipping look %s: %s' % (fname, e))
                continue
            key = look['name'].lower()
            if key in looks:
                continue  # a higher-priority folder already has this name
            look['_source'] = source
            looks[key] = look
    return sorted(looks.values(), key=lambda l: l['name'].lower())


# ---------------------------------------------------------------------------
# Capture / save / apply
# ---------------------------------------------------------------------------

def capture_look(node, name, description=''):
    """Build a look dict from a FlareSim node's current knob values."""
    knobs = node.knobs()
    look = {
        'flaresim_look_version': LOOK_VERSION,
        'name': name,
        'description': description,
        'lens': lens_to_look(node['lens_file'].value()),
        'knobs': {},
    }
    for k in LOOK_KNOBS:
        if k in knobs:
            look['knobs'][k] = _knob_value(knobs[k])
    # Per-surface overrides: only those changed from their defaults.
    for i in range(MAX_SURFS):
        for pattern, default in SURF_KNOBS:
            k = pattern % i
            if k in knobs:
                value = _knob_value(knobs[k])
                if not _same(value, default):
                    look['knobs'][k] = value
    if GHOST_KNOB in knobs:
        text = str(knobs[GHOST_KNOB].value()).strip()
        if text:
            look['knobs'][GHOST_KNOB] = text
    return look


def _safe_filename(name):
    stem = re.sub(r'[^A-Za-z0-9._ -]+', '_', name).strip(' ._')
    return (stem or 'look') + '.json'


def write_look(look, folder=None, overwrite=False):
    """Write a look dict to folder (default: your own looks folder).

    Returns the path written.  Raises FileExistsError when the file exists
    and overwrite is False.
    """
    folder = folder or USER_LOOKS_DIR
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, _safe_filename(look['name']))
    if os.path.exists(path) and not overwrite:
        raise FileExistsError(path)
    data = {k: v for k, v in look.items() if not k.startswith('_')}
    data.setdefault('flaresim_look_version', LOOK_VERSION)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=2)
        fh.write('\n')
    return path


def can_delete(look):
    """Only your own looks can be deleted; studio and starter looks are
    shared or shipped with FlareSim."""
    path = look.get('_path', '')
    return bool(path) and os.path.normcase(os.path.dirname(os.path.abspath(path))) == \
        os.path.normcase(os.path.abspath(USER_LOOKS_DIR))


def delete_look(look):
    """Delete one of your own looks from disk.  Raises ValueError for a
    studio or starter look, OSError when the file cannot be removed."""
    if not can_delete(look):
        raise ValueError('Only your own looks can be deleted: %s' % look.get('name', ''))
    os.remove(look['_path'])


def save_look(node, name, description='', folder=None, overwrite=False):
    """Save the node's look to folder (default: your own looks folder).

    Returns the path written.  Raises FileExistsError when the file exists
    and overwrite is False.
    """
    return write_look(capture_look(node, name, description), folder, overwrite)


def apply_look(look, nodes, reset_surfaces=True):
    """Apply a look to FlareSim / FlareSim3D nodes.

    With reset_surfaces, per-surface overrides not in the look go back to
    their defaults, so the look fully defines them.

    Returns a list of warnings (e.g. a lens file that could not be found).
    """
    warnings = []
    lens = resolve_lens(look.get('lens', ''))
    if look.get('lens') and not lens:
        warnings.append('Lens not found: %s' % look['lens'])

    values = look.get('knobs', {})
    for node in nodes:
        knobs = node.knobs()
        undo = nuke.Undo()
        undo.begin('Apply FlareSim look')
        try:
            if lens:
                knobs['lens_file'].setValue(lens)
            # Reset surface overrides first so the look fully defines them.
            for i in range(MAX_SURFS if reset_surfaces else 0):
                for pattern, default in SURF_KNOBS:
                    k = pattern % i
                    if k in knobs:
                        _set_knob_value(knobs[k], default)
            if reset_surfaces and GHOST_KNOB in knobs:
                knobs[GHOST_KNOB].setValue('')
            for k, default in LATER_KNOB_DEFAULTS.items():
                if k in knobs and k not in values:
                    _set_knob_value(knobs[k], default)
            for k, v in values.items():
                if k in knobs and k not in SKIPPED_KNOBS:
                    _set_knob_value(knobs[k], v)
        finally:
            undo.end()
    return warnings


# ---------------------------------------------------------------------------
# Node selection
# ---------------------------------------------------------------------------

def target_nodes():
    """Selected FlareSim nodes, or the only one in the script.

    Shows a message and returns [] when there is no clear target.
    """
    nodes = [n for n in nuke.selectedNodes() if n.Class() in FLARESIM_CLASSES]
    if nodes:
        return nodes
    all_fs = [n for n in nuke.allNodes() if n.Class() in FLARESIM_CLASSES]
    if len(all_fs) == 1:
        return all_fs
    if all_fs:
        nuke.message('Multiple FlareSim nodes exist but none are selected.\n'
                     'Select the node(s) you want to update and try again.')
    else:
        nuke.message('No FlareSim node found in the script.')
    return []
