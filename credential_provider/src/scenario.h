#pragma once
#include <windows.h>
#include <credentialprovider.h>

namespace fu {

// What the current LogonUI session looks like, from WTS.
struct SessionState {
    bool known = false;    // the query succeeded
    bool hasUser = false;  // a user is logged on in this session
    bool locked = false;   // ...and it is locked
};

// Since Windows 10, the lock screen asks providers with CPUS_LOGON: Microsoft
// "combined" CPUS_LOGON and CPUS_UNLOCK_WORKSTATION. So CPUS_LOGON alone can't tell
// a locked session from boot / first logon / "switch user". We take part only when
// this very session has a logged-on user and is locked; anything unknown means no.
// (The service separately refuses unless it saw the console session lock.)
bool ShouldParticipate(CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus, const SessionState& s);

SessionState CurrentSessionState();

}  // namespace fu
