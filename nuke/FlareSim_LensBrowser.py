"""
FlareSim_LensBrowser.py — Dockable lens and look browser panel for FlareSim.

Place this file in the same directory as FlareSim.dll on your NUKE_PATH.
Open the panel from the Pane menu: "FlareSim Lens Browser".
"""

import os
import re
import subprocess
import sys
import nuke
import nukescripts

import FlareSim_Looks


# Node classes that carry a lens_file knob.
FLARESIM_CLASSES = FlareSim_Looks.FLARESIM_CLASSES

# Lens library shipped next to this file in release packages.
_BUNDLED_LENS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 'lenses', 'lens_files')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_lens_meta(path):
    """Read name, focal_length and f-number from the header of a .lens file.

    Stops reading at the first 'surfaces:' line for speed.
    """
    name = os.path.splitext(os.path.basename(path))[0]
    focal = 0.0
    f_num = 0.0
    try:
        with open(path, encoding='utf-8', errors='replace') as fh:
            for line in fh:
                s = line.strip()
                if s.startswith('name:'):
                    v = s[5:].strip()
                    if v:
                        name = v
                elif s.startswith('focal_length:'):
                    try:
                        focal = float(s[13:].strip())
                    except ValueError:
                        pass
                elif s.startswith('#') and f_num == 0.0:
                    # Pick up "f/2.8" written in comment lines by the converter
                    m = re.search(r'\bf/(\d+(?:\.\d+)?)', s)
                    if m:
                        try:
                            f_num = float(m.group(1))
                        except ValueError:
                            pass
                elif s.startswith('surfaces:'):
                    break
    except Exception:
        pass
    return name, focal, f_num


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

