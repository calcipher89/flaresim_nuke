// lens_path.cpp — see lens_path.h
#include "lens_path.h"

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

// Plain OS calls rather than std::filesystem: older Linux toolchains (the
// Rocky 8 / gcc-toolset builds for Nuke) can leave std::filesystem symbols
// unresolved until the plugin is loaded on an artist's machine.
#ifdef _WIN32
#  ifndef WIN32_LEAN_AND_MEAN
#    define WIN32_LEAN_AND_MEAN
#  endif
#  include <windows.h>
#else
#  include <dirent.h>
#  include <dlfcn.h>
#  include <sys/stat.h>
#endif

namespace flaresim {

namespace lens_path_detail {

inline std::string slashes(std::string s)
{
    std::replace(s.begin(), s.end(), '\\', '/');
    return s;
}

inline std::string lower(std::string s)
{
    std::transform(s.begin(), s.end(), s.begin(),
                   [](unsigned char c) { return (char)std::tolower(c); });
    return s;
}

#ifdef _WIN32
inline std::wstring widen(const std::string& s)
{
    if (s.empty()) return std::wstring();
    const int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), nullptr, 0);
    std::wstring w(n, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), &w[0], n);
    return w;
}

inline std::string narrow(const std::wstring& w)
{
    if (w.empty()) return std::string();
    const int n = WideCharToMultiByte(CP_UTF8, 0, w.data(), (int)w.size(), nullptr, 0,
                                      nullptr, nullptr);
    std::string s(n, '\0');
    WideCharToMultiByte(CP_UTF8, 0, w.data(), (int)w.size(), &s[0], n, nullptr, nullptr);
    return s;
}

inline DWORD attributes(const std::string& p)
{
    return GetFileAttributesW(widen(p).c_str());
}

inline bool is_file(const std::string& p)
{
    const DWORD a = p.empty() ? INVALID_FILE_ATTRIBUTES : attributes(p);
    return a != INVALID_FILE_ATTRIBUTES && !(a & FILE_ATTRIBUTE_DIRECTORY);
}

inline bool is_dir(const std::string& p)
{
    const DWORD a = p.empty() ? INVALID_FILE_ATTRIBUTES : attributes(p);
    return a != INVALID_FILE_ATTRIBUTES && (a & FILE_ATTRIBUTE_DIRECTORY);
}

// Calls fn(name, is_directory) for each entry in dir.
template <typename Fn>
inline void list_dir(const std::string& dir, Fn fn)
{
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(widen(dir + "/*").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return;
    do {
        const std::string name = narrow(fd.cFileName);
        if (name == "." || name == "..") continue;
        if (!fn(name, (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0)) break;
    } while (FindNextFileW(h, &fd));
    FindClose(h);
}
#else
inline bool is_file(const std::string& p)
{
    struct stat st;
    return !p.empty() && stat(p.c_str(), &st) == 0 && S_ISREG(st.st_mode);
}

inline bool is_dir(const std::string& p)
{
    struct stat st;
    return !p.empty() && stat(p.c_str(), &st) == 0 && S_ISDIR(st.st_mode);
}

// Calls fn(name, is_directory) for each entry in dir.
template <typename Fn>
inline void list_dir(const std::string& dir, Fn fn)
{
    DIR* d = opendir(dir.c_str());
    if (!d) return;
    while (struct dirent* e = readdir(d)) {
        const std::string name = e->d_name;
        if (name == "." || name == "..") continue;
        bool folder = e->d_type == DT_DIR;
        if (e->d_type == DT_UNKNOWN || e->d_type == DT_LNK)  // some network filesystems
            folder = is_dir(dir + "/" + name);
        if (!fn(name, folder)) break;
    }
    closedir(d);
}
#endif

inline std::string join(const std::string& dir, const std::string& rel)
{
    if (dir.empty()) return rel;
    const char last = dir.back();
    return (last == '/' || last == '\\') ? dir + rel : dir + "/" + rel;
}

// Path after the last "/lenses/" (case-insensitive), or "".
inline std::string tail_after_lenses(const std::string& path)
{
    const std::string low = lower(path);
    const std::string key = "/lenses/";
    const size_t pos = low.rfind(key);
    return pos == std::string::npos ? std::string() : path.substr(pos + key.size());
}

inline std::string base_name(const std::string& path)
{
    const size_t pos = path.find_last_of('/');
    return pos == std::string::npos ? path : path.substr(pos + 1);
}

// Look for a file called `name` under `root`.  Stops after a fixed number of
// entries so a lens folder on a huge network share can't stall a render.
inline std::string find_by_name(const std::string& root, const std::string& name,
                                bool ignore_case)
{
    if (!is_dir(root)) return std::string();
    const std::string want = ignore_case ? lower(name) : name;
    int budget = 20000;
    std::vector<std::string> todo{ slashes(root) };
    std::string found;
    while (!todo.empty() && found.empty() && budget > 0) {
        const std::string dir = todo.back();
        todo.pop_back();
        std::vector<std::string> subdirs;
        list_dir(dir, [&](const std::string& entry, bool folder) {
            if (--budget < 0) return false;
            if (folder) {
                subdirs.push_back(join(dir, entry));
            } else if ((ignore_case ? lower(entry) : entry) == want) {
                found = join(dir, entry);
                return false;
            }
            return true;
        });
        // Visit sub-folders in name order, so the result doesn't depend on
        // the order the filesystem lists them in.
        std::sort(subdirs.rbegin(), subdirs.rend());
        todo.insert(todo.end(), subdirs.begin(), subdirs.end());
    }
    return found;
}

} // namespace lens_path_detail

// Folder holding this plugin binary, or "" if it can't be found.
std::string plugin_dir()
{
    static int anchor = 0;
    std::string path;
#ifdef _WIN32
    HMODULE mod = nullptr;
    if (GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                           GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                           reinterpret_cast<LPCWSTR>(&anchor), &mod)) {
        wchar_t buf[4096];
        const DWORD n = GetModuleFileNameW(mod, buf, 4096);
        if (n > 0 && n < 4096)
            path = lens_path_detail::narrow(std::wstring(buf, n));
    }
#else
    Dl_info info;
    if (dladdr(reinterpret_cast<void*>(&anchor), &info) && info.dli_fname)
        path = info.dli_fname;
#endif
    path = lens_path_detail::slashes(path);
    const size_t pos = path.find_last_of('/');
    return pos == std::string::npos ? std::string() : path.substr(0, pos);
}

