#pragma once
#include <windows.h>

#include "protocol.h"

namespace fu {

// One request/response round trip with face_service. Fails closed: any error
// in connecting, verifying the server, I/O, or parsing returns a failure HRESULT.
HRESULT CallService(MsgType type, Direction dir, DWORD timeoutMs, Status* status, Reason* reason,
                    uint8_t* flags = nullptr);

// Exposed for tests: is `pid` a SYSTEM process running exactly `expectedImage`?
bool IsTrustedServer(ULONG pid, const wchar_t* expectedImage);

}  // namespace fu
