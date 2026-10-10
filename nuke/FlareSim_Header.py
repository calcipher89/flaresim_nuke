"""
FlareSim_Header.py — the FlareSim+ header at the top of the node's panel.

The header is the FlareSim+ wordmark over a card for the node's lens: a
small render of its look, the lens name, the look name, and the camera's
focal length when a camera is connected (or the lens's own focal length and
f-stop).  It is drawn into a PNG (plus an @2x copy for high-DPI screens)
under ~/.nuke/FlareSim/headers and shown by the node's "header" Text knob.

It is set when a node is created or loaded, so it is there the first time
the panel opens, and redrawn when the lens, the look or the inputs change.
Nuke draws a Text knob's text once, when the panel is built, so a change
while the panel is open is also pushed to the panel's label.

Once a panel is open, the header is redrawn to the panel's width when the
panel is resized (the wordmark stays centred and the lens card stretches),
section titles get a spectral tick, and a floating panel is sized to the
tab you're on rather than its tallest tab.  Header images
are cached, so a script with many FlareSim nodes draws each header once.
The look thumbnail is the Lens Browser's preview, saved on
Apply to Node; until there is one, the lens thumbnail from the browser's
cache is used.
"""

import hashlib
import json
import os
import re
import sys

import nuke

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    from PySide2 import QtCore, QtGui, QtWidgets

NODE_CLASSES = ('FlareSim', 'FlareSim3D')
HEADER_VERSION = 1
HEADER_DIR = os.path.join(os.path.expanduser('~'), '.nuke', 'FlareSim', 'headers')
LOOK_THUMB_DIR = os.path.join(HEADER_DIR, 'looks')
ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icons')
WORDMARK = os.path.join(ICON_DIR, 'flaresim_plus_wordmark.png')

# Header layout, in logical pixels.  The width follows the panel; SIZE is
# the width used before the panel is open, and MIN_WIDTH the narrowest.
SIZE = (380, 128)
MIN_WIDTH = 300
_widths = {}      # node full name -> header width in its open panel
THUMB_RECT = (16, 62, 108, 56)
TRIGGER_KNOBS = ('showPanel', 'lens_file', 'look_name', 'inputChange')

# Divider titles on the FlareSim tab that get a spectral tick, as in the
# header's line.
SECTION_TITLES = ('Source', 'Source Extraction', 'Matte', 'Ghost', 'Distance',
                  'Camera', 'Aperture', 'Spectral', 'Highlight', 'Post-process',
                  'Output')
TICK_SIZE = (18, 3)
SPECTRUM = ('#ff5a5a', '#ffb340', '#f5ec5b', '#48d17a', '#3ec6ff', '#7b6cff')


def _qt_ready():
    return QtWidgets.QApplication.instance() is not None


def _knob(node, name, default=None):
    knobs = node.knobs()
    return knobs[name].value() if name in knobs else default


def lens_path(node):
    path = _knob(node, 'lens_file', '') or ''
    if path and hasattr(nuke, 'filenameFilter'):
        path = nuke.filenameFilter(path)
    return path.replace('\\', '/')


def camera_focal(node):
    """Focal length (mm) of a camera connected to the node, or 0."""
    for i in range(node.inputs()):
        inp = node.input(i)
        if inp is None or not inp.Class().startswith('Camera'):
            continue
        if 'focal' in inp.knobs():
            try:
                return float(inp['focal'].value())
            except (TypeError, ValueError):
                pass
    return 0.0


def look_key(node):
    """Identifies the node's lens and look settings, so a saved look
    thumbnail is used only while the node still matches it."""
    import FlareSim_Looks
    look = FlareSim_Looks.capture_look(node, '')
    look['lens'] = lens_path(node)
    look['name'] = _knob(node, 'look_name', '') or ''
    text = json.dumps(look, sort_keys=True)
    return hashlib.sha1(text.encode('utf-8')).hexdigest()