// Lens folders on this machine, in search order.
std::vector<std::string> lens_search_roots()
{
    std::vector<std::string> roots;
#ifdef _WIN32
    const char sep = ';';
#else
    const char sep = ':';
#endif
    if (const char* env = std::getenv("FLARESIM_LENS_PATH")) {
        std::string s(env);
        size_t start = 0;
        while (start <= s.size()) {
            size_t end = s.find(sep, start);
            if (end == std::string::npos) end = s.size();
            if (end > start) roots.push_back(s.substr(start, end - start));
            start = end + 1;
        }
    }
    const std::string here = plugin_dir();
    if (!here.empty()) roots.push_back(here + "/lenses");
#ifdef _WIN32
    const char* home = std::getenv("USERPROFILE");
#else
    const char* home = std::getenv("HOME");
#endif
    if (home && *home) roots.push_back(std::string(home) + "/.nuke/FlareSim/lenses");
    return roots;
}

// Resolve `stored` against `roots`.  Returns the path to load, or "" when
// no matching file exists.  Doesn't touch the cache (used by the tests).
std::string resolve_lens_path(const std::string& stored,
                                     const std::vector<std::string>& roots)
{
    using namespace lens_path_detail;
    if (stored.empty()) return std::string();
    if (is_file(stored)) return stored;

    const std::string norm = slashes(stored);
    if (is_file(norm)) return norm;

    const std::string tail = tail_after_lenses(norm);
    if (!tail.empty())
        for (const std::string& r : roots) {
            const std::string p = join(r, tail);
            if (is_file(p)) return slashes(p);
        }

    // Relative paths (e.g. "lens_files/X.lens" from a look) under each root.
    if (norm.find(':') == std::string::npos && norm[0] != '/')
        for (const std::string& r : roots) {
            const std::string p = join(r, norm);
            if (is_file(p)) return slashes(p);
        }

    const std::string name = base_name(norm);
    if (name.empty()) return std::string();
    for (const std::string& r : roots) {
        const std::string p = find_by_name(r, name, false);
        if (!p.empty()) return p;
    }
    for (const std::string& r : roots) {
        const std::string p = find_by_name(r, name, true);
        if (!p.empty()) return p;
    }
    return std::string();
}

// Resolve against this machine's lens folders.  Hits are cached for the
// session; misses are not, so a lens added later is still found.
std::string resolve_lens_path(const std::string& stored)
{
    if (stored.empty()) return std::string();
    if (lens_path_detail::is_file(stored)) return stored;

    static std::mutex mtx;
    static std::unordered_map<std::string, std::string> cache;
    {
        std::lock_guard<std::mutex> lock(mtx);
        auto it = cache.find(stored);
        if (it != cache.end() && lens_path_detail::is_file(it->second))
            return it->second;
    }
    const std::string found = resolve_lens_path(stored, lens_search_roots());
    if (!found.empty()) {
        std::lock_guard<std::mutex> lock(mtx);
        cache[stored] = found;
    }
    return found;
}

} // namespace flaresim
