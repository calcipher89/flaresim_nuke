"""
FlareSim_LensBrowser.py — the FlareSim Lens Browser window.

A standalone Qt window for picking a lens and building a flare look:

  * lens thumbnails under the preview, each a small render of that lens's
    flare, with search, maker, focal length, speed and type filters; drag
    the divider to go from a one-row carousel to a grid
  * a live flare preview: drag the light around to see the ghosts move
  * look controls (gain, aperture, ghost blur) that drive the preview
  * a Lens Elements tab: a side view of the lens, per-surface controls
    (the node's Surfaces tab knobs) and Shift+click ghost picking
  * start from a saved look, apply the result to a FlareSim node, or save it
    as a new look

Open it with the **Lens Browser** button on a FlareSim / FlareSim3D node, or
from Window > FlareSim Lens Browser.

The preview is rendered by the flaresim_preview library that is built and
installed next to the plugins.  Without it the window still works, minus the
preview and thumbnails.  Thumbnails are cached in ~/.nuke/FlareSim/thumbnails.
"""

import ctypes
import hashlib
import math
import os
import re
import shutil
import sys
import threading
import time

import nuke

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets

import FlareSim_Looks

# QShortcut lives in QtWidgets in Qt 5 and in QtGui in Qt 6.
QShortcut = getattr(QtWidgets, 'QShortcut', None) or QtGui.QShortcut


FLARESIM_CLASSES = FlareSim_Looks.FLARESIM_CLASSES

_HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLED_LENS_ROOT = os.path.join(_HERE, 'lenses')
USER_LENS_DIR = os.path.join(os.path.expanduser('~'), '.nuke', 'FlareSim', 'lenses')


# ---------------------------------------------------------------------------
# Lens library
# ---------------------------------------------------------------------------

# File-name prefixes that are typos or sub-brands of a maker.
_MAKER_ALIASES = {
    'ai': 'Nikon', 'af': 'Nikon', 'af-s': 'Nikon', 'nikkor': 'Nikon',
    'nikkor-n': 'Nikon', 'nikkor-s': 'Nikon', 'nikkor-h': 'Nikon',
    'w-nikkor': 'Nikon', 'medical-nikkor': 'Nikon',
    'zhong': 'Zhong Yi', 'fujifim': 'Fujifilm', 'simga': 'Sigma',
    'sasmung': 'Samsung', 'konina': 'Konica', 'venux': 'Venus',
    'rodenstein': 'Rodenstock', 'phase': 'Phase One', 'arri': 'Zeiss',
    'anamorphic': 'Other',
}

_CINE_RE = re.compile(r'\bT\d|cine|master.prime|ultra.prime|summilux.c\b|arriflex|'
                      r'kinoptik|angenieux|cooke', re.I)

FOCAL_RANGES = [
    ('Any focal length', 0, 1e9),
    ('Ultra wide (< 24mm)', 0, 24),
    ('Wide (24-35mm)', 24, 35.5),
    ('Normal (35-70mm)', 35.5, 70.5),
    ('Portrait (70-135mm)', 70.5, 135.5),
    ('Telephoto (> 135mm)', 135.5, 1e9),
]

SPEEDS = [
    ('Any speed', 0, 1e9),
    ('f/1.4 and faster', 0, 1.45),
    ('f/2 and faster', 0, 2.05),
    ('Slower than f/2', 2.05, 1e9),
]

TYPES = ['Any type', 'Cine', 'Stills', 'Anamorphic']
# Where a lens came from: the lenses shipped with FlareSim, ones you
# imported, or a studio folder in FLARESIM_LENS_PATH.
LIBRARIES = [('All lenses', ''), ('Bundled', 'Bundled'), ('Mine (imported)', 'Mine'),
             ('Studio', 'Studio')]


class LensInfo(object):
    __slots__ = ('path', 'name', 'maker', 'focal', 'fnum', 'kind', 'label', 'search',
                 'library')

    def __init__(self, path, library='Bundled'):
        self.path = path.replace('\\', '/')
        self.library = library
        stem = os.path.splitext(os.path.basename(path))[0]
        self.name, self.focal, self.fnum = stem.replace('_', ' '), 0.0, 0.0
        anamorphic = False
        try:
            with open(path, encoding='utf-8', errors='replace') as fh:
                for line in fh:
                    s = line.strip()
                    if s.startswith('name:'):
                        self.name = s[5:].strip() or self.name
                    elif s.startswith('focal_length:'):
                        try:
                            self.focal = float(s[13:].strip())
                        except ValueError:
                            pass
                    elif s.startswith('#'):
                        if self.fnum == 0.0:
                            m = re.search(r'\bf/(\d+(?:\.\d+)?)', s)
                            if m:
                                self.fnum = float(m.group(1))
                    elif s.startswith('surfaces:'):
                        break
                # Anamorphic lenses carry cylindrical or toric surfaces.
                rest = fh.read()
                anamorphic = bool(re.search(r'\b(cyl_x|cyl_y|toric)\b', rest))
        except (OSError, ValueError):
            pass
        if self.fnum == 0.0:
            m = re.search(r'[Ff](\d+(?:\.\d+)?)', stem)
            if m:
                try:
                    self.fnum = float(m.group(1))
                except ValueError:
                    pass

        prefix = stem.split('_')[0]
        if prefix[:1].isdigit() or prefix.lower() in ('zoom', 'wide-conversion', 'projection'):
            self.maker = 'Other'
        else:
            self.maker = _MAKER_ALIASES.get(prefix.lower(), prefix)

        text = stem + ' ' + self.name
        if anamorphic or 'anamorph' in text.lower():
            self.kind = 'Anamorphic'
        elif _CINE_RE.search(text):
            self.kind = 'Cine'
        else:
            self.kind = 'Stills'

        bits = []
        if self.focal > 0:
            bits.append('%gmm' % round(self.focal, 1))
        if self.fnum > 0:
            bits.append('f/%g' % self.fnum)
        self.label = '%s  [%s]' % (self.name, '  '.join(bits)) if bits else self.name
        self.search = (self.label + ' ' + stem + ' ' + self.maker + ' ' + self.kind).lower()


def lens_folders():
    """(folder, library) pairs scanned for .lens files: the bundled
    library, your imported lenses, and any folders in FLARESIM_LENS_PATH."""
    folders = [(BUNDLED_LENS_ROOT, 'Bundled'), (USER_LENS_DIR, 'Mine')]
    for d in os.environ.get('FLARESIM_LENS_PATH', '').split(os.pathsep):
        if d.strip():
            folders.append((d.strip(), 'Studio'))
    return folders


_lens_cache = {}


def scan_lenses(folders):
    """All lenses in folders (recursively), sorted by label.  Cached."""
    out, seen = [], set()
    for entry in folders:
        folder, library = entry if isinstance(entry, tuple) else (entry, 'Studio')
        key = os.path.normcase(os.path.abspath(folder))
        if key not in _lens_cache:
            found = []
            for root, _dirs, files in os.walk(folder):
                for fname in files:
                    if fname.lower().endswith('.lens'):
                        found.append(LensInfo(os.path.join(root, fname), library))
            _lens_cache[key] = found
        for info in _lens_cache[key]:
            norm = os.path.normcase(info.path)
            if norm not in seen:
                seen.add(norm)
                out.append(info)
    out.sort(key=lambda l: l.label.lower())
    return out


def is_lens_file(path):
    """True when path looks like a FlareSim .lens prescription."""
    if not path.lower().endswith('.lens') or not os.path.isfile(path):
        return False
    try:
        with open(path, encoding='utf-8', errors='replace') as fh:
            for line in fh:
                if line.strip().startswith('surfaces:'):
                    return True
    except OSError:
        pass
    return False


def _same_file_contents(a, b):
    try:
        if os.path.getsize(a) != os.path.getsize(b):
            return False
        with open(a, 'rb') as fa, open(b, 'rb') as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def import_lenses(paths, dest_root=None):
    """Copy .lens files and folders of them into your lens folder.

    paths: files or folders.  A folder is copied with its own name and
    sub-folders kept, so an imported set stays together.  A file that is
    already there unchanged is skipped; a different file with the same
    name gets a numbered name instead of replacing it.

    Returns (imported, skipped, failed): lists of destination paths,
    already-present source paths, and (source path, reason) pairs.
    """
    dest_root = dest_root or USER_LENS_DIR
    jobs = []        # (source file, destination file)
    failed = []
    for p in paths:
        p = os.path.abspath(p)
        if os.path.isdir(p):
            base = os.path.basename(p.rstrip('/\\')) or 'lenses'
            for root, _dirs, files in os.walk(p):
                rel = os.path.relpath(root, p)
                for fname in sorted(files):
                    if fname.lower().endswith('.lens'):
                        jobs.append((os.path.join(root, fname),
                                     os.path.normpath(os.path.join(dest_root, base, rel, fname))))
        else:
            jobs.append((p, os.path.join(dest_root, os.path.basename(p))))
    imported, skipped = [], []
    for src, dst in jobs:
        if not is_lens_file(src):
            failed.append((src, 'not a .lens file'))
            continue
        if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dst)):
            skipped.append(src)
            continue
        stem, ext = os.path.splitext(dst)
        n = 2
        while os.path.exists(dst) and not _same_file_contents(src, dst):
            dst = '%s_%d%s' % (stem, n, ext)
            n += 1
        if os.path.exists(dst):
            skipped.append(src)
            continue
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
        except OSError as e:
            failed.append((src, str(e)))
            continue
        imported.append(dst.replace('\\', '/'))
    _lens_cache.pop(os.path.normcase(os.path.abspath(dest_root)), None)
    return imported, skipped, failed


# ---------------------------------------------------------------------------
# Preview library (ctypes)
# ---------------------------------------------------------------------------

class FspParams(ctypes.Structure):
    """Mirror of FspParams in src/preview.h."""
    _fields_ = [
        ('src_x', ctypes.c_float), ('src_y', ctypes.c_float),
        ('src_r', ctypes.c_float), ('src_g', ctypes.c_float), ('src_b', ctypes.c_float),
        ('source_intensity', ctypes.c_float),
        ('flare_gain', ctypes.c_float),
        ('fov_h_deg', ctypes.c_float),
        ('ray_grid', ctypes.c_int),
        ('aperture_blades', ctypes.c_int),
        ('aperture_rotation', ctypes.c_float),
        ('ghost_blur', ctypes.c_float),
        ('ghost_blur_passes', ctypes.c_int),
        ('exposure', ctypes.c_float),
        ('draw_source', ctypes.c_int),
        ('accumulate', ctypes.c_int),
        ('seed', ctypes.c_int),
    ]


class FspSurface(ctypes.Structure):
    """Mirror of FspSurface in src/preview.h."""
    _fields_ = [
        ('enabled', ctypes.c_int), ('gain', ctypes.c_float),
        ('r', ctypes.c_float), ('g', ctypes.c_float), ('b', ctypes.c_float),
        ('offset_x', ctypes.c_float), ('offset_y', ctypes.c_float),
        ('scale', ctypes.c_float),
    ]


class FspSurfaceInfo(ctypes.Structure):
    """Mirror of FspSurfaceInfo in src/preview.h."""
    _fields_ = [
        ('radius', ctypes.c_float), ('radius_y', ctypes.c_float),
        ('thickness', ctypes.c_float), ('ior', ctypes.c_float),
        ('abbe_v', ctypes.c_float), ('semi_aperture', ctypes.c_float),
        ('z', ctypes.c_float), ('coating', ctypes.c_int),
        ('is_stop', ctypes.c_int), ('surface_type', ctypes.c_int),
    ]


FSP_API_VERSION = 2

_lib = None
_lib_error = None


def preview_library():
    """Load flaresim_preview once.  Returns (lib, None) or (None, reason)."""
    global _lib, _lib_error
    if _lib is not None or _lib_error is not None:
        return _lib, _lib_error
    names = {'win32': ['flaresim_preview.dll'],
             'darwin': ['flaresim_preview.dylib', 'libflaresim_preview.dylib']}.get(
                 sys.platform, ['flaresim_preview.so', 'libflaresim_preview.so'])
    candidates = []
    if os.environ.get('FLARESIM_PREVIEW_LIB'):
        candidates.append(os.environ['FLARESIM_PREVIEW_LIB'])
    candidates += [os.path.join(_HERE, n) for n in names]
    path = next((c for c in candidates if os.path.isfile(c)), None)
    if not path:
        _lib_error = ('The live preview library (%s) was not found next to '
                      'FlareSim_LensBrowser.py. Rebuild and reinstall FlareSim to '
                      'get the preview.' % names[0])
        return None, _lib_error
    try:
        lib = ctypes.CDLL(path)
        lib.fsp_api_version.restype = ctypes.c_int
        if lib.fsp_api_version() != FSP_API_VERSION:
            raise OSError('version %d, expected %d' % (lib.fsp_api_version(), FSP_API_VERSION))
        lib.fsp_create.restype = ctypes.c_void_p
        lib.fsp_destroy.argtypes = [ctypes.c_void_p]
        lib.fsp_load_lens.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        lib.fsp_load_lens.restype = ctypes.c_int
        lib.fsp_render.argtypes = [ctypes.c_void_p, ctypes.POINTER(FspParams),
                                   ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
        lib.fsp_render.restype = ctypes.c_int
        lib.fsp_num_passes.argtypes = [ctypes.c_void_p]
        lib.fsp_num_pairs.argtypes = [ctypes.c_void_p]
        lib.fsp_last_error.argtypes = [ctypes.c_void_p]
        lib.fsp_last_error.restype = ctypes.c_char_p
        lib.fsp_set_surfaces.argtypes = [ctypes.c_void_p, ctypes.POINTER(FspSurface),
                                         ctypes.c_int]
        lib.fsp_set_surfaces.restype = None
        lib.fsp_set_highlight.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        lib.fsp_set_highlight.restype = None
        lib.fsp_surface_info.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                         ctypes.POINTER(FspSurfaceInfo)]
        lib.fsp_surface_info.restype = ctypes.c_int
        lib.fsp_sensor_z.argtypes = [ctypes.c_void_p]
        lib.fsp_sensor_z.restype = ctypes.c_float
        lib.fsp_pick_ghosts.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(FspParams), ctypes.c_int, ctypes.c_int,
            ctypes.c_float, ctypes.c_float, ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_float), ctypes.c_int]
        lib.fsp_pick_ghosts.restype = ctypes.c_int
    except (OSError, AttributeError) as e:
        _lib_error = 'Could not load the live preview library %s: %s' % (path, e)
        return None, _lib_error
    _lib = lib
    return _lib, None


