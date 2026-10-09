"""
FlareSim_LensBrowser.py — the FlareSim Lens Browser window.

A standalone Qt window for picking a lens and building a flare look:

  * lens thumbnails under the preview, each a small render of that lens's
    flare, with search, maker, focal length, speed and type filters; drag
    the divider to go from a one-row carousel to a grid
  * a live flare preview: drag the light around to see the ghosts move
  * look controls (gain, aperture, ghost blur) that drive the preview
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
import os
import re
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


class LensInfo(object):
    __slots__ = ('path', 'name', 'maker', 'focal', 'fnum', 'kind', 'label', 'search')

    def __init__(self, path):
        self.path = path.replace('\\', '/')
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
    """Folders scanned for .lens files: the bundled library plus any in
    FLARESIM_LENS_PATH."""
    folders = [BUNDLED_LENS_ROOT]
    for d in os.environ.get('FLARESIM_LENS_PATH', '').split(os.pathsep):
        if d.strip():
            folders.append(d.strip())
    return folders


_lens_cache = {}


def scan_lenses(folders):
    """All lenses in folders (recursively), sorted by label.  Cached."""
    out, seen = [], set()
    for folder in folders:
        key = os.path.normcase(os.path.abspath(folder))
        if key not in _lens_cache:
            found = []
            for root, _dirs, files in os.walk(folder):
                for fname in files:
                    if fname.lower().endswith('.lens'):
                        found.append(LensInfo(os.path.join(root, fname)))
            _lens_cache[key] = found
        for info in _lens_cache[key]:
            norm = os.path.normcase(info.path)
            if norm not in seen:
                seen.add(norm)
                out.append(info)
    out.sort(key=lambda l: l.label.lower())
    return out


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


FSP_API_VERSION = 1

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
    except (OSError, AttributeError) as e:
        _lib_error = 'Could not load the live preview library %s: %s' % (path, e)
        return None, _lib_error
    _lib = lib
    return _lib, None


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
        grid and passes."""
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
    exposure."""

    sourceMoved = QtCore.Signal(float, float, bool)   # u, v (0..1), dragging
    dragStateChanged = QtCore.Signal(bool)
    exposureNudged = QtCore.Signal(float)

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

        p.setPen(QtGui.QColor(190, 190, 190))
        if self.message:
            p.drawText(r.adjusted(24, 24, -24, -24),
                       QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap, self.message)
        hint = 'Drag to move the light   |   Wheel: exposure'
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
        if event.button() == QtCore.Qt.LeftButton:
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
# (highlight, spectral, per-surface overrides) is carried through untouched.
_EDITED_KNOBS = ('flare_gain', 'aperture_blades', 'aperture_rotation',
                 'ghost_blur', 'ghost_blur_passes')

QUALITY = [('Draft', 32, 4), ('Good', 48, 8), ('Best', 64, 16)]   # grid, passes


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


def _node_alive(node):
    try:
        node.name()
        return True
    except (ValueError, AttributeError):
        return False


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
        self._source_colour = QtGui.QColor(255, 255, 255)
        self._surfaces = 0
        self._items = {}               # lens path -> grid item
        self._has_thumb = set()

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
        else:
            self.view.message = err

        self._thumb_timer = QtCore.QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.setInterval(80)
        self._thumb_timer.timeout.connect(self._queue_thumbnails)

        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(30)
        self._refresh_timer.timeout.connect(self._request_render)

        self._restore_layout()
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
        self.maker_combo = QtWidgets.QComboBox()
        counts = {}
        for l in self._lenses:
            counts[l.maker] = counts.get(l.maker, 0) + 1
        self.maker_combo.addItem('All makers (%d)' % len(self._lenses), '')
        for maker in sorted(counts, key=lambda m: (m == 'Other', m.lower())):
            self.maker_combo.addItem('%s (%d)' % (maker, counts[maker]), maker)
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
        for l in self._lenses:
            item = QtWidgets.QListWidgetItem(l.name)
            item.setData(QtCore.Qt.UserRole, l.path)
            item.setData(LensTileDelegate.SPEC_ROLE, self._item_spec(l))
            item.setToolTip('%s\n%s, %s\n%s' % (l.label, l.maker, l.kind,
                                                os.path.basename(l.path)))
            self.grid.addItem(item)
            self._items[l.path] = item
        self.grid.setMinimumHeight(90)

        centre = QtWidgets.QWidget()
        cv = QtWidgets.QVBoxLayout(centre)
        cv.setContentsMargins(0, 4, 0, 0)
        self.view = PreviewView()
        cv.addWidget(self.view, 1)
        under = QtWidgets.QHBoxLayout()
        under.addWidget(QtWidgets.QLabel('Exposure'))
        self.exposure = SliderRow(-6.0, 6.0, 0.0, decimals=2, step=0.25)
        under.addWidget(self.exposure, 1)
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
        open_btn = QtWidgets.QPushButton('Open .lens File...')
        open_btn.clicked.connect(self._browse_lens_file)
        lg.addWidget(open_btn)
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
        lk.addWidget(load_look)
        lv.addWidget(look_box)
        lv.addStretch(1)

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
        rv.addWidget(flare_box)

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
        note = QtWidgets.QLabel('These shape the preview only. The node keeps its own '
                                'source and camera settings.')
        note.setWordWrap(True)
        note.setStyleSheet('color: #999;')
        ll.addRow(note)
        rv.addWidget(light_box)
        rv.addStretch(1)

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
        scroll.setMaximumWidth(scroll.minimumWidth() + 120)
        self.exposure.setMinimumWidth(160)
        left.setMinimumWidth(260)
        left.setMaximumWidth(400)
        splitter.setCollapsible(1, False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([300, 900, 330])
        self.main_split = splitter

        # Signals.
        self.search.textChanged.connect(self._apply_filters)
        for combo in (self.maker_combo, self.focal_combo, self.speed_combo, self.type_combo):
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
        self.quality.currentIndexChanged.connect(self._schedule_render)
        self.show_source.toggled.connect(self._schedule_render)
        self.view.sourceMoved.connect(self._on_source_moved)
        self.view.dragStateChanged.connect(self._on_drag_state)
        self.view.exposureNudged.connect(
            lambda d: self.exposure.setValue(self.exposure.value() + d))

        QShortcut(QtGui.QKeySequence('Ctrl+F'), self, self.search.setFocus)
        QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_PageUp), self,
                            lambda: self._step_lens(-1))
        QShortcut(QtGui.QKeySequence(QtCore.Qt.Key_PageDown), self,
                            lambda: self._step_lens(1))
        self._update_colour_button()

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
        _f, flo, fhi = FOCAL_RANGES[self.focal_combo.currentIndex()]
        _s, slo, shi = SPEEDS[self.speed_combo.currentIndex()]
        kind = self.type_combo.currentText()
        out = []
        for l in self._lenses:
            ok = True
            if maker and l.maker != maker:
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

    def _browse_lens_file(self):
        start = os.path.dirname(self._lens_path) if self._lens_path else BUNDLED_LENS_ROOT
        path, _f = QtWidgets.QFileDialog.getOpenFileName(
            self, 'Open Lens File', start, 'Lens files (*.lens);;All files (*)')
        if path:
            self.set_lens(path)

    def set_lens(self, path):
        """Show a lens: select it in the grid (if listed) and preview it."""
        self._lens_path = path.replace('\\', '/')
        item = self._items.get(self._lens_path)
        if item is not None and not item.isHidden():
            self._select_item(item)
        else:
            self.grid.clearSelection()
        self._surfaces = 0
        self._update_info()
        self._schedule_render()

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
        else:
            self.look_desc.setText('No looks found.')

    def _load_look(self):
        i = self.look_combo.currentIndex()
        if not 0 <= i < len(self._looks):
            return
        look = self._looks[i]
        lens = FlareSim_Looks.resolve_lens(look.get('lens', ''))
        if look.get('lens') and not lens:
            QtWidgets.QMessageBox.warning(self, 'FlareSim', 'Lens not found: %s' % look['lens'])
        self._set_look_values(look.get('knobs', {}))
        self._look_name = look['name']
        if lens:
            self.set_lens(lens)

    def _set_look_values(self, values):
        rows = {'flare_gain': self.gain, 'aperture_blades': self.blades,
                'aperture_rotation': self.rotation, 'ghost_blur': self.blur,
                'ghost_blur_passes': self.blur_passes}
        for k, row in rows.items():
            if k in values:
                row.blockSignals(True)
                row.setValue(values[k])
                row.blockSignals(False)
        self._look_extra = {k: v for k, v in values.items() if k not in _EDITED_KNOBS}
        if self._look_extra:
            self.extra_label.setText('Also from the look: %d more settings (highlight, '
                                     'spectral, surfaces...) kept as they are.'
                                     % len(self._look_extra))
        else:
            self.extra_label.setText('')
        self._schedule_render()

    def current_look(self, name=''):
        """The window's settings as a look dict."""
        knobs = dict(self._look_extra)
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
        # Leave per-surface overrides on the node alone; carry through the rest.
        self._set_look_values(values)
        for k, row in (('fov_h', self.fov), ('source_intensity', self.intensity)):
            if k in knobs:
                row.setValue(float(knobs[k].value()))
        self._look_name = ''
        lens = knobs['lens_file'].value() if 'lens_file' in knobs else ''
        if lens:
            lens = nuke.filenameFilter(lens) if hasattr(nuke, 'filenameFilter') else lens
        if lens and os.path.isfile(lens):
            self.set_lens(lens)

    def _apply_to_node(self):
        if not self._lens_path:
            QtWidgets.QMessageBox.information(self, 'FlareSim', 'Pick a lens first.')
            return
        nodes = self._target_nodes(create=True)
        self._update_target_label()
        if not nodes:
            return
        look = self.current_look(self._look_name)
        look['lens'] = self._lens_path
        # Keep a node's per-surface tweaks when only the look changes; a new
        # lens has different surfaces, so reset them then.
        same_lens = all(os.path.normcase(n['lens_file'].value().replace('\\', '/')) ==
                        os.path.normcase(self._lens_path) for n in nodes)
        warnings = FlareSim_Looks.apply_look(look, nodes, reset_surfaces=not same_lens)
        if warnings:
            QtWidgets.QMessageBox.warning(self, 'FlareSim', '\n'.join(warnings))
        nuke.tprint('FlareSim: applied %s to %s' % (
            os.path.basename(self._lens_path), ', '.join(n.name() for n in nodes)))

    # -- preview --------------------------------------------------------

    def _choose_colour(self):
        c = QtWidgets.QColorDialog.getColor(self._source_colour, self, 'Light Colour')
        if c.isValid():
            self._source_colour = c
            self._update_colour_button()
            self._schedule_render()

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
        w, h = self.view.render_size()
        u, v = self.view.source
        c = self._source_colour
        _label, grid, passes = QUALITY[self.quality.currentIndex()]
        self._renderer.request({
            'lens': self._lens_path,
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
        })

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

    def _save_layout(self):
        st = self._settings()
        st.setValue('geometry', self.saveGeometry())
        st.setValue('main_split', self.main_split.saveState())
        st.setValue('view_split', self.view_split.saveState())

    def closeEvent(self, event):
        global _window
        self._save_layout()
        if self._renderer is not None:
            self._renderer.stop()
            self._renderer = None
        if self._thumbs is not None:
            self._thumbs.stop()
            self._thumbs = None
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