def look_thumb_path(node):
    return os.path.join(LOOK_THUMB_DIR, look_key(node) + '.png')


def save_look_thumbnail(node, image):
    """Save the Lens Browser's preview as the node's look thumbnail.
    `image` is a QImage; it is cropped to the card's shape."""
    if image is None or image.isNull():
        return
    tw, th = THUMB_RECT[2] * 2, THUMB_RECT[3] * 2
    scaled = image.scaled(tw, th, QtCore.Qt.KeepAspectRatioByExpanding,
                          QtCore.Qt.SmoothTransformation)
    x = (scaled.width() - tw) // 2
    y = (scaled.height() - th) // 2
    path = look_thumb_path(node)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    scaled.copy(x, y, tw, th).save(path)


def _thumbnail(node, lens):
    path = look_thumb_path(node)
    if os.path.isfile(path):
        return path
    if lens:
        try:
            import FlareSim_LensBrowser
            path = FlareSim_LensBrowser.thumbnail_path(lens)
            if os.path.isfile(path):
                return path
        except Exception:
            pass
    return ''


def header_info(node):
    """The text and thumbnail the header shows for a node."""
    lens = lens_path(node)
    name, focal, fnum = '', 0.0, 0.0
    if lens:
        try:
            import FlareSim_LensBrowser
            info = FlareSim_LensBrowser.LensInfo(lens)
            name, focal, fnum = info.name, info.focal, info.fnum
        except Exception:
            name = os.path.splitext(os.path.basename(lens))[0].replace('_', ' ')
    look = _knob(node, 'look_name', '') or ''
    cam = camera_focal(node)
    if cam > 0:
        pill = 'cam · %g mm' % round(cam, 1)
    else:
        bits = []
        if 0 < focal < 1e6:
            bits.append('%g mm' % round(focal, 1))
        if fnum > 0:
            bits.append('f/%g' % fnum)
        pill = ' · '.join(bits)
    return {
        'lens': name,
        'look': ('Look: ' + look) if look else ('Look: Custom' if lens else ''),
        'pill': pill,
        'thumb': _thumbnail(node, lens) if lens else '',
    }


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------

def _font(px, weight=None):
    f = QtGui.QFont(QtWidgets.QApplication.font())
    f.setPixelSize(px)
    if weight is not None:
        f.setWeight(weight)
    return f


def _elide(painter, text, width):
    return painter.fontMetrics().elidedText(text, QtCore.Qt.ElideRight, int(width))