# Per-surface settings, as on the node's Surfaces tab.  Offsets are in the
# node's pixels; the preview scales them to its own size.
SURF_DEFAULT = {'enabled': True, 'gain': 1.0, 'color': (1.0, 1.0, 1.0),
                'offx': 0.0, 'offy': 0.0, 'scale': 1.0}
# (knob name pattern, key), in the order of FlareSim_Looks.SURF_KNOBS.
SURF_KNOB_KEYS = (('surf_%d', 'enabled'), ('surf_gain_%d', 'gain'),
                  ('surf_color_%d', 'color'), ('surf_offx_%d', 'offx'),
                  ('surf_offy_%d', 'offy'), ('surf_scale_%d', 'scale'))


def default_surfaces():
    return [dict(SURF_DEFAULT) for _ in range(FlareSim_Looks.MAX_SURFS)]


def surface_changed(state):
    """True when a surface's settings differ from the defaults."""
    for key, default in SURF_DEFAULT.items():
        value = state[key]
        if key == 'color':
            if any(abs(a - b) > 1e-6 for a, b in zip(value, default)):
                return True
        elif key == 'enabled':
            if bool(value) != default:
                return True
        elif abs(value - default) > 1e-6:
            return True
    return False


def surfaces_from_knobs(values):
    """Surface states from a look's (or a node's) surf_* knob values."""
    states = default_surfaces()
    for i, state in enumerate(states):
        for pattern, key in SURF_KNOB_KEYS:
            v = values.get(pattern % i)
            if v is None:
                continue
            if key == 'color':
                v = list(v) if isinstance(v, (list, tuple)) else [v, v, v]
                state[key] = tuple(float(c) for c in (v + v[-1:] * 3)[:3])
            elif key == 'enabled':
                state[key] = bool(v)
            else:
                state[key] = float(v)
    return states


def surfaces_to_knobs(states):
    """surf_* knob values for the surfaces that are not at their defaults."""
    knobs = {}
    for i, state in enumerate(states):
        for pattern, key in SURF_KNOB_KEYS:
            value, default = state[key], SURF_DEFAULT[key]
            if key == 'color':
                if any(abs(a - b) > 1e-6 for a, b in zip(value, default)):
                    knobs[pattern % i] = [float(c) for c in value]
            elif key == 'enabled':
                if bool(value) != default:
                    knobs[pattern % i] = bool(value)
            elif abs(value - default) > 1e-6:
                knobs[pattern % i] = float(value)
    return knobs


def surface_array(packed):
    """ctypes array from the tuples the window sends with each request."""
    arr = (FspSurface * max(1, len(packed)))()
    for i, t in enumerate(packed):
        arr[i] = FspSurface(*t)
    return arr, len(packed)


class LensProbe(QtCore.QObject):
    """A second preview handle for the Lens Elements tab: reads a lens's
    surfaces for the diagram, and finds which ghosts light up a pixel on a
    background thread."""

    ghostsPicked = QtCore.Signal(object, float, float)   # [(a, b, share)], u, v

    MAX_PICK = 6

    def __init__(self, lib, parent=None):
        super(LensProbe, self).__init__(parent)
        self._lib = lib
        self._handle = lib.fsp_create()
        self._lock = threading.Lock()
        self._lens = None
        self._serial = 0

    def _load(self, lens):
        if lens != self._lens:
            self._lib.fsp_load_lens(self._handle, (lens or '').encode('utf-8'))
            self._lens = lens

    def geometry(self, lens):
        """The lens's surfaces as dicts, and the sensor position (mm)."""
        out = []
        with self._lock:
            if not self._handle:
                return out, 0.0
            self._load(lens)
            info = FspSurfaceInfo()
            i = 0
            while self._lib.fsp_surface_info(self._handle, i, ctypes.byref(info)) == 0:
                out.append({name: getattr(info, name) for name, _t in FspSurfaceInfo._fields_})
                i += 1
            return out, float(self._lib.fsp_sensor_z(self._handle))

    def pick(self, lens, params, surfaces, w, h, x, y, u, v):
        """Find the ghosts at pixel (x, y) of a w x h render; emits
        ghostsPicked.  Only the latest pick is reported."""
        self._serial += 1
        serial = self._serial

        def run():
            found = []
            with self._lock:
                if serial != self._serial or not self._handle:
                    return
                self._load(lens)
                arr, n = surface_array(surfaces)
                self._lib.fsp_set_surfaces(self._handle, arr, n)
                a = (ctypes.c_int * self.MAX_PICK)()
                b = (ctypes.c_int * self.MAX_PICK)()
                share = (ctypes.c_float * self.MAX_PICK)()
                p = FspParams(**params)
                count = self._lib.fsp_pick_ghosts(self._handle, ctypes.byref(p), w, h, x, y,
                                                  a, b, share, self.MAX_PICK)
                found = [(a[i], b[i], share[i]) for i in range(count)]
            if serial == self._serial:
                self.ghostsPicked.emit(found, u, v)

        t = threading.Thread(target=run, name='FlareSimGhostPick')
        t.daemon = True
        t.start()

    def stop(self):
        self._serial += 1
        with self._lock:
            if self._handle:
                self._lib.fsp_destroy(self._handle)
                self._handle = None


class PreviewRenderer(QtCore.QObject):
    """Renders the preview on a background thread.

    Each request is drawn once quickly (half size, coarse ray grid) so
    dragging stays live, then refined at full size, pass after pass, until a
    new request arrives or the pass limit is reached.
    """

    frameReady = QtCore.Signal(object, int, float, int)   # QImage, passes, ms, pairs
    lensLoaded = QtCore.Signal(str, int, str)           # path, surfaces, error

    DRAFT_GRID = 32

    def __init__(self, lib, parent=None):
        super(PreviewRenderer, self).__init__(parent)
        self._lib = lib
        self._handle = lib.fsp_create()
        self._cond = threading.Condition()
        self._request = None
        self._serial = 0
        self._stop = False
        self._thread = threading.Thread(target=self._run, name='FlareSimPreview')
        self._thread.daemon = True
        self._thread.start()

    def request(self, req):
        """req: dict with lens, params (FspParams kwargs), width, height,
        grid, passes, and optionally surfaces (FspSurface tuples) and
        highlight (surf_a, surf_b)."""
        with self._cond:
            self._request = dict(req)
            self._serial += 1
            self._cond.notify()

    def stop(self):
        with self._cond:
            self._stop = True
            self._cond.notify()
        self._thread.join(2.0)
        if not self._thread.is_alive() and self._handle:
            self._lib.fsp_destroy(self._handle)
            self._handle = None

    def _latest(self, serial):
        with self._cond:
            return self._serial != serial or self._stop

    def _render(self, params, w, h):
        buf = ctypes.create_string_buffer(w * h * 4)
        t0 = time.time()
        ok = self._lib.fsp_render(self._handle, ctypes.byref(params), w, h, buf)
        ms = (time.time() - t0) * 1000.0
        if ok < 0:
            return None, ms
        # QImage does not own this memory (and PySide2 does not keep the
        # bytes alive for it), so hold a reference until the copy is made.
        data = buf.raw
        view = QtGui.QImage(data, w, h, w * 4, QtGui.QImage.Format_RGBA8888)
        img = view.copy()
        del view, data
        return img, ms

    def _run(self):
        lens = None
        done = 0
        while True:
            with self._cond:
                while not self._stop and self._serial == done:
                    self._cond.wait()
                if self._stop:
                    return
                req, serial = self._request, self._serial
            done = serial
            try:
                lens = self._serve(req, serial, lens)
            except Exception as e:   # keep the thread alive for the next request
                sys.stderr.write('FlareSim preview: %s\n' % e)

    def _serve(self, req, serial, lens):
        """Draw one request.  Returns the lens now loaded."""
        if req['lens'] != lens:
            lens = req['lens']
            n = self._lib.fsp_load_lens(self._handle, (lens or '').encode('utf-8'))
            err = self._lib.fsp_last_error(self._handle).decode('utf-8', 'replace')
            self.lensLoaded.emit(lens or '', int(n), err)

        arr, n = surface_array(req.get('surfaces', ()))
        self._lib.fsp_set_surfaces(self._handle, arr, n)
        self._lib.fsp_set_highlight(self._handle, *req.get('highlight', (-1, -1)))

        w, h = req['width'], req['height']
        params = FspParams(**req['params'])

        # Quick draft so the image follows the mouse.
        params.ray_grid = min(req['grid'], self.DRAFT_GRID)
        params.accumulate, params.seed = 0, serial & 0xffff
        img, ms = self._render(params, max(1, w // 2), max(1, h // 2))
        if img is not None and not self._stop:
            self.frameReady.emit(img, 0, ms, self._lib.fsp_num_pairs(self._handle))

        # Refine at full size while nothing new has been asked for.
        params.ray_grid = req['grid']
        for p in range(req['passes']):
            if self._latest(serial):
                break
            params.accumulate, params.seed = int(p > 0), p * 7919 + 1
            img, ms = self._render(params, w, h)
            if img is None:
                break
            if self._latest(serial):
                break
            self.frameReady.emit(img, self._lib.fsp_num_passes(self._handle), ms,
                                 self._lib.fsp_num_pairs(self._handle))
        return lens


# Thumbnails: every lens rendered with the same light and settings, so they
# can be compared side by side.  Bump THUMB_VERSION when the look changes so
# old cached images are not reused.
THUMB_SIZE = (240, 135)
THUMB_VERSION = 1
THUMB_DIR = os.path.join(os.path.expanduser('~'), '.nuke', 'FlareSim', 'thumbnails')
THUMB_PARAMS = dict(
    src_x=0.72 * THUMB_SIZE[0], src_y=0.3 * THUMB_SIZE[1],
    src_r=1.0, src_g=1.0, src_b=1.0,
    source_intensity=8.0, flare_gain=10.0, fov_h_deg=40.0,
    ray_grid=32, aperture_blades=0, aperture_rotation=0.0,
    ghost_blur=0.003, ghost_blur_passes=3, exposure=0.25,
    draw_source=1, accumulate=0, seed=0)
THUMB_PASSES = 2


def thumbnail_path(lens_path):
    """Cache file for a lens's thumbnail; changes when the .lens file does."""
    try:
        st = os.stat(lens_path)
        stamp = '%d:%d' % (int(st.st_mtime), st.st_size)
    except OSError:
        stamp = ''
    key = '%s|%s|%d' % (os.path.normcase(os.path.abspath(lens_path)), stamp, THUMB_VERSION)
    digest = hashlib.sha1(key.encode('utf-8')).hexdigest()
    return os.path.join(THUMB_DIR, digest[:2], digest + '.png')


class ThumbnailRenderer(QtCore.QObject):
    """Loads cached thumbnails, or renders and caches them, on a background
    thread, in the order given to prioritize()."""

    thumbReady = QtCore.Signal(str, object)   # lens path, QImage

    def __init__(self, lib, parent=None):
        super(ThumbnailRenderer, self).__init__(parent)
        self._lib = lib
        self._handle = lib.fsp_create()
        self._cond = threading.Condition()
        self._queue = []
        self._paused = False
        self._stop = False
        self._thread = threading.Thread(target=self._run, name='FlareSimThumbnails')
        self._thread.daemon = True
        self._thread.start()

    def prioritize(self, paths):
        """Replace the work queue: these lenses, in this order."""
        with self._cond:
            self._queue = list(paths)
            self._cond.notify()

    def set_paused(self, paused):
        with self._cond:
            self._paused = paused
            self._cond.notify()

    def stop(self):
        with self._cond:
            self._stop = True
            self._cond.notify()
        self._thread.join(2.0)
        if not self._thread.is_alive() and self._handle:
            self._lib.fsp_destroy(self._handle)
            self._handle = None

    def _render(self, lens_path):
        if self._lib.fsp_load_lens(self._handle, lens_path.encode('utf-8')) <= 0:
            return None
        w, h = THUMB_SIZE
        buf = ctypes.create_string_buffer(w * h * 4)
        params = FspParams(**THUMB_PARAMS)
        for p in range(THUMB_PASSES):
            params.accumulate, params.seed = int(p > 0), p * 7919 + 1
            if self._lib.fsp_render(self._handle, ctypes.byref(params), w, h, buf) < 0:
                return None
        data = buf.raw   # keep alive until copied (see PreviewRenderer._render)
        view = QtGui.QImage(data, w, h, w * 4, QtGui.QImage.Format_RGBA8888)
        img = view.copy()
        del view, data
        return img

    def _run(self):
        while True:
            with self._cond:
                while not self._stop and (self._paused or not self._queue):
                    self._cond.wait()
                if self._stop:
                    return
                path = self._queue.pop(0)
            try:
                cache = thumbnail_path(path)
                img = QtGui.QImage(cache) if os.path.isfile(cache) else None
                if img is None or img.isNull():
                    img = self._render(path)
                    if img is not None:
                        try:
                            os.makedirs(os.path.dirname(cache), exist_ok=True)
                            img.save(cache, 'PNG')
                        except OSError:
                            pass
                if img is not None and not self._stop:
                    self.thumbReady.emit(path, img)
            except Exception as e:   # one bad lens must not stop the rest
                sys.stderr.write('FlareSim thumbnail %s: %s\n' % (path, e))


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

class PreviewView(QtWidgets.QWidget):
    """Shows the rendered flare.  Drag to move the light; the wheel changes
    exposure; Shift+click or right-click picks a ghost."""

    sourceMoved = QtCore.Signal(float, float, bool)   # u, v (0..1), dragging
    dragStateChanged = QtCore.Signal(bool)
    exposureNudged = QtCore.Signal(float)
    ghostPickRequested = QtCore.Signal(float, float)  # u, v (0..1)

    def __init__(self, parent=None):
        super(PreviewView, self).__init__(parent)
        self.setMinimumSize(320, 180)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setCursor(QtCore.Qt.CrossCursor)
        self.image = None
        self.background = None
        self.aspect = 16.0 / 9.0
        self.source = (0.7, 0.3)
        self.message = ''
        self.status = ''
        self.pick_marker = None   # (u, v) of the last ghost pick
        self._dragging = False

    def frame_rect(self):
        """The rectangle the image is drawn in, fitted to the widget."""
        w, h = self.width() - 8, self.height() - 8
        if w / float(max(h, 1)) > self.aspect:
            fw, fh = int(h * self.aspect), h
        else:
            fw, fh = w, int(w / self.aspect)
        return QtCore.QRect((self.width() - fw) // 2, (self.height() - fh) // 2, fw, fh)

    def render_size(self, limit=960):
        r = self.frame_rect()
        dpr = self.devicePixelRatioF() if hasattr(self, 'devicePixelRatioF') else 1.0
        w = max(64, int(r.width() * dpr))
        h = max(36, int(r.height() * dpr))
        if w > limit:
            h, w = int(h * limit / float(w)), limit
        return w, h

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(24, 24, 24))
        r = self.frame_rect()
        p.fillRect(r, QtCore.Qt.black)
        p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        if self.background is not None:
            p.drawImage(r, self.background)
        if self.image is not None:
            if self.background is not None:
                p.setCompositionMode(QtGui.QPainter.CompositionMode_Plus)
            p.drawImage(r, self.image)
            p.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)

        # Light position marker.
        sx = r.left() + self.source[0] * r.width()
        sy = r.top() + self.source[1] * r.height()
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.setPen(QtGui.QPen(QtGui.QColor(255, 200, 80, 200), 1.5))
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawEllipse(QtCore.QPointF(sx, sy), 9, 9)

        if self.pick_marker is not None:
            mx = r.left() + self.pick_marker[0] * r.width()
            my = r.top() + self.pick_marker[1] * r.height()
            p.setPen(QtGui.QPen(QtGui.QColor(120, 220, 255, 230), 1.5))
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                p.drawLine(QtCore.QPointF(mx + dx * 4, my + dy * 4),
                           QtCore.QPointF(mx + dx * 10, my + dy * 10))

        p.setPen(QtGui.QColor(190, 190, 190))
        if self.message:
            p.drawText(r.adjusted(24, 24, -24, -24),
                       QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap, self.message)
        hint = 'Drag: move light   |   Shift+click: pick a ghost   |   Wheel: exposure'
        p.drawText(r.adjusted(8, 6, -8, -6), QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft, hint)
        if self.status:
            p.drawText(r.adjusted(8, 6, -8, -6), QtCore.Qt.AlignBottom | QtCore.Qt.AlignLeft,
                       self.status)
        p.end()

    def _event_pos(self, event):
        pos = event.position() if hasattr(event, 'position') else event.pos()
        r = self.frame_rect()
        u = (pos.x() - r.left()) / float(max(r.width(), 1))
        v = (pos.y() - r.top()) / float(max(r.height(), 1))
        # Allow the light a little way outside the frame, where it still flares.
        return min(max(u, -0.25), 1.25), min(max(v, -0.25), 1.25)

    def mousePressEvent(self, event):
        picking = (event.button() == QtCore.Qt.RightButton or
                   (event.button() == QtCore.Qt.LeftButton and
                    event.modifiers() & QtCore.Qt.ShiftModifier))
        if picking:
            u, v = self._event_pos(event)
            if 0.0 <= u <= 1.0 and 0.0 <= v <= 1.0:
                self.pick_marker = (u, v)
                self.ghostPickRequested.emit(u, v)
                self.update()
        elif event.button() == QtCore.Qt.LeftButton:
            self._dragging = True
            self.dragStateChanged.emit(True)
            self.source = self._event_pos(event)
            self.sourceMoved.emit(self.source[0], self.source[1], True)
            self.update()

    def mouseMoveEvent(self, event):
        if self._dragging:
            self.source = self._event_pos(event)
            self.sourceMoved.emit(self.source[0], self.source[1], True)
            self.update()

    def mouseReleaseEvent(self, event):
        if self._dragging and event.button() == QtCore.Qt.LeftButton:
            self._dragging = False
            self.dragStateChanged.emit(False)
            self.source = self._event_pos(event)
            self.sourceMoved.emit(self.source[0], self.source[1], False)
            self.update()

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120.0
        if steps:
            self.exposureNudged.emit(0.25 * steps)

    def resizeEvent(self, event):
        super(PreviewView, self).resizeEvent(event)
        self.sourceMoved.emit(self.source[0], self.source[1], False)


class LensStrip(QtWidgets.QListWidget):
    """The lens thumbnails.  Emits resized so the window can switch between
    a one-row carousel and a wrapping grid as the strip is dragged taller."""

    resized = QtCore.Signal()

    def resizeEvent(self, event):
        super(LensStrip, self).resizeEvent(event)
        self.resized.emit()


class LensTileDelegate(QtWidgets.QStyledItemDelegate):
    """Draws a lens tile: the thumbnail, then the name and its focal length
    and speed on one line each, elided to fit."""

    SPEC_ROLE = QtCore.Qt.UserRole + 1

    def paint(self, painter, option, index):
        painter.save()
        r = option.rect.adjusted(3, 3, -3, -3)
        selected = bool(option.state & QtWidgets.QStyle.State_Selected)
        if selected:
            painter.fillRect(r, QtGui.QColor(61, 106, 153))
        icon = index.data(QtCore.Qt.DecorationRole)
        size = option.decorationSize
        img_rect = QtCore.QRect(r.left() + (r.width() - size.width()) // 2, r.top() + 3,
                                size.width(), size.height())
        if icon is not None:
            icon.paint(painter, img_rect)
        fm = option.fontMetrics
        line = fm.height()
        text_rect = QtCore.QRect(r.left() + 4, img_rect.bottom() + 4, r.width() - 8, line)
        name = index.data(QtCore.Qt.DisplayRole) or ''
        painter.setPen(QtGui.QColor(255, 255, 255) if selected else QtGui.QColor(210, 210, 210))
        painter.drawText(text_rect, QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter,
                         fm.elidedText(name, QtCore.Qt.ElideRight, text_rect.width()))
        spec = index.data(self.SPEC_ROLE) or ''
        painter.setPen(QtGui.QColor(230, 230, 230) if selected else QtGui.QColor(140, 140, 140))
        painter.drawText(text_rect.translated(0, line),
                         QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter,
                         fm.elidedText(spec, QtCore.Qt.ElideRight, text_rect.width()))
        painter.restore()

    def sizeHint(self, option, index):
        size = option.decorationSize
        return QtCore.QSize(size.width() + 14, size.height() + 2 * option.fontMetrics.height() + 16)


class SliderRow(QtWidgets.QWidget):
    """A label, a slider and a spin box that stay in step."""

    valueChanged = QtCore.Signal(float)

    def __init__(self, lo, hi, value, decimals=2, step=None, integer=False, parent=None):
        super(SliderRow, self).__init__(parent)
        self._lo, self._hi, self._int = lo, hi, integer
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setRange(0, 1000)
        if integer:
            self.spin = QtWidgets.QSpinBox()
            self.spin.setRange(int(lo), int(hi))
        else:
            self.spin = QtWidgets.QDoubleSpinBox()
            self.spin.setDecimals(decimals)
            self.spin.setRange(lo, hi * 10)   # typing past the slider is allowed
            self.spin.setSingleStep(step or (hi - lo) / 100.0)
        self.spin.setFixedWidth(84)
        self.slider.setMinimumWidth(80)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.spin)
        self.slider.valueChanged.connect(self._from_slider)
        self.spin.valueChanged.connect(self._from_spin)
        self.setValue(value)
        self._sync_slider(self.spin.value())

    def value(self):
        return float(self.spin.value())

    def setValue(self, v):
        self.spin.setValue(int(round(v)) if self._int else float(v))

    def _from_slider(self, s):
        v = self._lo + (self._hi - self._lo) * s / 1000.0
        if self._int:
            v = int(round(v))
        if abs(v - self.spin.value()) > 1e-9:
            self.spin.setValue(v)

    def _sync_slider(self, v):
        s = int(round((min(max(v, self._lo), self._hi) - self._lo) * 1000.0 / (self._hi - self._lo)))
        self.slider.blockSignals(True)
        self.slider.setValue(s)
        self.slider.blockSignals(False)

    def _from_spin(self, v):
        self._sync_slider(v)
        self.valueChanged.emit(float(v))


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------

