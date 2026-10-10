"""
FlareSim_Looks.py — save and apply FlareSim "looks".

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

import nuke


LOOK_VERSION = 1

# Node classes that carry a lens_file knob.
FLARESIM_CLASSES = ('FlareSim', 'FlareSim3D')

_HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLED_LOOKS_DIR = os.path.join(_HERE, 'looks')
BUNDLED_LENS_ROOT = os.path.join(_HERE, 'lenses')
USER_LOOKS_DIR = os.path.join(os.path.expanduser('~'), '.nuke', 'FlareSim', 'looks')
USER_LENS_DIR = os.path.join(os.path.expanduser('~'), '.nuke', 'FlareSim', 'lenses')

# Knobs a look stores.  Shot-specific knobs (source mode, Source XY,
# threshold, camera/FOV, seeds) are deliberately left out.
LOOK_KNOBS = (
    'flare_gain',
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
)

# Render settings a look no longer carries.  Older look files still list
# them; applying a look leaves the node's Quality alone.
SKIPPED_KNOBS = ('ray_grid',)

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
