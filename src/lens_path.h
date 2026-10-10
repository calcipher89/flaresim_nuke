// ============================================================================
// lens_path.h — find a script's lens file on this machine
//
// The lens_file knob stores a full path.  That path breaks when a script
// moves between machines: a Windows path opened on Linux, a lens imported
// into one artist's home folder, or a plugin install that moved to a new
// version folder.  resolve_lens_path() keeps the stored path when it exists
// and otherwise looks for the same file in the lens folders on this machine:
//
//   1. each folder on FLARESIM_LENS_PATH (studio lenses)
//   2. the bundled "lenses" folder next to the plugin
//   3. ~/.nuke/FlareSim/lenses (lenses imported in the Lens Browser)
//
// Matching tries the part of the path after ".../lenses/" first, then the
// file name anywhere in those folders (case-insensitive as a last resort,
// for scripts written on Windows).
// ============================================================================
#pragma once

#include <string>
#include <vector>

namespace flaresim {

// Folder holding this plugin binary, or "" if it can't be found.
std::string plugin_dir();

// Lens folders on this machine, in search order.
std::vector<std::string> lens_search_roots();

// Resolve `stored` against `roots`.  Returns the path to load, or "" when
// no matching file exists.  Doesn't touch the cache (used by the tests).
std::string resolve_lens_path(const std::string& stored,
                              const std::vector<std::string>& roots);

// Resolve against this machine's lens folders.  Hits are cached for the
// session; misses are not, so a lens added later is still found.
std::string resolve_lens_path(const std::string& stored);

} // namespace flaresim
