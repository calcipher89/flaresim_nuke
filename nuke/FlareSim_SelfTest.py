# FlareSim+ self-test
#
# Checks that FlareSim+ is installed and working on this machine and writes
# a plain-text report you can bring back for debugging.
#
# In a terminal (also as a farm job):
#
#     nuke -t /path/to/FlareSim/FlareSim_SelfTest.py
#     nuke -t /path/to/FlareSim/FlareSim_SelfTest.py --all --script shot.nk
#
#   --all            render every lens, not just a sample (slower)
#   --script PATH    also check the lens paths in this Nuke script
#   --out PATH       where to write the report (default: your home folder)
#
# In Nuke: Help > FlareSim+ > Self Test, or in the Script Editor:
#
#     import FlareSim_SelfTest; FlareSim_SelfTest.run()
#
# In the GUI the lens paths of the open script are checked too.

import ctypes
import datetime
import getpass
import os
import platform
import socket
import subprocess
import sys
import tempfile
import time
import traceback

import nuke

_HERE = os.path.dirname(os.path.abspath(__file__))

# Oldest NVIDIA driver for a CUDA 12.x build (CUDA minor version
# compatibility: the plugin carries code for each supported GPU, so any
# 12.x driver works).
MIN_DRIVER = 525
TEST_FORMAT = (512, 288)
SAMPLE_LENSES = 6

PASS, WARN, FAIL, INFO = 'PASS', 'WARN', 'FAIL', 'INFO'

last_report = None


class Report(object):
    def __init__(self):
        self.lines = []
        self.counts = {PASS: 0, WARN: 0, FAIL: 0}

    def section(self, title):
        self.lines.append('')
        self.lines.append('== %s' % title)

    def add(self, status, text, detail=None):
        if status in self.counts:
            self.counts[status] += 1
        self.lines.append('[%s] %s' % (status, text))
        for line in (detail or '').splitlines():
            self.lines.append('       %s' % line)

    def text(self):
        head = ['FlareSim+ self-test report',
                'Result: %s  (%d passed, %d warnings, %d failed)' % (
                    'FAILED' if self.counts[FAIL] else
                    'OK with warnings' if self.counts[WARN] else 'OK',
                    self.counts[PASS], self.counts[WARN], self.counts[FAIL])]
        return '\n'.join(head + self.lines) + '\n'


def _check(report, label, func):
    """Run one group of checks; a crash in it is reported, not raised."""
    report.section(label)
    try:
        func(report)
    except Exception:
        report.add(FAIL, '%s check crashed' % label, traceback.format_exc())


def _run_cmd(args):
    try:
        out = subprocess.check_output(args, stderr=subprocess.STDOUT, timeout=20)
        return out.decode('utf-8', 'replace').strip()
    except Exception:
        return None


def _os_name():
    for path in ('/etc/almalinux-release', '/etc/redhat-release', '/etc/os-release'):
        try:
            with open(path) as fh:
                text = fh.read()
        except OSError:
            continue
        if path.endswith('os-release'):
            for line in text.splitlines():
                if line.startswith('PRETTY_NAME='):
                    return line.split('=', 1)[1].strip().strip('"')
        return text.strip().splitlines()[0]
    return platform.platform()


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_machine(report):
    report.add(INFO, 'Machine: %s' % socket.gethostname())
    try:
        user = getpass.getuser()
    except Exception:
        user = '?'
    report.add(INFO, 'User: %s' % user)
    report.add(INFO, 'OS: %s' % _os_name())
    report.add(INFO, 'Nuke: %s (%s)' % (
        getattr(nuke, 'NUKE_VERSION_STRING', '?'),
        'GUI' if nuke.GUI else 'terminal'))
    report.add(INFO, 'Python: %s' % sys.version.split()[0])
    report.add(INFO, 'Date: %s' % datetime.datetime.now().strftime('%Y-%m-%d %H:%M'))
    for name in sorted(os.environ):
        if name.startswith('FLARESIM_') or name in ('NUKE_PATH', 'CUDA_VISIBLE_DEVICES'):
            report.add(INFO, '%s=%s' % (name, os.environ[name]))


