"""
FlareSimPlus_Convert.py — turn original FlareSim nodes into FlareSim+ nodes.

FlareSim+ registers its own node classes, FlareSimPlus and FlareSimPlus3D, so
it can be installed next to an original FlareSim (FlareSim / FlareSim3D)
without the two fighting over the same class name.  Old scripts keep their
original FlareSim nodes until you convert them:

  In Nuke     Help > FlareSim+ > Convert FlareSim Nodes converts the selected
              FlareSim / FlareSim3D nodes, or every one in the script when
              none is selected.  This needs the original FlareSim plugin to
              be loadable, so the old nodes open.  Ctrl+Z undoes it.

  On a file   Works without either plugin, e.g. for a script whose old nodes
              no longer open:

                  python FlareSimPlus_Convert.py shot.nk [more.nk ...]

              writes shot_flaresimplus.nk next to each script.  Add
              --in-place to overwrite the scripts (a .bak copy is kept).

Knob names, inputs and node names carry over: FlareSim+ kept the original's
knobs, so the converted node starts with the same settings.  Knobs FlareSim+
doesn't have are reported and dropped.
"""

import os
import re
import shutil
import sys

CLASS_MAP = {'FlareSim': 'FlareSimPlus', 'FlareSim3D': 'FlareSimPlus3D'}

# A side-by-side edition build (cmake -DFLARESIM_EDITION=Beta, classes with
# the edition in their names) also takes nodes from the regular build.  It is
# spelt 'FlareSim' 'Plus' in two halves so scripts/make_edition.py keeps it.
_PLUS = 'FlareSim' 'Plus'
if CLASS_MAP['FlareSim'] != _PLUS:
    CLASS_MAP[_PLUS] = CLASS_MAP['FlareSim']
    CLASS_MAP[_PLUS + '3D'] = CLASS_MAP['FlareSim3D']

# Knobs that belong to the node in the graph, not its settings.
_SKIP_KNOBS = {'name', 'xpos', 'ypos', 'selected', 'inputs', 'help', 'onCreate',
               'onDestroy', 'knobChanged', 'updateUI', 'autolabel', 'panel',
               'indicators', 'hide_input', 'cached', 'dope_sheet', 'bookmark',
               'process_mask', 'lifetimeStart', 'lifetimeEnd', 'useLifetime'}


# ---------------------------------------------------------------------------
# .nk text
# ---------------------------------------------------------------------------

# A node starts with its class name at the start of a line, then " {".
_NODE_RE = re.compile(r'^(\s*)(%s)(\s*\{)' % '|'.join(
    sorted(CLASS_MAP, key=len, reverse=True)), re.MULTILINE)


def convert_text(text):
    """Return (new_text, count) with original FlareSim nodes renamed."""
    count = [0]

    def sub(m):
        count[0] += 1
        return m.group(1) + CLASS_MAP[m.group(2)] + m.group(3)

    return _NODE_RE.sub(sub, text), count[0]


def convert_file(path, in_place=False):
    """Convert one .nk file.  Returns (output path, nodes converted)."""
    with open(path, 'r') as f:
        text = f.read()
    new_text, count = convert_text(text)
    if in_place:
        if count:
            shutil.copy2(path, path + '.bak')
        out = path
    else:
        root, ext = os.path.splitext(path)
        out = root + '_flaresimplus' + (ext or '.nk')
    if count or not in_place:
        with open(out, 'w') as f:
            f.write(new_text)
    return out, count


# ---------------------------------------------------------------------------
# Live nodes in Nuke
# ---------------------------------------------------------------------------

def _old_nodes(nuke):
    sel = [n for n in nuke.selectedNodes() if n.Class() in CLASS_MAP]
    if sel:
        return sel
    return [n for n in nuke.allNodes(recurseGroups=True) if n.Class() in CLASS_MAP]


def convert_node(old):
    """Replace one FlareSim / FlareSim3D node with a FlareSim+ node.

    Returns (new node, [knob names that could not be carried over])."""
    import nuke
    parent = old.parent() if hasattr(old, 'parent') else nuke.root()
    dropped = []
    with parent:
        new = getattr(nuke.nodes, CLASS_MAP[old.Class()])()
        new_knobs = new.knobs()
        for name, knob in old.knobs().items():
            if name in _SKIP_KNOBS or knob.Class() in ('Tab_Knob', 'Text_Knob',
                                                       'PyScript_Knob', 'Help_Knob'):
                continue
            if name not in new_knobs:
                if not knob.isDefault():
                    dropped.append(name)
                continue
            try:
                new_knobs[name].fromScript(knob.toScript())
            except Exception:
                dropped.append(name)

        # Inputs keep their order: plate, matte (mask), cam, ... on both.
        for i in range(min(old.inputs(), new.maxInputs())):
            new.setInput(i, old.input(i))
        for dep in old.dependent(nuke.INPUTS | nuke.HIDDEN_INPUTS, forceEvaluate=False):
            for i in range(dep.inputs()):
                if dep.input(i) is not None and dep.input(i).name() == old.name():
                    dep.setInput(i, new)

        new.setXYpos(old.xpos(), old.ypos())
        for k in ('label', 'tile_color', 'note_font', 'note_font_size',
                  'gl_color', 'disable'):
            if k in old.knobs() and k in new_knobs:
                try:
                    new_knobs[k].fromScript(old[k].toScript())
                except Exception:
                    pass
        name = old.name()
        nuke.delete(old)
        new.setName(name)
    return new, dropped


def convert_nodes(nodes=None):
    """Convert nodes (default: selected, else all).  Returns a summary."""
    import nuke
    nodes = nodes if nodes is not None else _old_nodes(nuke)
    if not nodes:
        return 'No original FlareSim nodes found.'
    lines = []
    undo = nuke.Undo()
    undo.begin('Convert FlareSim nodes')
    try:
        for old in nodes:
            label = old.fullName()
            new, dropped = convert_node(old)
            line = '%s -> %s' % (label, new.Class())
            if dropped:
                line += '  (not carried over: %s)' % ', '.join(sorted(dropped))
            lines.append(line)
    finally:
        undo.end()
    return 'Converted %d node(s) to FlareSim+:\n\n%s' % (len(lines), '\n'.join(lines))


def convert_from_menu():
    import nuke
    try:
        msg = convert_nodes()
    except Exception as e:
        msg = 'Could not convert: %s' % e
    nuke.message(msg)


def main(argv):
    in_place = '--in-place' in argv
    paths = [a for a in argv if not a.startswith('--')]
    if not paths:
        print(__doc__)
        return 1
    for path in paths:
        out, count = convert_file(path, in_place=in_place)
        print('%s: %d node(s) converted -> %s' % (path, count, out))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