def draw_header(info, scale=1, width=None):
    """Draw the header as a QImage at `scale` x the logical size, `width`
    logical pixels wide (SIZE by default)."""
    w, h = max(int(width or SIZE[0]), MIN_WIDTH), SIZE[1]
    img = QtGui.QImage(w * scale, h * scale, QtGui.QImage.Format_ARGB32_Premultiplied)
    img.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    p.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
    p.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
    p.scale(scale, scale)

    # Background.
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(QtGui.QColor('#1b1c1f'))
    p.drawRoundedRect(QtCore.QRectF(0, 0, w, h), 5, 5)

    # Wordmark (with its spectral line), centred along the top.
    mark = QtGui.QImage(WORDMARK)
    if not mark.isNull():
        mh = 44.0
        mw = mh * mark.width() / float(mark.height())
        p.drawImage(QtCore.QRectF((w - mw) / 2.0, 6, mw, mh), mark)
    else:
        p.setPen(QtGui.QColor('#f2f2f2'))
        p.setFont(_font(24, QtGui.QFont.Bold))
        p.drawText(QtCore.QRectF(0, 6, w, 34), QtCore.Qt.AlignCenter, 'FlareSim+')

    # Lens card.
    card = QtCore.QRectF(10, 56, w - 20, h - 62)
    p.setPen(QtGui.QPen(QtGui.QColor('#34353a'), 1))
    p.setBrush(QtGui.QColor('#25262a'))
    p.drawRoundedRect(card, 4, 4)

    tx, ty, tw, th = THUMB_RECT
    thumb_rect = QtCore.QRectF(tx, ty, tw, th)
    clip = QtGui.QPainterPath()
    clip.addRoundedRect(thumb_rect, 3, 3)
    p.save()
    p.setClipPath(clip)
    p.fillRect(thumb_rect, QtGui.QColor('#0d0f14'))
    thumb = QtGui.QImage(info['thumb']) if info['thumb'] else QtGui.QImage()
    if not thumb.isNull():
        src = QtCore.QRectF(thumb.rect())
        aspect = tw / float(th)
        if src.width() / src.height() > aspect:
            cw = src.height() * aspect
            src = QtCore.QRectF(src.x() + (src.width() - cw) / 2.0, src.y(), cw, src.height())
        else:
            ch = src.width() / aspect
            src = QtCore.QRectF(src.x(), src.y() + (src.height() - ch) / 2.0, src.width(), ch)
        p.drawImage(thumb_rect, thumb, src)
    p.restore()

    x = tx + tw + 12
    text_w = card.right() - x - 8
    if info['lens']:
        p.setPen(QtGui.QColor('#f1f1f3'))
        p.setFont(_font(13, QtGui.QFont.DemiBold))
        p.drawText(QtCore.QPointF(x, ty + 15), _elide(p, info['lens'], text_w))
        p.setPen(QtGui.QColor('#9a9ba3'))
        p.setFont(_font(11))
        p.drawText(QtCore.QPointF(x, ty + 31), _elide(p, info['look'], text_w))
        if info['pill']:
            p.setFont(_font(10))
            pw = p.fontMetrics().horizontalAdvance(info['pill']) + 16 \
                if hasattr(p.fontMetrics(), 'horizontalAdvance') \
                else p.fontMetrics().width(info['pill']) + 16
            pill = QtCore.QRectF(x, ty + 38, min(pw, text_w), 16)
            p.setPen(QtGui.QPen(QtGui.QColor('#5c5d63'), 1))
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawRoundedRect(pill, 8, 8)
            p.setPen(QtGui.QColor('#c9c9cf'))
            p.drawText(pill, QtCore.Qt.AlignCenter, info['pill'])
    else:
        p.setPen(QtGui.QColor('#9a9ba3'))
        p.setFont(_font(11))
        p.drawText(QtCore.QRectF(x, ty, text_w, th), QtCore.Qt.AlignVCenter | QtCore.Qt.TextWordWrap,
                   'No lens yet.  Click Lens Browser to pick one.')
    p.end()
    return img


