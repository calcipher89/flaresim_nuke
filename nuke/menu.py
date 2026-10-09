# FlareSim — Nuke node menu registration
#
# Keep this file (and FlareSim_LensBrowser.py) in the same folder as the
# FlareSim and FlareSim3D plugins, and add that folder to Nuke's plugin path,
# e.g. in ~/.nuke/init.py:
#
#     nuke.pluginAddPath('./plugins/FlareSim')
#
# Nuke loads every menu.py it finds on NUKE_PATH at startup.

import nuke

nuke.menu('Nodes').addCommand(
    'Filter/FlareSim',
    'nuke.createNode("FlareSim")',
)
nuke.menu('Nodes').addCommand(
    'Filter/FlareSim3D',
    'nuke.createNode("FlareSim3D")',
)

try:
    import FlareSim_LensBrowser
    FlareSim_LensBrowser.register()
except Exception as e:
    nuke.warning(f'FlareSim: could not load lens browser: {e}')
