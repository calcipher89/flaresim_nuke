// lens_path_test.cpp — checks resolve_lens_path() finds moved lens files.
#include "lens_path.h"

#include <cstdio>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

namespace fs = std::filesystem;

static int failures = 0;

static void expect(const std::string& got, const std::string& want, const char* what)
{
    if (got != want) {
        std::printf("FAIL %s\n  got:  %s\n  want: %s\n", what, got.c_str(), want.c_str());
        ++failures;
    } else {
        std::printf("ok   %s\n", what);
    }
}

static std::string touch(const fs::path& p)
{
    fs::create_directories(p.parent_path());
    std::ofstream(p) << "# lens\n";
    std::string s = p.generic_u8string();
    return s;
}

int main()
{
    const fs::path tmp = fs::temp_directory_path() / "flaresim_lens_path_test";
    fs::remove_all(tmp);
    const fs::path bundled = tmp / "plugins" / "FlareSim" / "lenses";
    const fs::path studio  = tmp / "studio_lenses";
    const std::string zeiss = touch(bundled / "lens_files" / "Zeiss_50mm_F1.4.lens");
    const std::string cooke = touch(studio / "cooke" / "Cooke_S4_32mm.lens");
    const std::vector<std::string> roots = {
        studio.generic_u8string(), bundled.generic_u8string() };

    expect(flaresim::resolve_lens_path(zeiss, roots), zeiss, "existing path kept");
    expect(flaresim::resolve_lens_path(
               "C:\\Program Files\\Nuke15.1v3\\plugins\\FlareSim\\lenses\\lens_files\\Zeiss_50mm_F1.4.lens",
               roots), zeiss, "Windows install path");
    expect(flaresim::resolve_lens_path(
               "/opt/nuke/plugins/FlareSim-1.0/lenses/lens_files/Zeiss_50mm_F1.4.lens", roots),
           zeiss, "moved install folder");
    expect(flaresim::resolve_lens_path("lens_files/Zeiss_50mm_F1.4.lens", roots),
           zeiss, "relative path");
    expect(flaresim::resolve_lens_path(
               "/home/jeff/.nuke/FlareSim/lenses/Cooke_S4_32mm.lens", roots),
           cooke, "imported lens found in studio folder");
    expect(flaresim::resolve_lens_path("D:/stuff/cooke_s4_32MM.lens", roots),
           cooke, "case-insensitive file name");
    expect(flaresim::resolve_lens_path("/nowhere/Missing.lens", roots),
           "", "missing lens");
    expect(flaresim::resolve_lens_path("", roots), "", "empty path");

    fs::remove_all(tmp);
    if (failures) std::printf("%d failure(s)\n", failures);
    return failures ? 1 : 0;
}