class FlareLensBrowser(nukescripts.PythonPanel):

    PANEL_ID = 'uk.co.flaresim.lensbrowser'

    def __init__(self):
        super().__init__('FlareSim Lens Browser', self.PANEL_ID)

        # --- Lens folder row ---
        self._dir_knob = nuke.String_Knob('lens_dir', 'Lens Folder')
        self._dir_knob.setTooltip(
            'Directory containing .lens files. '
            'Click Browse to pick any .lens file and the folder will be set automatically.'
        )
        self._browse_knob = nuke.Script_Knob('browse_dir', 'Browse...')
        self._browse_knob.clearFlag(nuke.STARTLINE)
        self._browse_knob.setTooltip('Open a file browser — the folder of the picked file is used.')

        # --- Filter row ---
        self._filter_knob = nuke.String_Knob('filter', 'Filter')
        self._filter_knob.setTooltip('Case-insensitive substring filter. Press Refresh to apply.')
        self._refresh_knob = nuke.Script_Knob('refresh_list', 'Refresh')
        self._refresh_knob.clearFlag(nuke.STARTLINE)

        # --- List ---
        self._list_knob = nuke.Enumeration_Knob('lens_list', 'Lens', ['(choose a folder above)'])
        self._list_knob.setTooltip('Select a lens, then click Load.')

        # --- Load ---
        self._load_knob = nuke.Script_Knob('load_lens', 'Load onto selected FlareSim')
        self._load_knob.setTooltip(
            'Sets the Lens File knob on the selected FlareSim / FlareSim3D node(s). '
            'If no FlareSim node is selected but exactly one exists in the script, '
            'it is used automatically.'
        )

        # --- Looks ---
        self._looks_div = nuke.Text_Knob('looks_div', 'Looks', '')
        self._look_list_knob = nuke.Enumeration_Knob('look_list', 'Look', ['(no looks found)'])
        self._look_list_knob.setTooltip(
            'Saved looks: a lens plus gain, aperture, spectral, blur and '
            'per-surface settings.  Source position, threshold and camera '
            'are not part of a look.'
        )
        self._look_refresh_knob = nuke.Script_Knob('look_refresh', 'Refresh')
        self._look_refresh_knob.clearFlag(nuke.STARTLINE)
        self._look_info_knob = nuke.Text_Knob('look_info', '', '')
        self._apply_look_knob = nuke.Script_Knob('apply_look', 'Apply Look to selected FlareSim')
        self._apply_look_knob.setTooltip(
            'Sets the lens and look settings on the selected FlareSim / '
            'FlareSim3D node(s).  Undo with Ctrl+Z.'
        )
        self._save_look_knob = nuke.Script_Knob('save_look', 'Save Look...')
        self._save_look_knob.setTooltip(
            "Saves the selected FlareSim node's lens and look settings as a "
            'new look in your own looks folder.'
        )
        self._open_looks_knob = nuke.Script_Knob('open_looks', 'Open My Looks Folder')
        self._open_looks_knob.clearFlag(nuke.STARTLINE)

        for k in (
            self._dir_knob,
            self._browse_knob,
            self._filter_knob,
            self._refresh_knob,
            self._list_knob,
            self._load_knob,
            self._looks_div,
            self._look_list_knob,
            self._look_refresh_knob,
            self._look_info_knob,
            self._apply_look_knob,
            self._save_look_knob,
            self._open_looks_knob,
        ):
            self.addKnob(k)

        # label → look dict, kept in sync with the look Enumeration_Knob values
        self._look_map = {}

        # label → absolute path, kept in sync with the Enumeration_Knob values
        self._path_map = {}

        # Start on the bundled lens library when it is installed alongside us.
        if os.path.isdir(_BUNDLED_LENS_DIR):
            self._dir_knob.setValue(_BUNDLED_LENS_DIR.replace('\\', '/'))
            self._refresh_list()

        self._refresh_looks()

    # ------------------------------------------------------------------
    # Event handler
    # ------------------------------------------------------------------

    def knobChanged(self, knob):
        if knob is self._browse_knob:
            path = nuke.getFilename('Select any .lens file', '*.lens')
            if path:
                folder = os.path.dirname(os.path.abspath(path))
                self._dir_knob.setValue(folder)
                self._refresh_list()

        elif knob is self._dir_knob:
            self._refresh_list()

        elif knob is self._filter_knob or knob is self._refresh_knob:
            self._refresh_list()

        elif knob is self._load_knob:
            self._load_selected()

        elif knob is self._look_refresh_knob:
            self._refresh_looks()

        elif knob is self._look_list_knob:
            self._show_look_info()

        elif knob is self._apply_look_knob:
            self._apply_selected_look()

        elif knob is self._save_look_knob:
            self._save_look()

        elif knob is self._open_looks_knob:
            self._open_looks_folder()

    # ------------------------------------------------------------------
    # List refresh
    # ------------------------------------------------------------------

    def _refresh_list(self):
        folder = self._dir_knob.value().strip()
        if not folder:
            return

        # Accept a full file path — just take the directory
        if os.path.isfile(folder):
            folder = os.path.dirname(folder)

        if not os.path.isdir(folder):
            nuke.message(f'FlareSim Lens Browser: folder not found:\n{folder}')
            return

        filt = self._filter_knob.value().strip().lower()

        entries = []
        try:
            filenames = sorted(os.listdir(folder), key=str.lower)
        except OSError as e:
            nuke.message(f'FlareSim Lens Browser: cannot read folder:\n{e}')
            return

        for fname in filenames:
            if not fname.lower().endswith('.lens'):
                continue
            fpath = os.path.join(folder, fname)
            name, focal, f_num = _read_lens_meta(fpath)

            if focal > 0 and f_num > 0:
                label = f'{name}  [{focal:.0f}mm  f/{f_num:.1f}]'
            elif focal > 0:
                label = f'{name}  [{focal:.0f}mm]'
            else:
                label = name

            if not filt or filt in label.lower():
                entries.append((label, fpath))

        entries.sort(key=lambda e: e[0].lower())
        self._path_map = {label: fpath for label, fpath in entries}
        labels = [label for label, _ in entries]

        if labels:
            self._list_knob.setValues(labels)
        else:
            self._list_knob.setValues(['(no matches)'])
            self._path_map = {}

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def _load_selected(self):
        label = self._list_knob.value()
        fpath = self._path_map.get(label)
        if not fpath:
            nuke.message('No lens selected, or list needs refreshing.')
            return

        nodes = FlareSim_Looks.target_nodes()
        if not nodes:
            return

        fpath_nuke = fpath.replace('\\', '/')
        for n in nodes:
            n['lens_file'].setValue(fpath_nuke)

    # ------------------------------------------------------------------
    # Looks
    # ------------------------------------------------------------------

    def _refresh_looks(self, select_name=None):
        current = select_name or self._look_list_knob.value()
        looks = FlareSim_Looks.list_looks()
        self._look_map = {}
        labels = []
        for look in looks:
            label = '%s  (%s)' % (look['name'], look['_source'])
            self._look_map[label] = look
            labels.append(label)
        if not labels:
            self._look_list_knob.setValues(['(no looks found)'])
            self._look_info_knob.setValue('')
            return
        self._look_list_knob.setValues(labels)
        for label in labels:
            if label == current or self._look_map[label]['name'] == current:
                self._look_list_knob.setValue(label)
                break
        self._show_look_info()

    def _show_look_info(self):
        look = self._look_map.get(self._look_list_knob.value())
        if not look:
            self._look_info_knob.setValue('')
            return
        lens = look.get('lens', '')
        lens_name = os.path.splitext(os.path.basename(lens))[0] if lens else '(no lens)'
        lines = ['Lens: %s' % lens_name]
        if look.get('description'):
            lines.append(look['description'])
        self._look_info_knob.setValue('\n'.join(lines))

    def _apply_selected_look(self):
        look = self._look_map.get(self._look_list_knob.value())
        if not look:
            nuke.message('No look selected, or the list needs refreshing.')
            return
        nodes = FlareSim_Looks.target_nodes()
        if not nodes:
            return
        warnings = FlareSim_Looks.apply_look(look, nodes)
        if warnings:
            nuke.message('Look applied with warnings:\n' + '\n'.join(warnings))

    def _save_look(self):
        nodes = FlareSim_Looks.target_nodes()
        if not nodes:
            return
        if len(nodes) > 1:
            nuke.message('Select just one FlareSim node to save its look.')
            return
        node = nodes[0]
        lens = node['lens_file'].value()
        default = os.path.splitext(os.path.basename(lens))[0] if lens else 'My Look'
        name = nuke.getInput('Look name', default)
        if not name or not name.strip():
            return
        name = name.strip()
        description = nuke.getInput('Description (optional)', '') or ''
        try:
            FlareSim_Looks.save_look(node, name, description.strip())
        except FileExistsError:
            if not nuke.ask('A look called "%s" already exists in your looks '
                            'folder.  Replace it?' % name):
                return
            FlareSim_Looks.save_look(node, name, description.strip(), overwrite=True)
        self._refresh_looks(select_name=name)

    def _open_looks_folder(self):
        folder = FlareSim_Looks.USER_LOOKS_DIR
        os.makedirs(folder, exist_ok=True)
        if sys.platform.startswith('win'):
            os.startfile(folder)
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', folder])
        else:
            subprocess.Popen(['xdg-open', folder])


# ---------------------------------------------------------------------------
# Registration — called once from menu.py at startup
# ---------------------------------------------------------------------------

def _show_browser():
    """Create (or raise) the Lens Browser panel in the current pane."""
    panel = FlareLensBrowser()
    return panel.addToPane()


def register():
    """Register the panel with Nuke and add it to the Pane menu."""
    nukescripts.registerPanel(FlareLensBrowser.PANEL_ID, _show_browser)
    nuke.menu('Pane').addCommand('FlareSim Lens Browser', _show_browser)
