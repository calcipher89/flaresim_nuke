# ============================================================================
# FlareSimPlus_Preview.py — Viewer Quality on FlareSim+ nodes
#
# Viewer Quality is a lower Quality used only while looking at a node in
# the Viewer.  The plugin already ignores it without a GUI (nuke -x / -t,
# the farm, Frame Server workers).  A Write rendered inside the GUI runs in
# the same Nuke as the Viewer, so while it renders this module sets the
# hidden render_full knob on every FlareSim+ node and the frames come out at
# full Quality.  If a render never reports back, render_full stays on and
# the Viewer shows full Quality (slower, never wrong) until the next render
# finishes or Viewer Quality is changed.
#
# The Node Graph label shows '(viewer: Low)' or similar while Viewer Quality
# is on, so nobody judges the final look from the preview.
# ============================================================================

import nuke

import FlareSimPlus_Log as _log

NODE_CLASSES = ('FlareSimPlus', 'FlareSimPlus3D')
_SAME = 'Same as Render'


def _flare_nodes():
    return [n for n in nuke.allNodes(recurseGroups=True) if n.Class() in NODE_CLASSES]


def _set_render_full(on):
    root = nuke.root()
    modified = root.modified()
    nuke.Undo.disable()
    try:
        for n in _flare_nodes():
            k = n.knob('render_full')
            if k is not None and bool(k.value()) != on:
                k.setValue(on)
    finally:
        nuke.Undo.enable()
        root.setModified(modified)


def _before_render():
    try:
        _set_render_full(True)
        _log.debug('preview', 'render started: full quality on')
    except Exception:
        _log.exception('preview', 'could not switch to full quality for a render')


def _after_render():
    try:
        _set_render_full(False)
        _log.debug('preview', 'render finished: viewer quality back')
    except Exception:
        _log.exception('preview', 'could not switch back to viewer quality')


def _autolabel():
    """Node Graph label: the default, plus the Viewer Quality while it is on."""
    n = nuke.thisNode()
    k = n.knob('viewer_quality')
    if k is None or k.value() == _SAME:
        return None
    label = n.name()
    extra = n.knob('label')
    if extra is not None:
        text = extra.evaluate()
        if text:
            label += '\n' + text
    return label + '\n(viewer: %s)' % k.value()


def register():
    """Hook GUI renders and the Node Graph label.  Does nothing without a GUI,
    where the plugin always renders at full Quality."""
    if not getattr(nuke, 'GUI', True):
        return
    nuke.addBeforeRender(_before_render)
    nuke.addAfterRender(_after_render)
    for cls in NODE_CLASSES:
        nuke.addAutolabel(_autolabel, nodeClass=cls)
