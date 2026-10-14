# FlareSim+ — Nuke node menu registration
#
# Keep this file (and the FlareSimPlus_*.py files and the icons folder) in
# the same folder as the FlareSimPlus and FlareSimPlus3D plugins, and add that
# folder to Nuke's plugin path,
# e.g. in ~/.nuke/init.py:
#
#     nuke.pluginAddPath('./plugins/FlareSim')
#
# Nuke loads every menu.py it finds on NUKE_PATH at startup.

import nuke

# New nodes start on Ghost Render: Smooth.  The knob itself defaults to
# Points so scripts saved before Smooth existed render as they did.
nuke.menu('Nodes').addCommand(
    'Filter/FlareSim+',
    'nuke.createNode("FlareSimPlus", "ghost_render Smooth")',
)
nuke.menu('Nodes').addCommand(
    'Filter/FlareSim+ 3D',
    'nuke.createNode("FlareSimPlus3D", "ghost_render Smooth")',
)

try:
    import FlareSimPlus_Header
    FlareSimPlus_Header.register()
except Exception as e:
    nuke.warning(f'FlareSim: could not load the panel header: {e}')

try:
    import FlareSimPlus_Preview
    FlareSimPlus_Preview.register()
except Exception as e:
    nuke.warning(f'FlareSim: could not set up Viewer Quality: {e}')

nuke.menu('Nuke').addCommand(
    'Help/FlareSim+/Self Test',
    'import FlareSimPlus_SelfTest; FlareSimPlus_SelfTest.run()',
)
nuke.menu('Nuke').addCommand(
    'Help/FlareSim+/Debug Logging On or Off',
    'import FlareSimPlus_Log; FlareSimPlus_Log.toggle_from_menu()',
)
nuke.menu('Nuke').addCommand(
    'Help/FlareSim+/Save Debug Report',
    'import FlareSimPlus_Log; FlareSimPlus_Log.report_from_menu()',
)
nuke.menu('Nuke').addCommand(
    'Help/FlareSim+/Convert FlareSim Nodes',
    'import FlareSimPlus_Convert; FlareSimPlus_Convert.convert_from_menu()',
)