def header_image(info, width=None):
    """Path of the header PNG for `info` at `width`, drawn on first use."""
    width = max(int(width or SIZE[0]), MIN_WIDTH)
    stamp = ''
    if info['thumb']:
        try:
            st = os.stat(info['thumb'])
            stamp = '%d:%d' % (int(st.st_mtime), st.st_size)
        except OSError:
            pass
    key = json.dumps([HEADER_VERSION, info, stamp, width, SIZE[1]], sort_keys=True)
    digest = hashlib.sha1(key.encode('utf-8')).hexdigest()
    path = os.path.join(HEADER_DIR, digest[:2], digest + '.png')
    if not os.path.isfile(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        draw_header(info, 2, width).save(path[:-4] + '@2x.png')
        draw_header(info, 1, width).save(path)
    return path


def refresh(node):
    """Redraw a FlareSim node's header."""
    if node is None or 'header' not in node.knobs() or not _qt_ready():
        return
    try:
        width = _widths.get(node.fullName(), SIZE[0])
        path = header_image(header_info(node), width).replace('\\', '/')
        html = '<img src="%s" width="%d" height="%d">%s' % (
            path, width, SIZE[1], _node_tag(node))
        old = node['header'].value()
        if old != html:
            node['header'].setValue(html)
            _update_open_panels(node, old, html)
    except Exception as e:
        sys.stderr.write('FlareSim header: %s\n' % e)


def _node_tag(node):
    """An invisible tag naming the node, so its panel's label can be told
    apart from another node's showing the same lens."""
    return '<!--flaresim:%s-->' % node.fullName()


def _update_open_panels(node, old, html):
    """Show a new header in panels that are already open.  Nuke builds a
    Text knob's label when the panel opens and doesn't redraw it on
    setValue, so find the labels still showing the old header."""
    src = re.search(r'src="[^"]*"', old or '')
    if not src:
        return
    labels = [w for w in QtWidgets.QApplication.allWidgets()
              if isinstance(w, QtWidgets.QLabel) and src.group(0) in w.text()]
    tag = _node_tag(node)
    tagged = [w for w in labels if tag in w.text()]
    for w in (tagged or labels):
        w.setText(html)


def tick_image():
    """Path of the spectral tick drawn before section titles."""
    path = os.path.join(HEADER_DIR, 'tick_v%d.png' % HEADER_VERSION)
    if not os.path.isfile(path):
        os.makedirs(HEADER_DIR, exist_ok=True)
        for scale, out in ((2, path[:-4] + '@2x.png'), (1, path)):
            w, h = TICK_SIZE[0] * scale, TICK_SIZE[1] * scale
            img = QtGui.QImage(w, h, QtGui.QImage.Format_ARGB32_Premultiplied)
            img.fill(QtCore.Qt.transparent)
            grad = QtGui.QLinearGradient(0, 0, w, 0)
            for i, c in enumerate(SPECTRUM):
                grad.setColorAt(i / float(len(SPECTRUM) - 1), QtGui.QColor(c))
            p = QtGui.QPainter(img)
            p.setRenderHint(QtGui.QPainter.Antialiasing, True)
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(grad)
            p.drawRoundedRect(QtCore.QRectF(0, 0, w, h), h / 2.0, h / 2.0)
            p.end()
            img.save(out)
    return path.replace('\\', '/')


def _panel_of(label):
    """The widget holding a node's knobs: the nearest parent of the header
    label that also holds the section titles."""
    w = label.parentWidget()
    for _ in range(10):
        if w is None:
            return None
        names = set(l.text().strip() for l in w.findChildren(QtWidgets.QLabel))
        if len(names.intersection(SECTION_TITLES)) >= 2:
            return w
        w = w.parentWidget()
    return None


def _header_labels(node):
    tag = _node_tag(node)
    return [w for w in QtWidgets.QApplication.allWidgets()
            if isinstance(w, QtWidgets.QLabel) and tag in w.text()]


def style_panel(node):
    """Fit the header to the node's open panel, put a spectral tick before
    each section title and size a floating panel to its tab.  Nuke builds
    the panel's widgets after the showPanel callback, so this runs once the
    event loop is back."""
    for hl in _header_labels(node):
        # The header image sets its width, not the label: let the panel
        # shrink, and redraw the header to fit.
        hl.setMinimumWidth(1)
        panel = _panel_of(hl)
        if panel is None:
            continue
        _style_titles(panel)
        _HeaderFitter.attach(node, hl, panel)
        _fit_tabs(panel)


def _style_titles(panel):
    """A spectral tick before each section title.  Nuke right-aligns divider
    titles to the label column, so the titles are given one width and
    left-aligned, which lines the ticks up."""
    tick = '<img src="%s" width="%d" height="%d" style="vertical-align: middle">' \
           '&nbsp;&nbsp;' % ((tick_image(),) + TICK_SIZE)
    titles = []
    for l in panel.findChildren(QtWidgets.QLabel):
        text = l.text().strip()
        if text in SECTION_TITLES:
            l.setTextFormat(QtCore.Qt.RichText)
            l.setText(tick + text)
            titles.append(l)
        elif text.startswith(tick):
            titles.append(l)
    if not titles:
        return
    width = max(l.sizeHint().width() for l in titles)
    for l in titles:
        l.setMinimumWidth(width)
        l.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)


