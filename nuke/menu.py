# FlareSim+ — Nuke node menu registration
#
# Keep this file (and the other FlareSim_*.py files and the icons folder) in
# the same folder as the
# FlareSim and FlareSim3D plugins, and add that folder to Nuke's plugin path,
# e.g. in ~/.nuke/init.py:
#
#     nuke.pluginAddPath('./plugins/FlareSim')
#
# Nuke loads every menu.py it finds on NUKE_PATH at startup.

import nuke

nuke.menu('Nodes').addCommand(
    'Filter/FlareSim+',
    'nuke.createNode("FlareSim")',
)
nuke.menu('Nodes').addCommand(
    'Filter/FlareSim+ 3D',
    'nuke.createNode("FlareSim3D")',
)

try:
    import FlareSim_LensBrowser
    FlareSim_LensBrowser.register()
except Exception as e:
    nuke.warning(f'FlareSim: could not load lens browser: {e}')

try:
    import FlareSim_Header
    FlareSim_Header.register()
except Exception as e:
    nuke.warning(f'FlareSim: could not load the panel header: {e}')

nuke.menu('Nuke').addCommand(
    'Help/FlareSim+/Self Test',
    'import FlareSim_SelfTest; FlareSim_SelfTest.run()',
)
nuke.menu('Nuke').addCommand(
    'Help/FlareSim+/Debug Logging On or Off',
    'import FlareSim_Log; FlareSim_Log.toggle_from_menu()',
)
nuke.menu('Nuke').addCommand(
    'Help/FlareSim+/Save Debug Report',
    'import FlareSim_Log; FlareSim_Log.report_from_menu()',
)
