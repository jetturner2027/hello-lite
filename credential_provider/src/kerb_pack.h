#pragma once
#include <windows.h>
#include <credentialprovider.h>

namespace fu {

// Build the KERB_INTERACTIVE_UNLOCK_LOGON blob LogonUI hands to LSA. The message type
// follows the scenario, as in Microsoft's SampleV2: KerbWorkstationUnlockLogon for
// CPUS_UNLOCK_WORKSTATION, KerbInteractiveLogon for CPUS_LOGON (which is what the
// Windows 10+ lock screen uses; Winlogon turns it into an unlock of the locked session).
// The password is CredProtect'ed first, as SampleV2 does for non-CredUI scenarios.
// On success *out is CoTaskMemAlloc'ed; the caller (LogonUI) frees it.
HRESULT PackLogon(CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus, const wchar_t* domain, const wchar_t* user,
                  const wchar_t* password, BYTE** out, ULONG* cb);

// Look up the Negotiate package id for CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION.
HRESULT NegotiatePackage(ULONG* package);

}  // namespace fu
