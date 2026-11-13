#include "scenario.h"

#include <wtsapi32.h>

namespace fu {

bool ShouldParticipate(CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus, const SessionState& s) {
    switch (cpus) {
        case CPUS_UNLOCK_WORKSTATION:  // pre-Windows 10 lock screen; still unlock by definition
            return true;
        case CPUS_LOGON:
            return s.known && s.hasUser && s.locked;
        default:  // CPUS_CREDUI (UAC, app prompts), CPUS_CHANGE_PASSWORD, ...
            return false;
    }
}

SessionState CurrentSessionState() {
    SessionState s;
    LPWSTR buf = nullptr;
    DWORD bytes = 0;
    if (!WTSQuerySessionInformationW(WTS_CURRENT_SERVER_HANDLE, WTS_CURRENT_SESSION, WTSSessionInfoEx, &buf, &bytes)) {
        return s;
    }
    const auto* info = reinterpret_cast<const WTSINFOEXW*>(buf);
    if (bytes >= sizeof(WTSINFOEXW) && info->Level == 1) {
        const WTSINFOEX_LEVEL1_W& l1 = info->Data.WTSInfoExLevel1;
        s.known = true;
        s.hasUser = l1.UserName[0] != L'\0';
        // SessionFlags is reported inverted only on Windows 7 / Server 2008 R2; we target 10+.
        s.locked = l1.SessionFlags == WTS_SESSIONSTATE_LOCK;
    }
    WTSFreeMemory(buf);
    return s;
}

}  // namespace fu
