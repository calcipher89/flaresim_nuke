# ============================================================================
# FlareSim_Log.py — FlareSim+ debug log and debug report
#
# Errors are always written to the log file.  Debug lines (knob events,
# panel sizes, lens lookups, Lens Browser actions) are only written while
# debug logging is on, so it costs nothing in normal use.  Turn it on with
# Help > FlareSim+ > Debug Logging, or for a whole session or farm job with
# the environment variable FLARESIM_DEBUG=1.
#
# The log lives in ~/.nuke/FlareSim/logs/FlareSim_<machine>.log.  Help >
# FlareSim+ > Save Debug Report writes one text file with the machine,
# Nuke and FlareSim+ setup, every FlareSim+ node, the layout of open
# FlareSim+ panels and the end of the log: that file is what to send when
# something goes wrong.
# ============================================================================

import datetime
import json
import os
import platform
import socket
import sys
import threading
import traceback

USER_DIR = os.path.join(os.path.expanduser('~'), '.nuke', 'FlareSim')
LOG_DIR = os.path.join(USER_DIR, 'logs')
SETTINGS = os.path.join(USER_DIR, 'debug.json')
MAX_BYTES = 5 * 1024 * 1024        # then the log moves to .1 and starts again
REPORT_LINES = 500                 # log lines included in a report

_lock = threading.Lock()
_state = {'enabled': None, 'warned': False}


def _host():
    try:
        return socket.gethostname().split('.')[0] or 'unknown'
    except Exception:
        return 'unknown'


def log_path():
    return os.path.join(LOG_DIR, 'FlareSim_%s.log' % _host()).replace('\\', '/')


def _env_flag():
    v = os.environ.get('FLARESIM_DEBUG', '').strip().lower()
    if v in ('1', 'true', 'on', 'yes'):
        return True
    if v in ('0', 'false', 'off', 'no'):
        return False
    return None


def enabled():
    """True while debug lines are written."""
    on = _state['enabled']
    if on is None:
        on = _env_flag()
        if on is None:
            try:
                with open(SETTINGS) as f:
                    on = bool(json.load(f).get('debug_log', False))
            except Exception:
                on = False
        _state['enabled'] = on
    return on


def set_enabled(on):
    """Turn debug logging on or off, and remember it for the next session
    (FLARESIM_DEBUG still wins when it is set)."""
    _state['enabled'] = bool(on)
    try:
        os.makedirs(USER_DIR, exist_ok=True)
        with open(SETTINGS, 'w') as f:
            json.dump({'debug_log': bool(on)}, f)
    except Exception as e:
        _stderr('could not save the debug setting: %s' % e)
    _write('INFO', 'log', 'debug logging %s' % ('on' if on else 'off'))


def _stderr(text):
    try:
        sys.stderr.write('FlareSim: %s\n' % text)
    except Exception:
        pass


def _format(fmt, args):
    if not args:
        return str(fmt)
    try:
        return fmt % args
    except Exception:
        return '%s %r' % (fmt, args)


def _write(level, area, text):
    stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    line = '%s %-5s %-8s %s\n' % (stamp, level, area, text.replace('\n', '\n    '))
    path = log_path()
    try:
        with _lock:
            os.makedirs(LOG_DIR, exist_ok=True)
            try:
                if os.path.getsize(path) > MAX_BYTES:
                    os.replace(path, path + '.1')
            except OSError:
                pass
            with open(path, 'a', encoding='utf-8') as f:
                f.write(line)
    except Exception as e:
        if not _state['warned']:
            _state['warned'] = True
            _stderr('could not write the log %s: %s' % (path, e))


def debug(area, fmt, *args):
    """A debug line, only written while debug logging is on.  Formatting
    is skipped when it is off."""
    if enabled():
        _write('DEBUG', area, _format(fmt, args))


def info(area, fmt, *args):
    _write('INFO', area, _format(fmt, args))


def error(area, fmt, *args):
    """Always written to the log, and to the terminal."""
    text = _format(fmt, args)
    _write('ERROR', area, text)
    _stderr('%s: %s' % (area, text))


def exception(area, fmt='', *args):
    """error() plus the traceback of the exception being handled.  The
    traceback goes to the log only."""
    text = _format(fmt, args) if fmt else 'error'
    exc = sys.exc_info()[1]
    if exc is not None:
        text = '%s: %s' % (text, exc)
    _write('ERROR', area, text + '\n' + traceback.format_exc().rstrip())
    _stderr('%s: %s' % (area, text))


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

_POLICIES = {0: 'Fixed', 1: 'Min', 4: 'Max', 5: 'Pref', 3: 'MinExp',
             7: 'Exp', 13: 'Ignored'}


def _policy(p):
    try:
        return _POLICIES.get(int(p), str(int(p)))
    except Exception:
        return str(p)


def describe(w):
    """One line about a widget: class, size, size hint, minimum size hint
    and vertical size policy."""
    if w is None:
        return 'none'
    try:
        hint, mhint = w.sizeHint(), w.minimumSizeHint()
        text = '%s %dx%d hint %dx%d min %dx%d v=%s' % (
            w.metaObject().className(), w.width(), w.height(),
            hint.width(), hint.height(), mhint.width(), mhint.height(),
            _policy(w.sizePolicy().verticalPolicy()))
        if not w.isVisible():
            text += ' hidden'
        return text
    except RuntimeError:
        return 'deleted'
    except Exception as e:
        return 'unknown (%s)' % e