# Look knobs the window edits directly.  Anything else in a loaded look
# (highlight, spectral) is carried through untouched; per-surface settings
# are edited on the Lens Elements tab.
_EDITED_KNOBS = ('flare_gain', 'aperture_blades', 'aperture_rotation',
                 'ghost_blur', 'ghost_blur_passes')

QUALITY = [('Draft', 32, 4), ('Good', 48, 8), ('Best', 64, 16)]   # grid, passes

# Preview-only settings, remembered between sessions until reset.
PREVIEW_DEFAULTS = {
    'exposure': 0.0,
    'show_light': True,
    'intensity': 8.0,
    'colour': '#ffffff',
    'fov': 40.0,
    'quality': 1,
}


def _setting_bool(value, default):
    """QSettings hands back 'true' / 'false' strings on some platforms."""
    if value is None:
        return default
    if isinstance(value, str):
        return value.lower() in ('1', 'true', 'yes')
    return bool(value)


def _setting_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _nuke_main_window():
    app = QtWidgets.QApplication.instance()
    if app is None:
        return None
    for w in app.topLevelWidgets():
        if w.inherits('QMainWindow') and w.metaObject().className() == 'Foundry::UI::DockMainWindow':
            return w
    for w in app.topLevelWidgets():
        if w.inherits('QMainWindow'):
            return w
    return None


def _breakable(name):
    """Let a long file name wrap at its underscores instead of widening the
    panel."""
    return name.replace('_', '_\u200b')


_SURF_KNOB_RE = re.compile(r'^surf_(?:gain_|color_|offx_|offy_|scale_)?\d+$')


def _format_width(node):
    """Width of the node's format in pixels, for surface offsets."""
    for get in (lambda: node.format().width(), lambda: node.width(),
                lambda: nuke.root().format().width()):
        try:
            w = int(get())
            if w > 0:
                return w
        except Exception:
            pass
    return 1920


def _node_alive(node):
    try:
        node.name()
        return True
    except (ValueError, AttributeError):
        return False


def surface_sag(radius, y):
    """Axial depth of a spherical surface at height y (0 when flat)."""
    if abs(radius) < 1e-6 or y * y >= radius * radius:
        return 0.0
    return radius - (1.0 if radius > 0 else -1.0) * (radius * radius - y * y) ** 0.5


def section_radius(s):
    """Radius of a surface in the side view (the YZ plane)."""
    kind = s['surface_type']
    if kind == 2:           # cylinder Y curves in XZ: flat from the side
        return 0.0
    if kind == 3:           # toric: radius_y is the YZ curvature
        return s['radius_y']
    return s['radius']


def trace_side_view(surfaces, sensor_z, y0, slope, bounce=None):
    """Trace a ray in the side view, as the node does (bounce = (a, b):
    forward to b, reflect, back to a, reflect, forward to the sensor).

    The ray enters at height y0 on the front surface with slope dy/dz.
    Uses each surface's drawn aperture, so the path matches the diagram.
    Returns the list of (z, y) points, ending on the sensor, or the points
    reached before the ray left the lens (and False).
    """
    def ior_before(k):
        return 1.0 if k <= 0 else surfaces[k - 1]['ior']

    norm = (1.0 + slope * slope) ** -0.5
    d = [norm, slope * norm]
    first = surfaces[0]
    lead = 10.0
    p = [first['z'] - lead, y0 - slope * lead]
    pts = [tuple(p)]

    def hit(k, d):
        s = surfaces[k]
        R = s['section_radius']
        if abs(R) < 1e-6:
            if abs(d[0]) < 1e-9:
                return None
            t = (s['z'] - p[0]) / d[0]
            h = [p[0] + t * d[0], p[1] + t * d[1]]
            n = [-1.0, 0.0]
        else:
            c = s['z'] + R
            oz, oy = p[0] - c, p[1]
            b = oz * d[0] + oy * d[1]
            cc = oz * oz + oy * oy - R * R
            disc = b * b - cc
            if disc < 0:
                return None
            t = None
            for cand in (-b - disc ** 0.5, -b + disc ** 0.5):
                hz = p[0] + cand * d[0]
                if cand > 1e-6 and (hz - c) * R < 0:   # the cap near the vertex
                    t = cand
                    break
            if t is None:
                return None
            h = [p[0] + t * d[0], p[1] + t * d[1]]
            n = [(h[0] - c) / abs(R), h[1] / abs(R)]
        if abs(h[1]) > s['draw_aperture'] * 1.001:
            return None
        if n[0] * d[0] + n[1] * d[1] > 0:
            n = [-n[0], -n[1]]
        return h, n

    def refract(d, n, n1, n2):
        eta = n1 / n2
        cos_i = -(n[0] * d[0] + n[1] * d[1])
        sin2_t = eta * eta * (1.0 - cos_i * cos_i)
        if sin2_t > 1.0:
            return None
        k = eta * cos_i - (1.0 - sin2_t) ** 0.5
        return [eta * d[0] + k * n[0], eta * d[1] + k * n[1]]

    def reflect(d, n):
        dn = d[0] * n[0] + d[1] * n[1]
        return [d[0] - 2 * dn * n[0], d[1] - 2 * dn * n[1]]

    a, b = bounce if bounce else (None, None)
    steps = []
    if bounce:
        steps += [(k, 'fwd') for k in range(0, b)] + [(b, 'refl')]
        steps += [(k, 'back') for k in range(b - 1, a, -1)] + [(a, 'refl')]
        steps += [(k, 'fwd') for k in range(a + 1, len(surfaces))]
    else:
        steps = [(k, 'fwd') for k in range(len(surfaces))]
    for k, mode in steps:
        res = hit(k, d)
        if res is None:
            return pts, False
        h, n = res
        p[:] = h
        pts.append(tuple(h))
        if mode == 'refl':
            d = reflect(d, n)
        elif mode == 'fwd':
            d = refract(d, n, ior_before(k), surfaces[k]['ior'])
        else:
            d = refract(d, n, surfaces[k]['ior'], ior_before(k))
        if d is None:
            return pts, False
    if d[0] <= 1e-9:
        return pts, False
    t = (sensor_z - p[0]) / d[0]
    pts.append((p[0] + t * d[0], p[1] + t * d[1]))
    return pts, True