def check_gpu(report):
    out = _run_cmd(['nvidia-smi', '--query-gpu=name,driver_version,memory.total',
                    '--format=csv,noheader'])
    if out is None:
        report.add(WARN, 'nvidia-smi not found or failed. The node render test '
                         'below is the real check.')
        return
    gpus = [l.strip() for l in out.splitlines() if l.strip()]
    if not gpus:
        report.add(FAIL, 'nvidia-smi lists no NVIDIA GPU. FlareSim+ needs one.')
        return
    for i, line in enumerate(gpus):
        parts = [p.strip() for p in line.split(',')]
        name, driver = parts[0], parts[1] if len(parts) > 1 else '?'
        try:
            major = int(driver.split('.')[0])
        except ValueError:
            major = 0
        mem = parts[2] if len(parts) > 2 else ''
        text = 'GPU %d: %s, driver %s, %s' % (i, name, driver, mem)
        if major and major < MIN_DRIVER:
            report.add(FAIL, text, 'Driver %d is older than %d, which FlareSim+ '
                       '(CUDA 12) needs. Update the NVIDIA driver.' % (major, MIN_DRIVER))
        else:
            report.add(PASS, text)
    if os.environ.get('CUDA_VISIBLE_DEVICES', None) == '':
        report.add(WARN, 'CUDA_VISIBLE_DEVICES is empty, so no GPU is visible to Nuke.')


def _plugin_file(name):
    ext = {'win32': '.dll', 'darwin': '.dylib'}.get(sys.platform, '.so')
    return os.path.join(_HERE, name + ext)


def check_install(report):
    report.add(INFO, 'Install folder: %s' % _HERE)
    for name in ('FlareSim', 'FlareSim3D', 'flaresim_preview'):
        path = _plugin_file(name)
        if os.path.isfile(path):
            stamp = datetime.datetime.fromtimestamp(os.path.getmtime(path))
            report.add(PASS, '%s found (built %s)' % (
                os.path.basename(path), stamp.strftime('%Y-%m-%d %H:%M')))
        else:
            report.add(FAIL if name != 'flaresim_preview' else WARN,
                       '%s missing from the install folder' % os.path.basename(path))
    for name in ('menu.py', 'FlareSim_LensBrowser.py', 'FlareSim_Looks.py',
                 'FlareSim_Header.py', 'icons', 'looks', 'lenses'):
        if not os.path.exists(os.path.join(_HERE, name)):
            report.add(FAIL, '%s missing from the install folder' % name)
    if not os.access(_HERE, os.W_OK):
        report.add(INFO, 'Install folder is read-only (fine for a shared install)')
    if sys.platform.startswith('linux'):
        out = _run_cmd(['ldd', _plugin_file('FlareSim')])
        # libDDImage and libNdk come with Nuke and may not be on ldd's path.
        missing = [l.strip() for l in (out or '').splitlines()
                   if 'not found' in l and 'DDImage' not in l and 'Ndk' not in l]
        if missing:
            report.add(WARN, 'FlareSim.so may need libraries this machine lacks',
                       '\n'.join(missing))
        elif out is not None:
            report.add(PASS, 'FlareSim.so library dependencies found')


def check_plugin_load(report):
    for cls in ('FlareSim', 'FlareSim3D'):
        try:
            nuke.load(cls)
        except Exception as e:
            report.add(FAIL, 'Could not load the %s plugin' % cls, str(e))
            continue
        node = None
        try:
            node = getattr(nuke.nodes, cls)()
            report.add(PASS, '%s node created' % cls)
        except Exception as e:
            report.add(FAIL, 'Could not create a %s node' % cls, str(e))
        finally:
            if node is not None:
                nuke.delete(node)


def check_folders(report):
    base = os.path.join(os.path.expanduser('~'), '.nuke', 'FlareSim')
    for sub in ('thumbnails', 'headers', 'looks', 'lenses'):
        path = os.path.join(base, sub)
        try:
            os.makedirs(path, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path, prefix='.selftest')
            os.close(fd)
            os.remove(tmp)
            report.add(PASS, 'Writable: %s' % path)
        except OSError as e:
            report.add(WARN, 'Not writable: %s (%s). Thumbnails, saved looks or '
                             'imported lenses will not work.' % (path, e))


def _all_lenses():
    import FlareSim_Looks
    out = []
    for root in FlareSim_Looks.lens_search_roots():
        if not os.path.isdir(root):
            continue
        for folder, _dirs, files in os.walk(root):
            for f in sorted(files):
                if f.lower().endswith('.lens'):
                    out.append((root, os.path.join(folder, f).replace('\\', '/')))
    return out


