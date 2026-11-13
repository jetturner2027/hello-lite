#include "trace.h"

#include <shlobj.h>

#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <cwchar>

namespace fu {

void Trace(const char* fmt, ...) noexcept {
    char line[256];
    SYSTEMTIME t;
    GetLocalTime(&t);
    int n = std::snprintf(line, sizeof(line), "%04u-%02u-%02u %02u:%02u:%02u.%03u pid=%lu tid=%lu ", t.wYear,
                          t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond, t.wMilliseconds, GetCurrentProcessId(),
                          GetCurrentThreadId());
    if (n < 0 || n >= static_cast<int>(sizeof(line))) return;
    va_list ap;
    va_start(ap, fmt);
    int m = std::vsnprintf(line + n, sizeof(line) - n - 2, fmt, ap);
    va_end(ap);
    if (m < 0) return;
    size_t len = strnlen(line, sizeof(line) - 2);
    line[len++] = '\r';
    line[len++] = '\n';

    PWSTR pd = nullptr;
    if (FAILED(SHGetKnownFolderPath(FOLDERID_ProgramData, 0, nullptr, &pd))) return;
    wchar_t path[MAX_PATH];
    int ok = std::swprintf(path, MAX_PATH, L"%s\\FaceUnlock\\logs\\cp.log", pd);
    CoTaskMemFree(pd);
    if (ok < 0) return;

    // OPEN_ALWAYS + FILE_APPEND_DATA: atomic appends from several threads/processes.
    HANDLE f = CreateFileW(path, FILE_APPEND_DATA | FILE_READ_ATTRIBUTES, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
                           OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (f == INVALID_HANDLE_VALUE) return;
    LARGE_INTEGER size{};
    if (GetFileSizeEx(f, &size) && size.QuadPart > 64 * 1024) {
        CloseHandle(f);
        f = CreateFileW(path, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, CREATE_ALWAYS,
                        FILE_ATTRIBUTE_NORMAL, nullptr);
        if (f == INVALID_HANDLE_VALUE) return;
    }
    DWORD written = 0;
    WriteFile(f, line, static_cast<DWORD>(len), &written, nullptr);
    CloseHandle(f);
}

}  // namespace fu