class LensDiagram(QtWidgets.QWidget):
    """A side view of the lens: each surface as its curve, glass shaded,
    the iris marked.  Click a surface to select it."""

    surfaceClicked = QtCore.Signal(int)

    MARGIN = 14

    def __init__(self, parent=None):
        super(LensDiagram, self).__init__(parent)
        self.setMinimumSize(240, 190)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self.setMouseTracking(True)
        self.surfaces = []      # dicts from LensProbe.geometry
        self.sensor_z = 0.0
        self.selected = -1
        self.ghost = None       # (a, b) of the picked ghost
        self.states = []        # per-surface settings, for changed / off marks
        self.message = ''
        self.optics = None      # dict(slope, sensor_half_w, ref_width) from the window
        self._hover = -1

    def sizeHint(self):
        return QtCore.QSize(340, 210)

    def set_lens(self, surfaces, sensor_z):
        self.surfaces, self.sensor_z = surfaces, sensor_z
        self.selected, self.ghost, self._hover = -1, None, -1
        for s in self.surfaces:
            s['section_radius'] = section_radius(s)
        self._fit_apertures()
        self.update()

    def _fit_apertures(self):
        """Heights to draw each surface at.  Many converted prescriptions
        list a clear diameter where a semi-diameter belongs, which would
        draw curves crossing each other, so all apertures shrink by one
        factor until neighbouring surfaces no longer cross."""
        surfs = self.surfaces

        def crosses(f):
            for k in range(len(surfs) - 1):
                s0, s1 = surfs[k], surfs[k + 1]
                a = min(f * s0['semi_aperture'], f * s1['semi_aperture'])
                gap = (s1['z'] + surface_sag(s1['section_radius'], a)) - (s0['z'] + surface_sag(s0['section_radius'], a))
                if gap < -0.05 * max(s1['z'] - s0['z'], 0.05):
                    return True
            return False

        f = 1.0
        while f > 0.3 and crosses(f):
            f *= 0.95
        for s in surfs:
            a = f * s['semi_aperture']
            if abs(s['section_radius']) > 1e-6:
                a = min(a, 0.98 * abs(s['section_radius']))
            s['draw_aperture'] = a

    # Mapping from lens space (z along the axis, y up, mm) to the widget.
    def _transform(self):
        if not self.surfaces:
            return None
        z0 = min(s['z'] + min(0.0, surface_sag(s['section_radius'], s['draw_aperture']))
                 for s in self.surfaces)
        z1 = max(self.sensor_z, max(s['z'] for s in self.surfaces))
        ymax = max(s['draw_aperture'] for s in self.surfaces) * 1.15 or 1.0
        m = self.MARGIN
        w, h = self.width() - 2 * m, self.height() - 2 * m - 14
        scale = min(w / max(z1 - z0, 1e-3), h / (2.0 * ymax))
        ox = m + (w - (z1 - z0) * scale) / 2.0 - z0 * scale
        oy = m + h / 2.0
        return scale, ox, oy

    def _curve(self, s, tf, steps=24):
        scale, ox, oy = tf
        a = s['draw_aperture']
        pts = []
        for i in range(steps + 1):
            y = -a + 2.0 * a * i / steps
            z = s['z'] + surface_sag(s['section_radius'], y)
            pts.append(QtCore.QPointF(ox + z * scale, oy - y * scale))
        return pts

    def surface_at(self, x, y):
        """Index of the surface nearest widget position (x, y), or -1."""
        tf = self._transform()
        if tf is None:
            return -1
        scale, ox, oy = tf
        best, best_d = -1, 12.0
        for i, s in enumerate(self.surfaces):
            a = s['draw_aperture']
            ly = (oy - y) / scale
            if abs(ly) > a * 1.2:
                continue
            ly = max(-a, min(a, ly))
            sx = ox + (s['z'] + surface_sag(s['section_radius'], ly)) * scale
            d = abs(sx - x)
            if d < best_d:
                best, best_d = i, d
        return best

    def paintEvent(self, _event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QtGui.QColor(27, 27, 27))
        tf = self._transform()
        if tf is None:
            p.setPen(QtGui.QColor(150, 150, 150))
            p.drawText(self.rect().adjusted(12, 12, -12, -12),
                       QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap,
                       self.message or 'Pick a lens to see its elements.')
            p.end()
            return
        scale, ox, oy = tf
        curves = [self._curve(s, tf) for s in self.surfaces]

        # Optical axis and sensor.
        p.setPen(QtGui.QPen(QtGui.QColor(70, 70, 70), 1, QtCore.Qt.DashLine))
        p.drawLine(QtCore.QPointF(self.MARGIN, oy), QtCore.QPointF(self.width() - self.MARGIN, oy))
        ymax = max(s['draw_aperture'] for s in self.surfaces)
        sx = ox + self.sensor_z * scale
        p.setPen(QtGui.QPen(QtGui.QColor(110, 110, 110), 2))
        p.drawLine(QtCore.QPointF(sx, oy - ymax * 0.6 * scale),
                   QtCore.QPointF(sx, oy + ymax * 0.6 * scale))

        # Glass between a surface and the next when the medium after it is
        # not air.
        for i, s in enumerate(self.surfaces[:-1]):
            if s['ior'] > 1.01 and not s['is_stop']:
                poly = QtGui.QPolygonF(curves[i] + list(reversed(curves[i + 1])))
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(QtGui.QColor(90, 140, 190, 70))
                p.drawPolygon(poly)

        # Surfaces.
        for i, s in enumerate(self.surfaces):
            state = self.states[i] if i < len(self.states) else SURF_DEFAULT
            off = not state['enabled']
            in_ghost = self.ghost is not None and i in self.ghost
            if s['is_stop']:
                a = s['draw_aperture']
                x = ox + s['z'] * scale
                p.setPen(QtGui.QPen(QtGui.QColor(200, 200, 200), 2))
                for sign in (1, -1):
                    p.drawLine(QtCore.QPointF(x, oy - sign * a * scale),
                               QtCore.QPointF(x, oy - sign * ymax * 1.12 * scale))
                if i != self.selected and not in_ghost:
                    continue
            colour = QtGui.QColor(150, 175, 195)
            width = 1.4
            if off:
                colour = QtGui.QColor(170, 70, 70)
            elif surface_changed(state):
                colour = QtGui.QColor(90, 210, 230)
            if in_ghost:
                colour, width = QtGui.QColor(255, 150, 60), 2.6
            if i == self._hover:
                width += 1.0
            if i == self.selected:
                colour, width = QtGui.QColor(255, 210, 90), 3.0
            pen = QtGui.QPen(colour, width)
            if off:
                pen.setStyle(QtCore.Qt.DashLine)
            p.setPen(pen)
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawPolyline(QtGui.QPolygonF(curves[i]))

        self._draw_light(p, tf)

        # Labels for the selected surface and the picked ghost's surfaces.
        p.setPen(QtGui.QColor(220, 220, 220))
        marks = {}
        if self.ghost is not None:
            marks[self.ghost[0]] = 'A'
            marks[self.ghost[1]] = 'B'
        if self.selected >= 0:
            marks[self.selected] = marks.get(self.selected, '') + ' %d' % self.selected
        for i, text in marks.items():
            if 0 <= i < len(curves):
                top = curves[i][-1]
                p.drawText(QtCore.QRectF(top.x() - 30, top.y() - 18, 60, 16),
                           QtCore.Qt.AlignCenter, text.strip())

        p.setPen(QtGui.QColor(130, 130, 130))
        p.drawText(self.rect().adjusted(8, 0, -8, -4), QtCore.Qt.AlignBottom | QtCore.Qt.AlignLeft,
                   'Click a surface to edit it. Esc deselects.')
        p.end()

    def _ghost_pairs(self):
        """The ghosts to draw: the picked one, else every ghost off the
        selected surface."""
        if self.ghost is not None:
            return [tuple(sorted(self.ghost))], True
        i = self.selected
        if i < 0:
            return [], False
        pairs = []
        for j in range(len(self.surfaces)):
            if j == i or self.surfaces[j]['is_stop'] or self.surfaces[i]['is_stop']:
                continue
            pairs.append((min(i, j), max(i, j)))
        return pairs, False

    def _draw_light(self, p, tf):
        """Light paths through the lens: the image-forming ray, faint, and
        the ghosts of the selection, drawn with their surfaces' settings so
        each change shows: the tint colours the path, gain sets its
        strength, a surface turned off drops it, and offset and scale move
        where it lands on the sensor (arrows)."""
        if self.optics is None:
            return
        scale, ox, oy = tf
        slope = self.optics['slope']
        to_px = lambda pt: QtCore.QPointF(ox + pt[0] * scale, oy - pt[1] * scale)
        front = self.surfaces[0]['draw_aperture']

        pts, ok = trace_side_view(self.surfaces, self.sensor_z, 0.0, slope)
        if len(pts) > 1:
            p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 60), 1))
            p.drawPolyline(QtGui.QPolygonF([to_px(q) for q in pts]))

        pairs, bold = self._ghost_pairs()
        heights = (-0.5, 0.0, 0.5) if bold else (0.0,)
        for a, b in pairs:
            st = [self.states[k] if k < len(self.states) else SURF_DEFAULT for k in (a, b)]
            on = st[0]['enabled'] and st[1]['enabled']
            gain = st[0]['gain'] * st[1]['gain']
            tint = [st[0]['color'][c] * st[1]['color'][c] for c in range(3)]
            peak = max(max(tint), 1e-6)
            colour = QtGui.QColor.fromRgbF(*[min(1.0, 0.25 + 0.75 * t / peak) for t in tint])
            if on:
                strength = min(1.0, 0.25 + 0.75 * min(gain, 2.0) / 2.0) if gain > 0 else 0.08
                colour.setAlphaF((0.9 if bold else 0.55) * strength)
                width = (1.6 if bold else 1.0) * (0.6 + 0.4 * min(gain, 3.0))
                pen = QtGui.QPen(colour, width)
            else:
                pen = QtGui.QPen(QtGui.QColor(170, 70, 70, 140), 1, QtCore.Qt.DashLine)
            off_mm = (st[0]['offy'] + st[1]['offy']) / float(max(self.optics['ref_width'], 1)) \
                * 2.0 * self.optics['sensor_half_w']
            sc = st[0]['scale'] * st[1]['scale']
            for y0 in heights:
                pts, ok = trace_side_view(self.surfaces, self.sensor_z, y0 * front, slope, (a, b))
                if len(pts) < 2:
                    continue
                p.setPen(pen)
                p.drawPolyline(QtGui.QPolygonF([to_px(q) for q in pts]))
                if not (ok and on):
                    continue
                z, y = pts[-1]
                moved = (z, y * sc + off_mm)
                p.setBrush(colour)
                p.drawEllipse(to_px(moved), 2.5, 2.5)
                if abs(moved[1] - y) * scale > 2.0:
                    p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 150), 1, QtCore.Qt.DotLine))
                    p.drawLine(to_px((z, y)), to_px(moved))
                p.setBrush(QtCore.Qt.NoBrush)

    def mouseMoveEvent(self, event):
        pos = event.position() if hasattr(event, 'position') else event.pos()
        i = self.surface_at(pos.x(), pos.y())
        if i != self._hover:
            self._hover = i
            self.setCursor(QtCore.Qt.PointingHandCursor if i >= 0 else QtCore.Qt.ArrowCursor)
            self.setToolTip(surface_description(self.surfaces[i], i) if i >= 0 else '')
            self.update()

    def leaveEvent(self, event):
        self._hover = -1
        self.update()
        super(LensDiagram, self).leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            pos = event.position() if hasattr(event, 'position') else event.pos()
            # Clicking empty space, or the selected surface again, deselects.
            i = self.surface_at(pos.x(), pos.y())
            self.surfaceClicked.emit(-1 if i == self.selected else i)


def surface_description(s, i):
    """One line about a surface for labels and tooltips."""
    if s['is_stop']:
        return 'Surface %d: the iris (aperture stop), %.1f mm across' % (i, 2 * s['semi_aperture'])
    shape = {1: 'cylinder X', 2: 'cylinder Y', 3: 'toric'}.get(s['surface_type'], '')
    if abs(s['radius']) < 1e-6:
        curve = 'flat'
    else:
        curve = 'R %.1f mm' % s['radius']
    if shape:
        curve = '%s %s' % (shape, curve)
    behind = 'glass n %.3f behind' % s['ior'] if s['ior'] > 1.01 else 'air behind'
    coat = 'uncoated' if s['coating'] <= 0 else (
        'single coated' if s['coating'] == 1 else 'multi-coated (%d)' % s['coating'])
    return 'Surface %d: %s, %s, %s' % (i, curve, behind, coat)


