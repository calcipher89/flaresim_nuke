// trace_log.h — optional render trace for FlareSim and FlareSim3D.
//
// Set FLARESIM_TRACE_LOG to a file path and each node appends one line per
// validate, compute and first row served for a new state: frame, hash, why
// it computed, whether Nuke cancelled it, and how long it took.  Unset (the
// default) it costs one cached getenv check.  Meant for tracking down
// Viewer refresh problems on artists' machines without a debug build.

#pragma once

#include <chrono>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <mutex>

namespace flaresim {

inline const char* trace_log_path()
{
    static const char* path = std::getenv("FLARESIM_TRACE_LOG");
    return (path && *path) ? path : nullptr;
}

inline bool trace_enabled() { return trace_log_path() != nullptr; }

// One line: wall-clock seconds, node name, op address, then the message.
inline void trace(const char* node, const void* op, const char* fmt, ...)
{
    const char* path = trace_log_path();
    if (!path) return;
    static std::mutex m;
    std::lock_guard<std::mutex> lock(m);
    FILE* fp = std::fopen(path, "a");
    if (!fp) return;
    const double t = std::chrono::duration<double>(
        std::chrono::system_clock::now().time_since_epoch()).count();
    std::fprintf(fp, "%.3f %s %p ", t, node ? node : "?", op);
    va_list ap;
    va_start(ap, fmt);
    std::vfprintf(fp, fmt, ap);
    va_end(ap);
    std::fputc('\n', fp);
    std::fclose(fp);
}

} // namespace flaresim