def describe_panel(panel, stack=None, viewport=None):
    """Lines about an open panel: its tabs, scroll area and window."""
    lines = ['panel   ' + describe(panel)]
    try:
        if stack is not None:
            lines.append('tabs    ' + describe(stack))
            current = stack.currentWidget()
            for i in range(stack.count()):
                page = stack.widget(i)
                mark = '*' if page is current else ' '
                lines.append('  %s page %d  %s' % (mark, i, describe(page)))
        if viewport is not None:
            lines.append('scroll  ' + describe(viewport))
        lines.append('parents')
        w = panel.parentWidget()
        for _ in range(12):        # the chain up to the window, for the report
            if w is None:
                break
            text = describe(w)
            try:
                if w.minimumHeight() > 0 or w.maximumHeight() < 16777215:
                    text += ' minh %d maxh %d' % (w.minimumHeight(), w.maximumHeight())
                if hasattr(w, 'widgetResizable'):
                    text += ' resizable' if w.widgetResizable() else ' not resizable'
            except RuntimeError:
                break
            lines.append('  ' + text)
            if w.isWindow():
                break
            w = w.parentWidget()
        win = panel.window()
        lines.append('window  %s%s' % (describe(win),
                     ' floating' if win.isWindow() and win.parentWidget() is None else ''))
    except RuntimeError:
        lines.append('(panel deleted)')
    return lines


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _tail(path, n):
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            return f.readlines()[-n:]
    except OSError:
        return []


def _knob(node, name):
    k = node.knobs().get(name)
    try:
        return k.value() if k is not None else None
    except Exception:
        return None


def report_text():
    lines = []
    add = lines.append
    add('FlareSim+ debug report')
    add('Written  %s' % datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    add('Machine  %s (%s)' % (_host(), platform.platform()))
    add('Python   %s' % sys.version.split()[0])
    try:
        import nuke
    except ImportError:
        nuke = None
    if nuke is not None:
        add('Nuke     %s%s' % (getattr(nuke, 'NUKE_VERSION_STRING', '?'),
                               '' if getattr(nuke, 'GUI', True) else ' (no GUI)'))
    try:
        from PySide2 import QtCore
        add('Qt       PySide2 %s' % QtCore.qVersion())
    except Exception:
        try:
            from PySide6 import QtCore
            add('Qt       PySide6 %s' % QtCore.qVersion())
        except Exception:
            add('Qt       none')
    add('Plugin   %s' % os.path.dirname(os.path.abspath(__file__)).replace('\\', '/'))
    add('Debug    %s (log %s)' % ('on' if enabled() else 'off', log_path()))
    add('')
    add('Environment')
    for key in sorted(os.environ):
        if key.startswith('FLARESIM') or key in ('NUKE_PATH', 'CUDA_VISIBLE_DEVICES',
                                                  'QT_SCALE_FACTOR', 'QT_AUTO_SCREEN_SCALE_FACTOR'):
            add('  %s=%s' % (key, os.environ[key]))
    try:
        import FlareSim_Looks
        add('Lens folders')
        for root in FlareSim_Looks.lens_search_roots():
            add('  %s%s' % (root, '' if os.path.isdir(root) else '  (missing)'))
    except Exception as e:
        add('Lens folders: %s' % e)
    add('')

    if nuke is not None:
        try:
            nodes = [n for n in nuke.allNodes(recurseGroups=True)
                     if n.Class() in ('FlareSim', 'FlareSim3D')]
        except Exception:
            nodes = []
        add('Nodes (%d)' % len(nodes))
        for n in nodes:
            lens = _knob(n, 'lens_file') or ''
            try:
                import FlareSim_Looks
                found = FlareSim_Looks.resolve_lens(lens) if lens else ''
            except Exception:
                found = '?'
            inputs = []
            for i in range(n.inputs()):
                up = n.input(i)
                inputs.append('%d=%s' % (i, up.name() if up is not None else '-'))
            add('  %s (%s)%s' % (n.fullName(), n.Class(),
                                  '  ERROR' if n.hasError() else ''))
            add('    lens %s -> %s' % (lens or '(none)', found or 'NOT FOUND'))
            add('    inputs %s' % ' '.join(inputs))
            for name in ('look_name', 'show_advanced', 'quality', 'source_mode',
                         'use_camera', 'camera_info', 'clip_to'):
                v = _knob(n, name)
                if v is not None:
                    add('    %s = %s' % (name, v))
        add('')
        try:
            import FlareSim_Header
            panels = FlareSim_Header.open_panel_reports()
        except Exception as e:
            panels = [('?', ['could not read the open panels: %s' % e])]
        add('Open panels (%d)' % len(panels))
        for name, plines in panels:
            add('  ' + name)
            lines.extend('    ' + l for l in plines)
        add('')

    tail = _tail(log_path(), REPORT_LINES)
    add('Log (last %d lines of %s)' % (len(tail), log_path()))
    lines.extend(l.rstrip('\n') for l in tail)
    return '\n'.join(lines) + '\n'


def save_report(path=None):
    """Write the debug report and return its path."""
    if path is None:
        stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        path = os.path.join(os.path.expanduser('~'),
                            'FlareSim_debug_%s_%s.txt' % (_host(), stamp))
    with open(path, 'w', encoding='utf-8') as f:
        f.write(report_text())
    return path.replace('\\', '/')


# ---------------------------------------------------------------------------
# Help menu
# ---------------------------------------------------------------------------

def toggle_from_menu():
    import nuke
    set_enabled(not enabled())
    if enabled():
        nuke.message('FlareSim+ debug logging is on.\n\nLog: %s\n\n'
                     'Repeat what went wrong, then use Help > FlareSim+ > '
                     'Save Debug Report.' % log_path())
    else:
        nuke.message('FlareSim+ debug logging is off.')


def report_from_menu():
    import nuke
    try:
        path = save_report()
    except Exception as e:
        nuke.message('Could not write the FlareSim+ debug report:\n%s' % e)
        return
    nuke.message('FlareSim+ debug report saved to\n\n%s' % path)