class LensBrowserWindow(QtWidgets.QWidget):

    def __init__(self, parent=None):
        super(LensBrowserWindow, self).__init__(parent or _nuke_main_window())
        self.setWindowFlags(QtCore.Qt.Window)
        self.setWindowTitle('FlareSim Lens Browser')
        self.setObjectName('FlareSimLensBrowser')
        self.resize(1360, 860)

        self._node = None
        self._lenses = scan_lenses(lens_folders())
        self._filtered = []
        self._lens_path = ''
        self._look_extra = {}          # look knobs carried through as-is
        self._look_name = ''
        self._created_node = None
        self._source_colour = QtGui.QColor(255, 255, 255)
        self._surfaces = 0
        self._items = {}               # lens path -> grid item
        self._has_thumb = set()
        self._surf = default_surfaces()   # per-surface settings for this lens
        # Surfaces edited here since the settings were last in step with
        # the node; all of them after a look or a lens change.
        self._surf_dirty = set()
        self._surf_all_dirty = False
        # Look settings changed here since then; every one after a look is
        # picked.  Apply writes only these, so settings made on the node
        # are kept.
        self._knob_dirty = set()
        self._look_dirty = False
        self._sel_surface = -1
        self._ghost = None             # (surf_a, surf_b) of the picked ghost
        self._picked = []              # [(a, b, share)] from the last pick
        self._ref_width = 1920         # node format width, for surface offsets
        self._probe = None

        self._build_ui()

        lib, err = preview_library()
        self._renderer = None
        self._thumbs = None
        if lib is not None:
            self._renderer = PreviewRenderer(lib, self)
            self._renderer.frameReady.connect(self._on_frame)
            self._renderer.lensLoaded.connect(self._on_lens_loaded)
            self._thumbs = ThumbnailRenderer(lib, self)
            self._thumbs.thumbReady.connect(self._on_thumbnail)
            self._probe = LensProbe(lib, self)
            self._probe.ghostsPicked.connect(self._on_ghosts_picked)
        else:
            self.view.message = err
            self.diagram.message = err

        self._thumb_timer = QtCore.QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.setInterval(80)
        self._thumb_timer.timeout.connect(self._queue_thumbnails)

        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(30)
        self._refresh_timer.timeout.connect(self._request_render)

        self._restore_layout()
        self._restore_preview()
        # Save preview settings shortly after they change, so they survive
        # even if Nuke quits without closing the window.
        self._preview_save_timer = QtCore.QTimer(self)
        self._preview_save_timer.setSingleShot(True)
        self._preview_save_timer.setInterval(500)
        self._preview_save_timer.timeout.connect(self._save_preview)
        for row in (self.exposure, self.intensity, self.fov):
            row.valueChanged.connect(self._preview_save_timer.start)
        self.quality.currentIndexChanged.connect(self._preview_save_timer.start)
        self.show_source.toggled.connect(self._preview_save_timer.start)
        self._apply_filters()
        self._refresh_looks()

    # -- layout ---------------------------------------------------------

    def _build_ui(self):
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        root.addWidget(splitter)

        # Left: find a lens, lens info, looks.
        left = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 4, 0)
        find_box = QtWidgets.QGroupBox('Find a Lens')
        filters = QtWidgets.QFormLayout(find_box)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText('Search lenses (name, maker, 50mm, f/1.4...)')
        self.search.setClearButtonEnabled(True)
        self.type_combo = QtWidgets.QComboBox()
        self.type_combo.addItems(TYPES)
        self.library_combo = QtWidgets.QComboBox()
        self.library_combo.setToolTip(
            'Bundled: lenses that ship with FlareSim. Mine: lenses you imported '
            '(~/.nuke/FlareSim/lenses). Studio: folders in FLARESIM_LENS_PATH.')
        self.maker_combo = QtWidgets.QComboBox()
        self._fill_library_combos()
        self.focal_combo = QtWidgets.QComboBox()
        for label, _lo, _hi in FOCAL_RANGES:
            self.focal_combo.addItem(label)
        self.speed_combo = QtWidgets.QComboBox()
        for label, _lo, _hi in SPEEDS:
            self.speed_combo.addItem(label)
        self.count_label = QtWidgets.QLabel()
        self.count_label.setStyleSheet('color: #999;')
        self.size_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.size_slider.setRange(50, 150)
        self.size_slider.setValue(75)
        self.size_slider.setToolTip('Thumbnail size when the lens strip shows more than '
                                    'one row. A single row fills the strip.')
        filters.addRow(self.search)
        filters.addRow('Library', self.library_combo)
        filters.addRow('Type', self.type_combo)
        filters.addRow('Maker', self.maker_combo)
        filters.addRow('Focal', self.focal_combo)
        filters.addRow('Speed', self.speed_combo)
        filters.addRow('Tile size', self.size_slider)
        filters.addRow(self.count_label)
        lv.addWidget(find_box)

        self.grid = LensStrip()
        self.grid.setViewMode(QtWidgets.QListView.IconMode)
        self.grid.setResizeMode(QtWidgets.QListView.Adjust)
        self.grid.setMovement(QtWidgets.QListView.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setWordWrap(True)
        self.grid.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.grid.setTextElideMode(QtCore.Qt.ElideRight)
        self.grid.setToolTip('Click a lens to preview it; double-click to apply it to the node.')
        self.grid.setStyleSheet('QListWidget { background: #1b1b1b; }')
        self.grid.setItemDelegate(LensTileDelegate(self.grid))
        self._placeholder = None
        self._fill_grid()
        self.grid.setMinimumHeight(90)

        centre = QtWidgets.QWidget()
        cv = QtWidgets.QVBoxLayout(centre)
        cv.setContentsMargins(0, 4, 0, 0)
        self.view = PreviewView()
        cv.addWidget(self.view, 1)
        under = QtWidgets.QHBoxLayout()
        under.addWidget(QtWidgets.QLabel('Exposure'))
        self.exposure = SliderRow(-6.0, 6.0, PREVIEW_DEFAULTS['exposure'], decimals=2, step=0.25)
        under.addWidget(self.exposure, 1)
        exp_reset = QtWidgets.QPushButton('Reset')
        exp_reset.setToolTip('Put the preview exposure back to 0. '
                             'Exposure is remembered when the browser closes.')
        exp_reset.clicked.connect(
            lambda: self.exposure.setValue(PREVIEW_DEFAULTS['exposure']))
        under.addWidget(exp_reset)
        self.show_source = QtWidgets.QCheckBox('Show light')
        self.show_source.setChecked(True)
        under.addWidget(self.show_source)
        bg_btn = QtWidgets.QPushButton('Background...')
        bg_btn.setToolTip('Show an image behind the flare, e.g. a still of your plate.')
        bg_btn.clicked.connect(self._choose_background)
        under.addWidget(bg_btn)
        self.clear_bg_btn = QtWidgets.QPushButton('Clear')
        self.clear_bg_btn.setEnabled(False)
        self.clear_bg_btn.clicked.connect(self._clear_background)
        under.addWidget(self.clear_bg_btn)
        cv.addLayout(under)

        # Centre: the live preview over the lens strip.  Drag the divider:
        # a short strip is a one-row carousel, a taller one wraps into a grid.
        self.view_split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self.view_split.addWidget(centre)
        self.view_split.addWidget(self.grid)
        self.view_split.setCollapsible(0, False)
        self.view_split.setCollapsible(1, False)
        self.view_split.setStretchFactor(0, 1)
        self.view_split.setStretchFactor(1, 0)
        self.view_split.setSizes([620, 200])
        splitter.addWidget(left)
        splitter.addWidget(self.view_split)

        # Right: look controls and actions.
        right = QtWidgets.QWidget()
        rv = QtWidgets.QVBoxLayout(right)
        rv.setContentsMargins(4, 0, 0, 0)

        lens_box = QtWidgets.QGroupBox('Lens')
        lg = QtWidgets.QVBoxLayout(lens_box)
        self.info_label = QtWidgets.QLabel()
        self.info_label.setWordWrap(True)
        self.info_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        lg.addWidget(self.info_label)
        lens_btns = QtWidgets.QHBoxLayout()
        open_btn = QtWidgets.QPushButton('Open .lens File...')
        open_btn.setToolTip('Preview a .lens file from anywhere without adding it to the library.')
        open_btn.clicked.connect(self._browse_lens_file)
        lens_btns.addWidget(open_btn)
        import_btn = QtWidgets.QPushButton('Import Lens')
        import_btn.setToolTip('Copy .lens files, or a folder of them, into your lens '
                              'library (~/.nuke/FlareSim/lenses).')
        import_menu = QtWidgets.QMenu(import_btn)
        import_menu.addAction('Lens Files...', self._import_lens_files)
        import_menu.addAction('Folder...', self._import_lens_folder)
        import_btn.setMenu(import_menu)
        lens_btns.addWidget(import_btn)
        lg.addLayout(lens_btns)
        lv.addWidget(lens_box)

        look_box = QtWidgets.QGroupBox('Start From a Look')
        lk = QtWidgets.QVBoxLayout(look_box)
        self.look_combo = QtWidgets.QComboBox()
        lk.addWidget(self.look_combo)
        self.look_desc = QtWidgets.QLabel()
        self.look_desc.setWordWrap(True)
        self.look_desc.setStyleSheet('color: #999;')
        lk.addWidget(self.look_desc)
        load_look = QtWidgets.QPushButton('Load Look')
        load_look.setToolTip('Load the look\'s lens and settings into this window.')
        load_look.clicked.connect(self._load_look)
        self.delete_look_btn = QtWidgets.QPushButton('Delete Look')
        self.delete_look_btn.setToolTip('Delete this look from your own looks folder. '
                                        'Studio and starter looks cannot be deleted here.')
        self.delete_look_btn.clicked.connect(self._delete_look)
        look_btns = QtWidgets.QHBoxLayout()
        look_btns.addWidget(load_look)
        look_btns.addWidget(self.delete_look_btn)
        lk.addLayout(look_btns)
        lv.addWidget(look_box)
        lv.addStretch(1)

        self.tabs = QtWidgets.QTabWidget()
        look_tab = QtWidgets.QWidget()
        tv = QtWidgets.QVBoxLayout(look_tab)
        tv.setContentsMargins(4, 6, 4, 4)

        flare_box = QtWidgets.QGroupBox('Flare Look')
        fl = QtWidgets.QFormLayout(flare_box)
        self.gain = SliderRow(0.0, 50.0, 10.0, decimals=2, step=0.5)
        self.blades = SliderRow(0, 12, 0, integer=True)
        self.rotation = SliderRow(0.0, 180.0, 0.0, decimals=1, step=1.0)
        self.blur = SliderRow(0.0, 0.02, 0.003, decimals=4, step=0.0005)
        self.blur_passes = SliderRow(0, 5, 3, integer=True)
        self.gain.setToolTip('Flare Gain on the node.')
        self.blades.setToolTip('Aperture Blades on the node. 0 = round iris.')
        self.rotation.setToolTip('Aperture Rotation on the node, in degrees.')
        fl.addRow('Gain', self.gain)
        fl.addRow('Blades', self.blades)
        fl.addRow('Rotation', self.rotation)
        fl.addRow('Ghost Blur', self.blur)
        fl.addRow('Blur Passes', self.blur_passes)
        self.extra_label = QtWidgets.QLabel()
        self.extra_label.setWordWrap(True)
        self.extra_label.setStyleSheet('color: #999;')
        fl.addRow(self.extra_label)
        tv.addWidget(flare_box)

        light_box = QtWidgets.QGroupBox('Preview Light and Camera')
        ll = QtWidgets.QFormLayout(light_box)
        self.intensity = SliderRow(0.0, 50.0, 8.0, decimals=2, step=0.5)
        self.colour_btn = QtWidgets.QPushButton()
        self.colour_btn.setToolTip('Light colour')
        self.colour_btn.clicked.connect(self._choose_colour)
        self.fov = SliderRow(5.0, 120.0, 40.0, decimals=1, step=1.0)
        self.quality = QtWidgets.QComboBox()
        for label, grid, passes in QUALITY:
            self.quality.addItem('%s (%d passes)' % (label, passes))
        self.quality.setCurrentIndex(1)
        ll.addRow('Intensity', self.intensity)
        ll.addRow('Colour', self.colour_btn)
        ll.addRow('FOV', self.fov)
        ll.addRow('Quality', self.quality)
        light_reset = QtWidgets.QPushButton('Reset Preview Light')
        light_reset.setToolTip('Put intensity, colour, FOV and quality back to their defaults.')
        light_reset.clicked.connect(self._reset_preview_light)
        ll.addRow(light_reset)
        note = QtWidgets.QLabel('Preview only, remembered between sessions. Opening '
                                'the browser from a node uses its intensity and FOV.')
        note.setWordWrap(True)
        note.setStyleSheet('color: #999;')
        ll.addRow(note)
        tv.addWidget(light_box)
        tv.addStretch(1)
        self.tabs.addTab(look_tab, 'Look')
        self.tabs.addTab(self._build_elements_tab(), 'Lens Elements')
        rv.addWidget(self.tabs, 1)

        self.target_label = QtWidgets.QLabel()
        self.target_label.setWordWrap(True)
        rv.addWidget(self.target_label)
        self.apply_btn = QtWidgets.QPushButton('Apply to Node')
        self.apply_btn.setDefault(True)
        self.apply_btn.setMinimumHeight(32)
        self.apply_btn.setToolTip('Set the lens and look settings on the target FlareSim '
                                  'node. Creates a FlareSim node when there is none.')
        self.apply_btn.clicked.connect(self._apply_to_node)
        rv.addWidget(self.apply_btn)
        actions = QtWidgets.QHBoxLayout()
        save_btn = QtWidgets.QPushButton('Save as Look...')
        save_btn.clicked.connect(self._save_look)
        from_node = QtWidgets.QPushButton('Reload From Node')
        from_node.setToolTip('Load the target node\'s lens and look settings.')
        from_node.clicked.connect(self._load_from_node)
        actions.addWidget(save_btn)
        actions.addWidget(from_node)
        rv.addLayout(actions)

        # The controls scroll when the window is short.
        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(right)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        splitter.addWidget(scroll)
        scroll.setMinimumWidth(right.minimumSizeHint().width() +
                               scroll.verticalScrollBar().sizeHint().width() + 4)
        scroll.setMaximumWidth(scroll.minimumWidth() + 240)
        self.exposure.setMinimumWidth(160)
        left.setMinimumWidth(260)
        left.setMaximumWidth(400)
        splitter.setCollapsible(1, False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([300, 860, 380])
        self.main_split = splitter

        # Signals.
        self.search.textChanged.connect(self._apply_filters)
        for combo in (self.library_combo, self.maker_combo, self.focal_combo,
                      self.speed_combo, self.type_combo):
            combo.currentIndexChanged.connect(self._apply_filters)
        self.grid.currentItemChanged.connect(self._on_lens_picked)
        self.grid.itemDoubleClicked.connect(lambda _item: self._apply_to_node())
        self.grid.verticalScrollBar().valueChanged.connect(
            lambda _v: self._thumb_timer.start())
        self.grid.horizontalScrollBar().valueChanged.connect(
            lambda _v: self._thumb_timer.start())
        self.size_slider.valueChanged.connect(self._layout_strip)
        self.grid.resized.connect(self._layout_strip)
        self.look_combo.currentIndexChanged.connect(self._show_look_info)
        for row in (self.gain, self.blades, self.rotation, self.blur, self.blur_passes,
                    self.intensity, self.fov, self.exposure):
            row.valueChanged.connect(self._schedule_render)
        for k, row in self._look_rows().items():
            row.valueChanged.connect(lambda _v, k=k: self._knob_dirty.add(k))
        self.quality.currentIndexChanged.connect(self._schedule_render)
        self.show_source.toggled.connect(self._schedule_render)
        self.view.sourceMoved.connect(self._on_source_moved)
        self.view.dragStateChanged.connect(self._on_drag_state)
        self.view.exposureNudged.connect(
            lambda d: self.exposure.setValue(self.exposure.value() + d))
        self.view.ghostPickRequested.connect(self._pick_ghost)
        self.tabs.currentChanged.connect(self._schedule_render)

        QShortcut(QtGui.QKeySequence('Ctrl+F'), self, self.search.setFocus)
        QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Escape), self, lambda: self._select_surface(-1))
        QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_PageUp), self,
                            lambda: self._step_lens(-1))
        QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_PageDown), self,
                            lambda: self._step_lens(1))
        self._update_colour_button()

    def _build_elements_tab(self):
        tab = QtWidgets.QWidget()
        ev = QtWidgets.QVBoxLayout(tab)
        ev.setContentsMargins(4, 6, 4, 4)

        self.diagram = LensDiagram()
        self.diagram.states = self._surf
        self.diagram.surfaceClicked.connect(lambda i: self._select_surface(i, keep_ghost=True))
        ev.addWidget(self.diagram)

        nav = QtWidgets.QHBoxLayout()
        prev_btn = QtWidgets.QToolButton()
        prev_btn.setArrowType(QtCore.Qt.LeftArrow)
        prev_btn.setToolTip('Previous surface')
        prev_btn.clicked.connect(lambda: self._step_surface(-1))
        next_btn = QtWidgets.QToolButton()
        next_btn.setArrowType(QtCore.Qt.RightArrow)
        next_btn.setToolTip('Next surface')
        next_btn.clicked.connect(lambda: self._step_surface(1))
        self.surf_title = QtWidgets.QLabel()
        self.surf_title.setWordWrap(True)
        nav.addWidget(prev_btn)
        nav.addWidget(self.surf_title, 1)
        nav.addWidget(next_btn)
        ev.addLayout(nav)

        surf_box = QtWidgets.QGroupBox('Surface')
        sf = QtWidgets.QFormLayout(surf_box)
        self.surf_enabled = QtWidgets.QCheckBox('Makes ghosts')
        self.surf_enabled.setToolTip('Off drops every ghost that bounces off this surface.')
        self.surf_gain = SliderRow(0.0, 5.0, 1.0, decimals=2, step=0.05)
        self.surf_gain.setToolTip('Brightness of the ghosts off this surface. A ghost '
                                  'bounces off two surfaces, so both gains multiply.')
        self.surf_colour_btn = QtWidgets.QPushButton()
        self.surf_colour_btn.setToolTip('Tint for the ghosts off this surface, '
                                        'like a coloured coating.')
        self.surf_colour_btn.clicked.connect(self._choose_surface_colour)
        self.surf_offx = SliderRow(-1000.0, 1000.0, 0.0, decimals=1, step=1.0)
        self.surf_offy = SliderRow(-1000.0, 1000.0, 0.0, decimals=1, step=1.0)
        for row in (self.surf_offx, self.surf_offy):
            row.spin.setRange(-10000.0, 10000.0)
            row.setToolTip('Shift the ghosts off this surface, in the node\'s pixels. '
                           'A ghost\'s two surfaces add their shifts.')
        self.surf_scale = SliderRow(0.25, 3.0, 1.0, decimals=3, step=0.01)
        self.surf_scale.setToolTip('Grow or shrink the ghosts off this surface about the '
                                   'frame centre.')
        sf.addRow(self.surf_enabled)
        sf.addRow('Gain', self.surf_gain)
        sf.addRow('Tint', self.surf_colour_btn)
        sf.addRow('Offset X', self.surf_offx)
        sf.addRow('Offset Y', self.surf_offy)
        sf.addRow('Scale', self.surf_scale)
        resets = QtWidgets.QHBoxLayout()
        reset_one = QtWidgets.QPushButton('Reset Surface')
        reset_one.clicked.connect(self._reset_surface)
        reset_all = QtWidgets.QPushButton('Reset All')
        reset_all.setToolTip('Put every surface back to its defaults.')
        reset_all.clicked.connect(self._reset_all_surfaces)
        resets.addWidget(reset_one)
        resets.addWidget(reset_all)
        sf.addRow(resets)
        self.surf_changed_label = QtWidgets.QLabel()
        self.surf_changed_label.setStyleSheet('color: #999;')
        sf.addRow(self.surf_changed_label)
        ev.addWidget(surf_box)
        self._surf_controls = [self.surf_enabled, self.surf_gain, self.surf_colour_btn,
                               self.surf_offx, self.surf_offy, self.surf_scale,
                               reset_one]

        ghost_box = QtWidgets.QGroupBox('Pick a Ghost')
        gv = QtWidgets.QVBoxLayout(ghost_box)
        hint = QtWidgets.QLabel('Shift+click (or right-click) a ghost in the preview to '
                                'see which two surfaces make it.')
        hint.setWordWrap(True)
        hint.setStyleSheet('color: #999;')
        gv.addWidget(hint)
        self.ghost_list = QtWidgets.QListWidget()
        self.ghost_list.setMaximumHeight(110)
        self.ghost_list.currentRowChanged.connect(self._on_ghost_row)
        gv.addWidget(self.ghost_list)
        pair_row = QtWidgets.QHBoxLayout()
        self.ghost_a_btn = QtWidgets.QPushButton('Edit A')
        self.ghost_b_btn = QtWidgets.QPushButton('Edit B')
        self.ghost_a_btn.clicked.connect(lambda: self._edit_ghost_surface(0))
        self.ghost_b_btn.clicked.connect(lambda: self._edit_ghost_surface(1))
        pair_row.addWidget(self.ghost_a_btn)
        pair_row.addWidget(self.ghost_b_btn)
        gv.addLayout(pair_row)
        self.highlight_check = QtWidgets.QCheckBox('Highlight in the preview')
        self.highlight_check.setChecked(True)
        self.highlight_check.setToolTip('Dim the other ghosts in the preview so the picked '
                                        'ghost, or the selected surface\'s ghosts, stand out. '
                                        'The node is not affected.')
        self.highlight_check.toggled.connect(self._schedule_render)
        gv.addWidget(self.highlight_check)
        ev.addWidget(ghost_box)
        ev.addStretch(1)

        self.surf_enabled.toggled.connect(lambda v: self._edit_surface('enabled', bool(v)))
        for row, key in ((self.surf_gain, 'gain'), (self.surf_offx, 'offx'),
                         (self.surf_offy, 'offy'), (self.surf_scale, 'scale')):
            row.valueChanged.connect(lambda v, key=key: self._edit_surface(key, v))
        self._update_surface_panel()
        self._update_ghost_list()
        return tab

    # -- lens elements --------------------------------------------------

    def _num_surfaces(self):
        return len(self.diagram.surfaces)

    def _select_surface(self, i, keep_ghost=False):
        if not keep_ghost or (self._ghost is not None and i not in self._ghost):
            self._clear_ghost()
        self._sel_surface = i if 0 <= i < self._num_surfaces() else -1
        self.diagram.selected = self._sel_surface
        self.diagram.update()
        self._update_surface_panel()
        self._schedule_render()

    def _step_surface(self, delta):
        n = self._num_surfaces()
        if n:
            start = self._sel_surface if self._sel_surface >= 0 else (-1 if delta > 0 else 0)
            self._select_surface((start + delta) % n)

    def _update_surface_panel(self):
        i = self._sel_surface
        n = self._num_surfaces()
        valid = 0 <= i < n
        for w in self._surf_controls:
            w.setEnabled(valid)
        if valid:
            self.surf_title.setText(surface_description(self.diagram.surfaces[i], i))
        elif n:
            self.surf_title.setText('%d surfaces. Click one in the diagram.' % n)
        else:
            self.surf_title.setText('')
        state = self._surf[i] if valid else SURF_DEFAULT
        for w in (self.surf_enabled, self.surf_gain, self.surf_offx, self.surf_offy,
                  self.surf_scale):
            w.blockSignals(True)
        self.surf_enabled.setChecked(bool(state['enabled']))
        self.surf_gain.setValue(state['gain'])
        self.surf_offx.setValue(state['offx'])
        self.surf_offy.setValue(state['offy'])
        self.surf_scale.setValue(state['scale'])
        for w in (self.surf_enabled, self.surf_gain, self.surf_offx, self.surf_offy,
                  self.surf_scale):
            w.blockSignals(False)
        for row in (self.surf_gain, self.surf_offx, self.surf_offy, self.surf_scale):
            row._sync_slider(row.value())
        c = state['color']
        swatch = QtGui.QColor.fromRgbF(*[min(max(v, 0.0), 1.0) for v in c])
        label = 'None (click to pick)' if all(abs(v - 1.0) < 1e-6 for v in c) else \
            '%.2f  %.2f  %.2f' % tuple(c)
        self.surf_colour_btn.setText(label)
        text_colour = '#000' if swatch.lightnessF() > 0.5 else '#fff'
        self.surf_colour_btn.setStyleSheet('background-color: %s; color: %s; min-height: 18px;'
                                           % (swatch.name(), text_colour))
        changed = sum(1 for k in range(n) if surface_changed(self._surf[k]))
        self.surf_changed_label.setText(
            '%d of %d surfaces changed.' % (changed, n) if changed else '')

    def _edit_surface(self, key, value):
        i = self._sel_surface
        if not 0 <= i < self._num_surfaces():
            return
        self._surf[i][key] = value
        self._surf_dirty.add(i)
        self.diagram.update()
        self._update_surface_panel()
        self._schedule_render()

    def _open_colour_picker(self, start, title, on_change):
        """A colour wheel that updates the preview live as you pick.

        on_change(QColor) runs on every change; Cancel calls it with start
        again.  The dialog is Qt's own (not the OS one) and stays on top of
        the browser: inside Nuke the static QColorDialog.getColor can open
        behind the window, so clicking the swatch seemed to do nothing.
        """
        old = getattr(self, '_colour_dialog', None)
        if old is not None:
            old.reject()
        dlg = QtWidgets.QColorDialog(start, self)
        dlg.setWindowTitle(title)
        dlg.setOption(QtWidgets.QColorDialog.DontUseNativeDialog, True)
        dlg.setWindowFlags(dlg.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
        dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        dlg.currentColorChanged.connect(on_change)
        dlg.colorSelected.connect(on_change)
        dlg.rejected.connect(lambda: on_change(start))

        def closed(*_args):
            if getattr(self, '_colour_dialog', None) is dlg:
                self._colour_dialog = None
        dlg.finished.connect(closed)
        self._colour_dialog = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        return dlg

    def _choose_surface_colour(self):
        i = self._sel_surface
        if not 0 <= i < self._num_surfaces():
            return
        c = self._surf[i]['color']
        start = QtGui.QColor.fromRgbF(*[min(max(v, 0.0), 1.0) for v in c])

        def apply(colour, i=i):
            # Tint the surface the picker was opened for, even if another
            # surface gets selected meanwhile.
            if not (colour.isValid() and 0 <= i < len(self._surf)):
                return
            self._surf[i]['color'] = (colour.redF(), colour.greenF(), colour.blueF())
            self._surf_dirty.add(i)
            self.diagram.update()
            if i == self._sel_surface:
                self._update_surface_panel()
            self._schedule_render()
        self._open_colour_picker(start, 'Surface %d Tint' % i, apply)

    def _reset_surface(self):
        i = self._sel_surface
        if 0 <= i < self._num_surfaces():
            self._surf[i].update(SURF_DEFAULT)
            self._surf_dirty.add(i)
            self.diagram.update()
            self._update_surface_panel()
            self._schedule_render()

    def _reset_all_surfaces(self):
        for state in self._surf:
            state.update(SURF_DEFAULT)
        self._surf_all_dirty = True
        self.diagram.update()
        self._update_surface_panel()
        self._schedule_render()

    def _set_surfaces(self, states):
        """Replace every surface's settings (from a node or a look)."""
        for state, new in zip(self._surf, states):
            state.clear()
            state.update(new)
        self.diagram.update()
        self._update_surface_panel()

    def _packed_surfaces(self):
        """Surface settings for the preview library: offsets go from the
        node's pixels to fractions of the image width."""
        ref = float(max(self._ref_width, 1))
        out = []
        for state in self._surf[:max(self._num_surfaces(), 0)]:
            r, g, b = state['color']
            out.append((int(bool(state['enabled'])), state['gain'], r, g, b,
                        state['offx'] / ref, state['offy'] / ref, state['scale']))
        return out

    def _highlight(self):
        if (self.tabs.currentIndex() != 1 or not self.highlight_check.isChecked()):
            return (-1, -1)
        if self._ghost is not None:
            return self._ghost
        if self._sel_surface >= 0:
            return (self._sel_surface, -1)
        return (-1, -1)

    def _pick_ghost(self, u, v):
        if self._probe is None or not self._lens_path:
            return
        if self.tabs.currentIndex() != 1:
            self.tabs.setCurrentIndex(1)
        req = self._render_request()
        params = dict(req['params'], ray_grid=min(req['grid'], 48))
        w, h = req['width'], req['height']
        self.ghost_list.clear()
        self.ghost_list.addItem('Looking...')
        self._probe.pick(self._lens_path, params, req['surfaces'], w, h,
                         u * w, v * h, u, v)

    def _on_ghosts_picked(self, found, u, v):
        self._picked = list(found)
        if self.view.pick_marker != (u, v):
            return
        self._update_ghost_list()
        if self._picked:
            self.ghost_list.setCurrentRow(0)
        else:
            self._clear_ghost(keep_list=True)

    def _update_ghost_list(self):
        self.ghost_list.blockSignals(True)
        self.ghost_list.clear()
        for a, b, share in self._picked:
            item = QtWidgets.QListWidgetItem('Surfaces %d + %d   %.0f%%' % (a, b, share * 100.0))
            item.setToolTip('This ghost bounces off surfaces %d and %d and makes %.0f%% of '
                            'the light where you clicked.' % (a, b, share * 100.0))
            self.ghost_list.addItem(item)
        if not self._picked and self.view.pick_marker is not None:
            self.ghost_list.addItem('No ghost there. Try a brighter spot.')
        self.ghost_list.blockSignals(False)
        has = self._ghost is not None
        self.ghost_a_btn.setEnabled(has)
        self.ghost_b_btn.setEnabled(has)
        if has:
            self.ghost_a_btn.setText('Edit %d' % self._ghost[0])
            self.ghost_b_btn.setText('Edit %d' % self._ghost[1])
        else:
            self.ghost_a_btn.setText('Edit A')
            self.ghost_b_btn.setText('Edit B')

    def _on_ghost_row(self, row):
        if not 0 <= row < len(self._picked):
            return
        a, b, _share = self._picked[row]
        self._ghost = (a, b)
        self.diagram.ghost = self._ghost
        self._update_ghost_list()
        self._select_surface(a, keep_ghost=True)

    def _edit_ghost_surface(self, which):
        if self._ghost is not None:
            self._select_surface(self._ghost[which], keep_ghost=True)

    def _clear_ghost(self, keep_list=False):
        self._ghost = None
        self.diagram.ghost = None
        if not keep_list:
            self._picked = []
            self.view.pick_marker = None
            self.view.update()
        self._update_ghost_list()
        self.diagram.update()

    # -- lens list ------------------------------------------------------

    @staticmethod
    def _item_spec(l):
        bits = []
        if l.focal > 0:
            bits.append('%gmm' % round(l.focal, 1))
        if l.fnum > 0:
            bits.append('f/%g' % l.fnum)
        return '  '.join(bits) or l.maker

    def _layout_strip(self, *_args):
        """Fit the tiles to the lens strip: one row of tiles as tall as the
        strip allows (a carousel), or, once two rows of slider-sized tiles fit,
        a wrapping grid of those."""
        g = self.grid
        line = g.fontMetrics().height()
        text_h = 2 * line + 16
        aspect = THUMB_SIZE[0] / float(THUMB_SIZE[1])
        avail = g.height() - 2 * g.frameWidth() - 4
        tile_h = int(THUMB_SIZE[1] * self.size_slider.value() / 100.0)
        if avail >= 2 * (tile_h + text_h):
            wrap = True
        else:
            wrap = False
            bar = g.horizontalScrollBar().sizeHint().height()
            tile_h = max(40, min(int(THUMB_SIZE[1] * 1.6), avail - bar - text_h))
        tile_w = int(tile_h * aspect)
        if g.isWrapping() != wrap or g.flow() != (QtWidgets.QListView.LeftToRight):
            g.setFlow(QtWidgets.QListView.LeftToRight)
            g.setWrapping(wrap)
            g.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff if wrap
                                           else QtCore.Qt.ScrollBarAsNeeded)
            g.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded if wrap
                                         else QtCore.Qt.ScrollBarAlwaysOff)
        size = QtCore.QSize(tile_w, tile_h)
        if g.iconSize() != size:
            g.setIconSize(size)
            g.setGridSize(QtCore.QSize(tile_w + 14, tile_h + text_h))
        current = g.currentItem()
        if current is not None:
            g.scrollToItem(current)
        if hasattr(self, '_thumb_timer'):   # not yet while building the UI
            self._thumb_timer.start()

    def _placeholder_icon(self):
        if self._placeholder is None:
            pm = QtGui.QPixmap(*THUMB_SIZE)
            pm.fill(QtGui.QColor(12, 12, 12))
            p = QtGui.QPainter(pm)
            p.setPen(QtGui.QColor(90, 90, 90))
            p.drawText(pm.rect(), QtCore.Qt.AlignCenter,
                       'rendering...' if self._thumbs else 'no preview')
            p.end()
            self._placeholder = QtGui.QIcon(pm)
        return self._placeholder

    def _visible_items(self):
        return [self.grid.item(i) for i in range(self.grid.count())
                if not self.grid.item(i).isHidden()]

    def _apply_filters(self, *_args):
        terms = self.search.text().strip().lower().split()
        maker = self.maker_combo.currentData()
        library = self.library_combo.currentData()
        _f, flo, fhi = FOCAL_RANGES[self.focal_combo.currentIndex()]
        _s, slo, shi = SPEEDS[self.speed_combo.currentIndex()]
        kind = self.type_combo.currentText()
        out = []
        for l in self._lenses:
            ok = True
            if library and l.library != library:
                ok = False
            elif maker and l.maker != maker:
                ok = False
            elif self.focal_combo.currentIndex() and not (flo <= l.focal < fhi):
                ok = False
            elif self.speed_combo.currentIndex() and not (l.fnum > 0 and slo <= l.fnum < shi):
                ok = False
            elif self.type_combo.currentIndex() and l.kind != kind:
                ok = False
            elif any(t not in l.search for t in terms):
                ok = False
            item = self._items[l.path]
            item.setHidden(not ok)
            if ok:
                out.append(l)
                if l.path not in self._has_thumb:
                    item.setIcon(self._placeholder_icon())
        self._filtered = out
        self.count_label.setText('%d of %d' % (len(out), len(self._lenses)))

        # Keep the current lens when it passes the filters; otherwise show
        # the first match.
        current = self._items.get(self._lens_path)
        if current is not None and not current.isHidden():
            self._select_item(current)
        elif out:
            self.set_lens(out[0].path)
        self._thumb_timer.start()

    def _queue_thumbnails(self):
        """Ask for thumbnails of the lenses on screen first, then the rest of
        the filtered list."""
        if self._thumbs is None:
            return
        vp = self.grid.viewport().rect()
        on_screen, rest = [], []
        for l in self._filtered:
            if l.path in self._has_thumb:
                continue
            item = self._items[l.path]
            (on_screen if self.grid.visualItemRect(item).intersects(vp) else rest).append(l.path)
        self._thumbs.prioritize(on_screen + rest)

    def _on_thumbnail(self, path, image):
        item = self._items.get(path)
        if item is None:
            return
        item.setIcon(QtGui.QIcon(QtGui.QPixmap.fromImage(image)))
        self._has_thumb.add(path)

    def _on_drag_state(self, dragging):
        # Give the live preview the CPU while the light is being dragged.
        if self._thumbs is not None:
            self._thumbs.set_paused(dragging)

    def _select_item(self, item):
        if self.grid.currentItem() is not item:
            self.grid.blockSignals(True)
            self.grid.setCurrentItem(item)
            self.grid.blockSignals(False)
        self.grid.scrollToItem(item)

    def _on_lens_picked(self, item, _previous=None):
        if item is None:
            return
        path = item.data(QtCore.Qt.UserRole)
        if path and path != self._lens_path:
            self.set_lens(path)

    def _step_lens(self, delta):
        items = self._visible_items()
        if not items:
            return
        current = self._items.get(self._lens_path)
        i = items.index(current) if current in items else -1
        self.set_lens(items[(i + delta) % len(items)].data(QtCore.Qt.UserRole))

    def _fill_library_combos(self):
        """Library and maker filters with their lens counts; keeps the
        current choices."""
        library = self.library_combo.currentData()
        maker = self.maker_combo.currentData()
        for combo in (self.library_combo, self.maker_combo):
            combo.blockSignals(True)
            combo.clear()
        lib_counts = {}
        counts = {}
        for l in self._lenses:
            lib_counts[l.library] = lib_counts.get(l.library, 0) + 1
            counts[l.maker] = counts.get(l.maker, 0) + 1
        for label, key in LIBRARIES:
            n = len(self._lenses) if not key else lib_counts.get(key, 0)
            if key == 'Studio' and not n:
                continue
            self.library_combo.addItem('%s (%d)' % (label, n), key)
        self.maker_combo.addItem('All makers (%d)' % len(self._lenses), '')
        for m in sorted(counts, key=lambda m: (m == 'Other', m.lower())):
            self.maker_combo.addItem('%s (%d)' % (m, counts[m]), m)
        for combo, value in ((self.library_combo, library), (self.maker_combo, maker)):
            i = combo.findData(value) if value else 0
            combo.setCurrentIndex(max(i, 0))
            combo.blockSignals(False)

    def _fill_grid(self):
        # Keep the thumbnails already rendered.
        icons = dict((path, item.icon()) for path, item in self._items.items()
                     if path in self._has_thumb)
        self.grid.clear()
        self._items = {}
        for l in self._lenses:
            item = QtWidgets.QListWidgetItem(l.name)
            item.setData(QtCore.Qt.UserRole, l.path)
            item.setData(LensTileDelegate.SPEC_ROLE, self._item_spec(l))
            item.setToolTip('%s\n%s, %s  (%s)\n%s' % (l.label, l.maker, l.kind, l.library,
                                                      os.path.basename(l.path)))
            if l.path in icons:
                item.setIcon(icons[l.path])
            self.grid.addItem(item)
            self._items[l.path] = item

    def _import_lens_files(self):
        paths, _f = QtWidgets.QFileDialog.getOpenFileNames(
            self, 'Import Lens Files', self._import_start_dir(),
            'Lens files (*.lens);;All files (*)')
        if paths:
            self.import_lenses(paths)

    def _import_lens_folder(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self, 'Import a Folder of Lenses', self._import_start_dir())
        if path:
            self.import_lenses([path])

    def _import_start_dir(self):
        st = self._settings()
        last = st.value('import_dir')
        return str(last) if last and os.path.isdir(str(last)) else os.path.expanduser('~')

    def import_lenses(self, paths, quiet=False):
        """Copy lenses into your library, list them and show the first one."""
        if paths:
            first = os.path.abspath(paths[0])
            self._settings().setValue(
                'import_dir', first if os.path.isdir(first) else os.path.dirname(first))
        imported, skipped, failed = import_lenses(paths)
        self._reload_lenses()
        if imported or skipped:
            # Show what was imported: the Mine library, unfiltered.
            self.search.clear()
            for combo in (self.type_combo, self.focal_combo, self.speed_combo, self.maker_combo):
                combo.setCurrentIndex(0)
            i = self.library_combo.findData('Mine')
            if i >= 0:
                self.library_combo.setCurrentIndex(i)
            self._apply_filters()
            show = imported[0] if imported else None
            if show and show in self._items:
                self.set_lens(show)
        lines = []
        if imported:
            lines.append('Imported %d lens%s into %s.' % (
                len(imported), '' if len(imported) == 1 else 'es', USER_LENS_DIR))
        if skipped:
            lines.append('%d %s already in your library.' % (
                len(skipped), 'was' if len(skipped) == 1 else 'were'))
        if failed:
            lines.append('%d could not be imported:' % len(failed))
            lines += ['  %s: %s' % (os.path.basename(p), why) for p, why in failed[:10]]
            if len(failed) > 10:
                lines.append('  ...and %d more' % (len(failed) - 10))
        if not lines:
            lines.append('No .lens files found.')
        nuke.tprint('FlareSim: ' + ' '.join(l.strip() for l in lines))
        if not quiet:
            box = QtWidgets.QMessageBox.warning if failed and not imported else \
                QtWidgets.QMessageBox.information
            box(self, 'Import Lens', '\n'.join(lines))
        return imported, skipped, failed

    def _reload_lenses(self):
        """Rescan the lens folders after an import."""
        self._lenses = scan_lenses(lens_folders())
        self._fill_library_combos()
        self._fill_grid()
        self._apply_filters()

    def _browse_lens_file(self):
        start = os.path.dirname(self._lens_path) if self._lens_path else BUNDLED_LENS_ROOT
        path, _f = QtWidgets.QFileDialog.getOpenFileName(
            self, 'Open Lens File', start, 'Lens files (*.lens);;All files (*)')
        if path:
            self.set_lens(path)

    def set_lens(self, path, surfaces=None):
        """Show a lens: select it in the grid (if listed) and preview it.

        surfaces: per-surface settings to use with it.  Without them a new
        lens starts with every surface at its defaults, since its surfaces
        are not the old lens's.
        """
        path = path.replace('\\', '/')
        changed = path != self._lens_path
        self._lens_path = path
        item = self._items.get(self._lens_path)
        if item is not None and not item.isHidden():
            self._select_item(item)
        else:
            self.grid.clearSelection()
        if surfaces is not None:
            self._set_surfaces(surfaces)
        elif changed:
            self._set_surfaces(default_surfaces())
            self._surf_all_dirty = True
        if changed:
            self._load_geometry()
        self._surfaces = 0
        self._update_info()
        self._schedule_render()

    def _load_geometry(self):
        geom, sensor_z = ([], 0.0)
        if self._probe is not None and self._lens_path:
            geom, sensor_z = self._probe.geometry(self._lens_path)
        self.diagram.set_lens(geom, sensor_z)
        self._sel_surface = -1
        self._clear_ghost()
        self._update_surface_panel()

    def _lens_info(self):
        for l in self._lenses:
            if l.path == self._lens_path:
                return l
        return LensInfo(self._lens_path) if self._lens_path else None

    def _update_info(self, pairs=None):
        info = self._lens_info()
        if info is None:
            self.info_label.setText('No lens selected.')
            return
        lines = ['<b>%s</b>' % info.name, '%s, %s' % (info.maker, info.kind)]
        bits = []
        if info.focal > 0:
            bits.append('%g mm' % round(info.focal, 1))
        if info.fnum > 0:
            bits.append('f/%g' % info.fnum)
        if self._surfaces:
            bits.append('%d surfaces' % self._surfaces)
        if pairs:
            bits.append('%d ghosts' % pairs)
        if bits:
            lines.append(', '.join(bits))
        lines.append('<span style="color:#888">%s</span>' % _breakable(os.path.basename(info.path)))
        self.info_label.setText('<br>'.join(lines))

    # -- looks ----------------------------------------------------------

    def _refresh_looks(self, select_name=None):
        self._looks = FlareSim_Looks.list_looks()
        self.look_combo.blockSignals(True)
        self.look_combo.clear()
        for look in self._looks:
            self.look_combo.addItem('%s  (%s)' % (look['name'], look['_source']))
        if select_name:
            for i, look in enumerate(self._looks):
                if look['name'].lower() == select_name.lower():
                    self.look_combo.setCurrentIndex(i)
        self.look_combo.blockSignals(False)
        self._show_look_info()

    def _show_look_info(self, *_args):
        i = self.look_combo.currentIndex()
        if 0 <= i < len(self._looks):
            look = self._looks[i]
            text = look.get('description', '')
            if look.get('lens'):
                text += ('\n' if text else '') + 'Lens: %s' % _breakable(os.path.basename(look['lens']))
            self.look_desc.setText(text)
            mine = FlareSim_Looks.can_delete(look)
            self.delete_look_btn.setEnabled(mine)
            self.delete_look_btn.setToolTip(
                'Delete this look from your own looks folder.' if mine else
                'This is a %s look, so it cannot be deleted here.' % look['_source'].lower())
        else:
            self.look_desc.setText('No looks found.')
            self.delete_look_btn.setEnabled(False)

    def _delete_look(self):
        i = self.look_combo.currentIndex()
        if not 0 <= i < len(self._looks):
            return
        look = self._looks[i]
        if not FlareSim_Looks.can_delete(look):
            return
        answer = QtWidgets.QMessageBox.question(
            self, 'Delete Look', 'Delete the look "%s"?\n\nThis removes %s and cannot be undone.'
            % (look['name'], look['_path']))
        if answer != QtWidgets.QMessageBox.Yes:
            return
        try:
            FlareSim_Looks.delete_look(look)
        except (OSError, ValueError) as e:
            QtWidgets.QMessageBox.warning(self, 'Delete Look', 'Could not delete the look:\n%s' % e)
            return
        if self._look_name.lower() == look['name'].lower():
            self._look_name = ''
        nuke.tprint('FlareSim: deleted look %s' % look['_path'])
        self._refresh_looks()

    def _load_look(self):
        i = self.look_combo.currentIndex()
        if not 0 <= i < len(self._looks):
            return
        look = self._looks[i]
        lens = FlareSim_Looks.resolve_lens(look.get('lens', ''))
        if look.get('lens') and not lens:
            QtWidgets.QMessageBox.warning(self, 'FlareSim', 'Lens not found: %s' % look['lens'])
        surfaces = self._set_look_values(look.get('knobs', {}))
        self._look_name = look['name']
        if lens:
            self.set_lens(lens, surfaces)
        else:
            self._set_surfaces(surfaces)
        # A look defines every surface and setting.
        self._surf_all_dirty = True
        self._look_dirty = True

    def _look_rows(self):
        return {'flare_gain': self.gain, 'aperture_blades': self.blades,
                'aperture_rotation': self.rotation, 'ghost_blur': self.blur,
                'ghost_blur_passes': self.blur_passes}

    def _set_look_values(self, values):
        for k, row in self._look_rows().items():
            if k in values:
                row.blockSignals(True)
                row.setValue(values[k])
                row.blockSignals(False)
        self._look_extra = {k: v for k, v in values.items()
                            if k not in _EDITED_KNOBS and not _SURF_KNOB_RE.match(k)
                            and k not in FlareSim_Looks.SKIPPED_KNOBS}
        if self._look_extra:
            self.extra_label.setText('Also from the look: %d more settings (highlight, '
                                     'spectral...) kept as they are.'
                                     % len(self._look_extra))
        else:
            self.extra_label.setText('')
        self._schedule_render()
        return surfaces_from_knobs(values)

    def current_look(self, name='', surfaces=True):
        """The window's settings as a look dict.  With surfaces, it includes
        the per-surface settings that differ from their defaults."""
        knobs = dict(self._look_extra)
        if surfaces:
            knobs.update(surfaces_to_knobs(self._surf[:self._num_surfaces() or None]))
        knobs.update({
            'flare_gain': self.gain.value(),
            'aperture_blades': int(self.blades.value()),
            'aperture_rotation': self.rotation.value(),
            'ghost_blur': self.blur.value(),
            'ghost_blur_passes': int(self.blur_passes.value()),
        })
        return {
            'flaresim_look_version': FlareSim_Looks.LOOK_VERSION,
            'name': name,
            'description': '',
            'lens': FlareSim_Looks.lens_to_look(self._lens_path),
            'knobs': knobs,
        }

    def _save_look(self):
        name, ok = QtWidgets.QInputDialog.getText(
            self, 'Save Look', 'Look name:', text=self._look_name or self._default_look_name())
        if not ok or not name.strip():
            return
        desc, ok = QtWidgets.QInputDialog.getText(self, 'Save Look', 'Description (optional):')
        if not ok:
            return
        look = self.current_look(name.strip())
        look['description'] = desc.strip()
        try:
            path = FlareSim_Looks.write_look(look)
        except FileExistsError as e:
            answer = QtWidgets.QMessageBox.question(
                self, 'Save Look', 'A look named "%s" already exists. Replace it?' % name)
            if answer != QtWidgets.QMessageBox.Yes:
                return
            path = FlareSim_Looks.write_look(look, overwrite=True)
        except OSError as e:
            QtWidgets.QMessageBox.warning(self, 'Save Look', 'Could not save the look:\n%s' % e)
            return
        self._look_name = look['name']
        self._refresh_looks(select_name=look['name'])
        nuke.tprint('FlareSim: saved look to %s' % path)

    def _default_look_name(self):
        info = self._lens_info()
        return info.name if info else 'My Look'

    # -- node -----------------------------------------------------------

    def set_node(self, node):
        """Tie the window to a node and load its lens and look settings."""
        self._node = node
        self._load_from_node()

    def _target_nodes(self, create=False):
        if self._node is not None and _node_alive(self._node):
            return [self._node]
        self._node = None
        nodes = [n for n in nuke.selectedNodes() if n.Class() in FLARESIM_CLASSES]
        if nodes:
            return nodes
        all_fs = [n for n in nuke.allNodes(recurseGroups=True) if n.Class() in FLARESIM_CLASSES]
        if len(all_fs) == 1:
            return all_fs
        if all_fs:
            if create:
                QtWidgets.QMessageBox.information(
                    self, 'FlareSim', 'Select the FlareSim node(s) to update, then try again.')
            return []
        if create:
            node = nuke.createNode('FlareSim', inpanel=False)
            self._node = node
            self._created_node = node
            return [node]
        return []

    def _update_target_label(self):
        if self._node is not None and _node_alive(self._node):
            self.target_label.setText('Target: <b>%s</b>' % self._node.fullName())
        else:
            self.target_label.setText('Target: the selected FlareSim node, or a new one.')

    def _load_from_node(self):
        nodes = self._target_nodes()
        self._update_target_label()
        if not nodes:
            return
        node = nodes[0]
        knobs = node.knobs()
        values = {}
        for k in FlareSim_Looks.LOOK_KNOBS:
            if k in knobs:
                values[k] = FlareSim_Looks._knob_value(knobs[k])
        for i in range(FlareSim_Looks.MAX_SURFS):
            for pattern, _key in SURF_KNOB_KEYS:
                k = pattern % i
                if k in knobs:
                    values[k] = FlareSim_Looks._knob_value(knobs[k])
        surfaces = self._set_look_values(values)
        self._ref_width = _format_width(node)
        for k, row in (('fov_h', self.fov), ('source_intensity', self.intensity)):
            if k in knobs:
                row.setValue(float(knobs[k].value()))
        self._look_name = ''
        lens = knobs['lens_file'].value() if 'lens_file' in knobs else ''
        if lens:
            lens = nuke.filenameFilter(lens) if hasattr(nuke, 'filenameFilter') else lens
        if lens and os.path.isfile(lens):
            self.set_lens(lens, surfaces)
        else:
            self._set_surfaces(surfaces)
        self._surf_dirty.clear()
        self._surf_all_dirty = False
        self._knob_dirty.clear()
        self._look_dirty = False
        self._schedule_render()

    def _apply_to_node(self):
        if not self._lens_path:
            QtWidgets.QMessageBox.information(self, 'FlareSim', 'Pick a lens first.')
            return
        self._created_node = None
        nodes = self._target_nodes(create=True)
        self._update_target_label()
        if not nodes:
            return
        new_node = self._created_node is not None
        # A new lens (or a look) sets every surface.  Otherwise only the
        # surfaces edited here are written, so tweaks made on the node's
        # Surfaces tab in the meantime are kept.
        same_lens = all(os.path.normcase(n['lens_file'].value().replace('\\', '/')) ==
                        os.path.normcase(self._lens_path) for n in nodes)
        all_surfaces = self._surf_all_dirty or not same_lens
        look = self.current_look(self._look_name, surfaces=all_surfaces)
        look['lens'] = self._lens_path
        # Settings not changed here (Ray Grid, Spectral, Highlight, or the
        # gain and blur when left alone) keep the node's values.  A picked
        # look, or a new node, takes them all.
        if not (self._look_dirty or new_node):
            look['knobs'] = {k: v for k, v in look['knobs'].items()
                             if k in self._knob_dirty or _SURF_KNOB_RE.match(k)}
        if not all_surfaces:
            for i in sorted(self._surf_dirty):
                state = self._surf[i]
                for pattern, key in SURF_KNOB_KEYS:
                    v = state[key]
                    look['knobs'][pattern % i] = list(v) if key == 'color' else v
        warnings = FlareSim_Looks.apply_look(look, nodes, reset_surfaces=all_surfaces)
        self._surf_dirty.clear()
        self._surf_all_dirty = False
        self._knob_dirty.clear()
        self._look_dirty = False
        self._ref_width = _format_width(nodes[0])
        if warnings:
            QtWidgets.QMessageBox.warning(self, 'FlareSim', '\n'.join(warnings))
        nuke.tprint('FlareSim: applied %s to %s' % (
            os.path.basename(self._lens_path), ', '.join(n.name() for n in nodes)))

    # -- preview --------------------------------------------------------

    def _choose_colour(self):
        def apply(colour):
            if colour.isValid():
                self._source_colour = QtGui.QColor(colour)
                self._update_colour_button()
                self._schedule_render()
                self._preview_save_timer.start()
        self._open_colour_picker(QtGui.QColor(self._source_colour), 'Light Colour', apply)

    def _update_colour_button(self):
        self.colour_btn.setStyleSheet('background-color: %s; min-height: 18px;'
                                      % self._source_colour.name())

    def _choose_background(self):
        path, _f = QtWidgets.QFileDialog.getOpenFileName(
            self, 'Background Image', '', 'Images (*.png *.jpg *.jpeg *.tif *.tiff *.bmp)')
        if not path:
            return
        img = QtGui.QImage(path)
        if img.isNull():
            QtWidgets.QMessageBox.warning(self, 'FlareSim', 'Could not read %s' % path)
            return
        self.view.background = img
        self.view.aspect = img.width() / float(max(img.height(), 1))
        self.clear_bg_btn.setEnabled(True)
        self._schedule_render()

    def _clear_background(self):
        self.view.background = None
        self.view.aspect = 16.0 / 9.0
        self.clear_bg_btn.setEnabled(False)
        self._schedule_render()

    def _on_source_moved(self, _u, _v, dragging):
        # While dragging render at once; otherwise coalesce bursts of changes.
        if dragging:
            self._request_render()
        else:
            self._schedule_render()

    def _schedule_render(self, *_args):
        self._refresh_timer.start()

    def _request_render(self):
        if self._renderer is None:
            self.view.update()
            return
        self._renderer.request(self._render_request())

    def _update_diagram_optics(self, w, h):
        """Give the lens diagram the preview light's angle, so its light
        paths follow the light."""
        _u, v = self.view.source
        tan_half_h = math.tan(math.radians(min(max(self.fov.value(), 1.0), 170.0)) * 0.5)
        tan_half_v = tan_half_h * h / float(max(w, 1))
        info = self._lens_info()
        focal = info.focal if info is not None and info.focal > 0 else 50.0
        self.diagram.optics = {'slope': (0.5 - v) * 2.0 * tan_half_v,
                               'sensor_half_w': focal * tan_half_h,
                               'ref_width': self._ref_width}
        self.diagram.update()

    def _render_request(self):
        w, h = self.view.render_size()
        u, v = self.view.source
        c = self._source_colour
        _label, grid, passes = QUALITY[self.quality.currentIndex()]
        self._update_diagram_optics(w, h)
        return {
            'lens': self._lens_path,
            'surfaces': self._packed_surfaces(),
            'highlight': self._highlight(),
            'width': w, 'height': h, 'grid': grid, 'passes': passes,
            'params': dict(
                src_x=u * w, src_y=v * h,
                src_r=c.redF(), src_g=c.greenF(), src_b=c.blueF(),
                source_intensity=self.intensity.value(),
                flare_gain=self.gain.value(),
                fov_h_deg=self.fov.value(),
                ray_grid=grid,
                aperture_blades=int(self.blades.value()),
                aperture_rotation=self.rotation.value(),
                ghost_blur=self.blur.value(),
                ghost_blur_passes=int(self.blur_passes.value()),
                exposure=self.exposure.value(),
                draw_source=int(self.show_source.isChecked()),
                accumulate=0, seed=0),
        }

    def _on_frame(self, image, passes, ms, pairs):
        self.view.image = image
        quality = QUALITY[self.quality.currentIndex()]
        if passes:
            self.view.status = 'Pass %d of %d  (%.0f ms)' % (passes, quality[2], ms)
        else:
            self.view.status = 'Draft  (%.0f ms)' % ms
        self.view.update()
        self._update_info(pairs)

    def _on_lens_loaded(self, path, surfaces, error):
        if path != self._lens_path:
            return
        self._surfaces = surfaces
        self.view.message = '' if surfaces else (error or 'Could not load this lens.')
        self._update_info()

    # -- Qt events ------------------------------------------------------

    def showEvent(self, event):
        super(LensBrowserWindow, self).showEvent(event)
        self._update_target_label()
        self._schedule_render()
        self._thumb_timer.start()

    def resizeEvent(self, event):
        super(LensBrowserWindow, self).resizeEvent(event)
        if hasattr(self, '_thumb_timer'):
            self._thumb_timer.start()

    def _settings(self):
        return QtCore.QSettings('FlareSim', 'LensBrowser')

    def _restore_layout(self):
        """Window size and divider positions from the last session."""
        st = self._settings()
        for key, restore in (('geometry', self.restoreGeometry),
                             ('main_split', self.main_split.restoreState),
                             ('view_split', self.view_split.restoreState)):
            value = st.value(key)
            if value is not None:
                restore(value)

    def _restore_preview(self):
        """Preview exposure, light and quality from the last session."""
        st = self._settings()
        st.beginGroup('preview')
        d = PREVIEW_DEFAULTS
        self.exposure.setValue(_setting_float(st.value('exposure'), d['exposure']))
        self.show_source.setChecked(_setting_bool(st.value('show_light'), d['show_light']))
        self.intensity.setValue(_setting_float(st.value('intensity'), d['intensity']))
        self.fov.setValue(_setting_float(st.value('fov'), d['fov']))
        quality = int(_setting_float(st.value('quality'), d['quality']))
        if 0 <= quality < len(QUALITY):
            self.quality.setCurrentIndex(quality)
        colour = QtGui.QColor(str(st.value('colour') or d['colour']))
        st.endGroup()
        self._source_colour = colour if colour.isValid() else QtGui.QColor(d['colour'])
        self._update_colour_button()

    def _save_preview(self):
        st = self._settings()
        st.beginGroup('preview')
        st.setValue('exposure', self.exposure.value())
        st.setValue('show_light', self.show_source.isChecked())
        st.setValue('intensity', self.intensity.value())
        st.setValue('fov', self.fov.value())
        st.setValue('quality', self.quality.currentIndex())
        st.setValue('colour', self._source_colour.name())
        st.endGroup()

    def _reset_preview_light(self):
        d = PREVIEW_DEFAULTS
        self.intensity.setValue(d['intensity'])
        self.fov.setValue(d['fov'])
        self.quality.setCurrentIndex(d['quality'])
        self._source_colour = QtGui.QColor(d['colour'])
        self._update_colour_button()
        self._schedule_render()
        self._save_preview()

    def _save_layout(self):
        st = self._settings()
        st.setValue('geometry', self.saveGeometry())
        st.setValue('main_split', self.main_split.saveState())
        st.setValue('view_split', self.view_split.saveState())

    def closeEvent(self, event):
        global _window
        if getattr(self, '_colour_dialog', None) is not None:
            self._colour_dialog.accept()
        self._save_layout()
        self._save_preview()
        if self._renderer is not None:
            self._renderer.stop()
            self._renderer = None
        if self._thumbs is not None:
            self._thumbs.stop()
            self._thumbs = None
        if self._probe is not None:
            self._probe.stop()
            self._probe = None
        if _window is self:
            _window = None
        super(LensBrowserWindow, self).closeEvent(event)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

_window = None


def show_window(node=None):
    """Open the Lens Browser (one window, reused), optionally tied to a node."""
    global _window
    if _window is None:
        _window = LensBrowserWindow()
        _window.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
    if node is not None:
        _window.set_node(node)
    else:
        _window._update_target_label()
    _window.show()
    _window.raise_()
    _window.activateWindow()
    return _window


def show_for_node(node):
    """Called from the Lens Browser button on FlareSim / FlareSim3D."""
    return show_window(node)


def register():
    """Add the Lens Browser to the Window menu."""
    nuke.menu('Nuke').addCommand('Window/FlareSim Lens Browser', show_window)
