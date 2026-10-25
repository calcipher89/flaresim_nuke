#!/usr/bin/env python3
"""make_edition.py — write the Python files for a side-by-side FlareSim+ build.

    python3 scripts/make_edition.py Beta nuke/ out/

copies menu.py and every FlareSimPlus_*.py from nuke/ to out/, with each
FlareSimPlus name (node classes, module files, imports) turned into
FlareSimPlus<Edition> and each "FlareSim+" label into "FlareSim+ <Edition>".
The compiled nodes get the same names from cmake -DFLARESIM_EDITION=<Edition>
(src/edition.h).  The result loads next to a regular FlareSim+ install in the
same Nuke session: other classes, other modules, other menu entries.

User data stays shared: ~/.nuke/FlareSim (looks, lenses, logs, caches) and the
FLARESIM_* environment variables are the same for both builds.
"""

import os
import re
import sys


def convert(text, edition):
    text = text.replace('FlareSimPlus', 'FlareSimPlus' + edition)
    return text.replace('FlareSim+', 'FlareSim+ ' + edition)


def main(argv):
    if len(argv) != 4 or not re.match(r'^[A-Z][A-Za-z0-9]*$', argv[1]):
        print(__doc__.strip(), file=sys.stderr)
        return 1
    edition, src, out = argv[1:]
    os.makedirs(out, exist_ok=True)
    names = sorted(n for n in os.listdir(src)
                   if n == 'menu.py' or (n.startswith('FlareSimPlus_') and n.endswith('.py')))
    for name in names:
        with open(os.path.join(src, name), encoding='utf-8') as f:
            text = f.read()
        with open(os.path.join(out, convert(name, edition)), 'w', encoding='utf-8',
                  newline='\n') as f:
            f.write(convert(text, edition))
    print('make_edition: %d file(s) -> %s' % (len(names), out))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