def check_lens_folders(report):
    import FlareSim_Looks
    lenses = _all_lenses()
    for root in FlareSim_Looks.lens_search_roots():
        n = sum(1 for r, _ in lenses if r == root)
        if not os.path.isdir(root):
            if root != FlareSim_Looks.USER_LENS_DIR:
                report.add(FAIL if root == FlareSim_Looks.BUNDLED_LENS_ROOT else WARN,
                           'Lens folder not found: %s' % root)
        elif root == FlareSim_Looks.USER_LENS_DIR:
            report.add(INFO, '%d imported lenses in %s' % (n, root))
        else:
            report.add(PASS if n else WARN, '%d lenses in %s' % (n, root))
    if os.path.isdir(FlareSim_Looks.USER_LENS_DIR) and os.listdir(FlareSim_Looks.USER_LENS_DIR):
        report.add(WARN, 'Lenses imported into your home folder (%s) are not seen '
                         'by other artists or the farm. Put studio lenses in a folder '
                         'on FLARESIM_LENS_PATH.' % FlareSim_Looks.USER_LENS_DIR)


def check_preview(report):
    try:
        import FlareSim_LensBrowser as lb
    except Exception as e:
        report.add(FAIL, 'Lens Browser could not be imported', str(e))
        return
    lib, err = lb.preview_library()
    if lib is None:
        report.add(FAIL, 'Lens Browser preview library did not load', err)
        return
    report.add(PASS, 'Lens Browser preview library loaded')
    handle = lib.fsp_create()
    bad = []
    lenses = _all_lenses()
    try:
        for _root, path in lenses:
            if lib.fsp_load_lens(handle, path.encode('utf-8')) <= 0:
                msg = lib.fsp_last_error(handle) or b''
                bad.append('%s: %s' % (path, msg.decode('utf-8', 'replace')))
    finally:
        lib.fsp_destroy(handle)
    if bad:
        report.add(WARN, '%d of %d lens files could not be read' % (len(bad), len(lenses)),
                   '\n'.join(bad[:50]) + ('\n...' if len(bad) > 50 else ''))
    else:
        report.add(PASS, 'All %d lens files read' % len(lenses))


def _sample_energy(node, w, h):
    total, lit = 0.0, 0
    for j in range(9):
        for i in range(16):
            x, y = (i + 0.5) * w / 16.0, (j + 0.5) * h / 9.0
            v = sum(nuke.sample(node, c, x, y) for c in ('rgba.red', 'rgba.green', 'rgba.blue'))
            if v != v:  # NaN
                return float('nan'), lit
            total += v
            lit += v > 1e-5
    return total, lit


def _render_lens(lens, out_dir):
    """Render one lens through the node.  Returns (status, text, detail)."""
    w, h = TEST_FORMAT
    fmt = '%d %d 1 flaresim_selftest' % (w, h)
    nuke.addFormat(fmt)
    made = []
    try:
        const = nuke.nodes.Constant(color=[0, 0, 0, 1])
        made.append(const)
        const['format'].setValue('flaresim_selftest')
        fs = nuke.nodes.FlareSim(inputs=[const])
        made.append(fs)
        fs['source_mode'].setValue('Manual XY')
        fs['manual_xy'].setValue([w * 0.35, h * 0.6])
        fs['source_intensity'].setValue(30)
        fs['quality'].setValue('Low')
        fs['lens_file'].setValue(lens)
        out = os.path.join(out_dir, 'flaresim_selftest.exr').replace('\\', '/')
        wr = nuke.nodes.Write(inputs=[fs], file=out, file_type='exr')
        made.append(wr)
        start = time.time()
        try:
            nuke.execute(wr, 1, 1)
        except Exception as e:
            return FAIL, 'Render failed: %s' % os.path.basename(lens), str(e)
        secs = time.time() - start
        energy, lit = _sample_energy(fs, w, h)
        if energy != energy:
            return FAIL, 'NaN pixels: %s' % os.path.basename(lens), None
        if lit == 0:
            return FAIL, 'Black frame (no flare): %s' % os.path.basename(lens), None
        return PASS, 'Rendered %s in %.2fs (%d of 144 samples lit)' % (
            os.path.basename(lens), secs, lit), None
    finally:
        for n in reversed(made):
            try:
                nuke.delete(n)
            except Exception:
                pass


