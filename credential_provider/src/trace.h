#pragma once
#include <windows.h>

namespace fu {

// One-line diagnostics from inside LogonUI to %ProgramData%\FaceUnlock\logs\cp.log
// (the logs folder is SYSTEM/Admins-writable, user-readable). Event names and
// numbers only: never a password, SID, user name, or anything from a frame.
// Best effort and bounded: the file restarts once it passes 64 KB.
void Trace(const char* fmt, ...) noexcept;

}  // namespace fu