class _HeaderFitter(QtCore.QObject):
    """Redraws a node's header to its panel's width when the panel is shown
    or resized.  The redraw waits for resizing to pause."""

    def __init__(self, node, label, panel):
        super(_HeaderFitter, self).__init__(panel)
        self._name = node.fullName()
        self._label = label
        self._panel = panel
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self.fit)
        panel.installEventFilter(self)

    @classmethod
    def attach(cls, node, label, panel):
        fitter = panel.property('flaresim_header_fitter')
        if not isinstance(fitter, _HeaderFitter):
            fitter = cls(node, label, panel)
            panel.setProperty('flaresim_header_fitter', fitter)
        fitter._label = label
        fitter.fit()
        return fitter

    def eventFilter(self, obj, event):
        if obj is self._panel and event.type() in (QtCore.QEvent.Resize, QtCore.QEvent.Show):
            self._timer.start()
        return False

    def width(self):
        """The header width that leaves the same margin on both sides."""
        try:
            left = self._label.mapTo(self._panel, QtCore.QPoint(0, 0)).x()
        except RuntimeError:       # the label was deleted with its panel
            return 0
        return max(MIN_WIDTH, (self._panel.width() - 2 * max(left, 0)) // 4 * 4)

    def fit(self):
        w = self.width()
        if not w or _widths.get(self._name) == w:
            return
        _widths[self._name] = w
        node = nuke.toNode(self._name)
        if node is not None:
            refresh(node)


def _fit_tabs(panel):
    """Size a floating panel to the tab on show rather than to its tallest
    tab.  A docked panel fills its pane whatever its size, so it is left
    alone."""
    w = panel
    stack = None
    while w is not None:
        parent = w.parentWidget()
        if isinstance(parent, QtWidgets.QStackedWidget):
            stack = parent
            break
        w = parent
    if stack is None:
        return

    def apply(_index=None):
        win = stack.window()
        if win is None or isinstance(win, QtWidgets.QMainWindow) or win.isMaximized():
            return
        QtCore.QTimer.singleShot(0, fit)

    def fit():
        """Make the window as tall as the tab's knobs: what's around them
        (title bar, tabs, buttons) stays, the knob area fits its content."""
        try:
            win = stack.window()
            current = stack.currentWidget()
        except RuntimeError:       # the panel was closed
            return
        if win is None or current is None:
            return
        content = panel if (current is panel or current.isAncestorOf(panel)) else current
        need = win.height() - content.height() + content.sizeHint().height()
        try:
            need = min(need, win.screen().availableGeometry().height())
        except Exception:
            pass
        if need > 0 and abs(need - win.height()) > 4:
            win.resize(win.width(), need)

    if not stack.property('flaresim_fit_tabs'):
        stack.setProperty('flaresim_fit_tabs', True)
        stack.currentChanged.connect(apply)
    apply()


def _style_later(node):
    try:
        name = node.fullName()
    except Exception:
        return

    def run():
        try:
            n = nuke.toNode(name)
            if n is not None:
                style_panel(n)
        except Exception as e:
            sys.stderr.write('FlareSim header: %s\n' % e)
    QtCore.QTimer.singleShot(0, run)


def _on_create():
    refresh(nuke.thisNode())


def _on_knob_changed():
    k = nuke.thisKnob()
    if k is None:
        return
    if k.name() in TRIGGER_KNOBS:
        refresh(nuke.thisNode())
    # Show Advanced reveals more section titles.
    if k.name() in ('showPanel', 'show_advanced'):
        _style_later(nuke.thisNode())


def register():
    """Keep the header up to date on FlareSim and FlareSim3D nodes."""
    if not getattr(nuke, 'GUI', True):
        return
    for cls in NODE_CLASSES:
        nuke.addOnCreate(_on_create, nodeClass=cls)
        nuke.addKnobChanged(_on_knob_changed, nodeClass=cls)