def check_render(report, all_lenses=False):
    lenses = [p for _r, p in _all_lenses()]
    if not lenses:
        report.add(FAIL, 'No lenses to render')
        return
    if all_lenses or len(lenses) <= SAMPLE_LENSES:
        picked = lenses
    else:
        step = len(lenses) / float(SAMPLE_LENSES)
        picked = [lenses[int(i * step)] for i in range(SAMPLE_LENSES)]
        report.add(INFO, 'Rendering %d of %d lenses (use --all for every lens)'
                   % (len(picked), len(lenses)))
    out_dir = tempfile.mkdtemp(prefix='flaresim_selftest')
    failed = 0
    for lens in picked:
        status, text, detail = _render_lens(lens, out_dir)
        failed += status == FAIL
        report.add(status, text, detail)
        # A GPU or driver problem fails every lens the same way.
        if failed >= 3 and failed == picked.index(lens) + 1:
            report.add(FAIL, 'Stopped: the first 3 renders all failed')
            break
    try:
        for f in os.listdir(out_dir):
            os.remove(os.path.join(out_dir, f))
        os.rmdir(out_dir)
    except OSError:
        pass


def check_script_lenses(report, script=None):
    import FlareSim_Looks
    if script:
        nuke.scriptOpen(script)
        report.add(INFO, 'Script: %s' % script)
    else:
        report.add(INFO, 'Script: %s' % (nuke.root().name() or 'untitled'))
    nodes = [n for n in nuke.allNodes(recurseGroups=True)
             if n.Class() in FlareSim_Looks.FLARESIM_CLASSES]
    if not nodes:
        report.add(INFO, 'No FlareSim+ nodes in the script')
        return
    for n in nodes:
        stored = n['lens_file'].value() if 'lens_file' in n.knobs() else ''
        if not stored:
            report.add(WARN, '%s has no lens' % n.fullName())
            continue
        path = nuke.filenameFilter(stored) if hasattr(nuke, 'filenameFilter') else stored
        found = FlareSim_Looks.resolve_lens(path)
        if not found:
            report.add(FAIL, '%s: lens not found on this machine' % n.fullName(), stored)
        elif os.path.normcase(found) != os.path.normcase(path.replace('\\', '/')):
            report.add(PASS, '%s: lens found by name' % n.fullName(),
                       'saved:  %s\nfound:  %s' % (stored, found))
        else:
            report.add(PASS, '%s: lens found' % n.fullName())


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def default_report_path():
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M')
    return os.path.join(os.path.expanduser('~'), 'FlareSim_selftest_%s_%s.txt'
                        % (socket.gethostname().split('.')[0], stamp))


def run(all_lenses=False, script=None, out=None, show=True):
    """Run every check and write the report.  Returns the report path."""
    report = Report()
    _check(report, 'Machine', check_machine)
    _check(report, 'NVIDIA GPU', check_gpu)
    _check(report, 'Install', check_install)
    _check(report, 'Plugin', check_plugin_load)
    _check(report, 'User folders', check_folders)
    _check(report, 'Lens folders', check_lens_folders)
    _check(report, 'Lens Browser', check_preview)
    with nuke.root():
        _check(report, 'Node render', lambda r: check_render(r, all_lenses))
    if script or nuke.GUI:
        _check(report, 'Script lenses', lambda r: check_script_lenses(r, script))
    global last_report
    last_report = report

    path = out or default_report_path()
    text = report.text()
    try:
        with open(path, 'w') as fh:
            fh.write(text)
    except OSError as e:
        path = None
        print('FlareSim+ self-test: could not write the report: %s' % e)
    print(text)
    if path:
        print('Report written to %s' % path)
    if show and nuke.GUI:
        nuke.message('FlareSim+ self-test: %s\n\nReport: %s'
                     % (text.splitlines()[1], path or '(not written)'))
    return path


def _main(argv):
    all_lenses = '--all' in argv
    script = out = None
    if '--script' in argv:
        script = argv[argv.index('--script') + 1]
    if '--out' in argv:
        out = argv[argv.index('--out') + 1]
    path = run(all_lenses=all_lenses, script=script, out=out, show=False)
    # Non-zero exit when anything failed, so a farm job shows up red.
    return 0 if path and not last_report.counts[FAIL] else 1


if __name__ == '__main__':
    # Run as "nuke -t FlareSim_SelfTest.py": make the plugin findable even
    # when this folder isn't on NUKE_PATH.
    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)
    nuke.pluginAddPath(_HERE)
    sys.exit(_main(sys.argv[1:]))
