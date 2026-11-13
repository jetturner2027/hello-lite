#include "pipe_client.h"

#include <bcrypt.h>
#include <shlobj.h>

#include <memory>
#include <string>

namespace fu {
namespace {

struct HandleCloser {
    void operator()(HANDLE h) const {
        if (h && h != INVALID_HANDLE_VALUE) CloseHandle(h);
    }
};
using UniqueHandle = std::unique_ptr<void, HandleCloser>;

std::wstring ExpectedServerImage() {
    PWSTR pf = nullptr;
    std::wstring path;
    if (SUCCEEDED(SHGetKnownFolderPath(FOLDERID_ProgramFiles, 0, nullptr, &pf))) {
        path = std::wstring(pf) + L"\\FaceUnlock\\python\\python.exe";
    }
    CoTaskMemFree(pf);
    return path;
}

// Overlapped I/O with a deadline, so a hung service can't hang LogonUI.
bool IoWithTimeout(HANDLE pipe, bool write, void* buf, DWORD len, DWORD timeoutMs, DWORD* done) {
    OVERLAPPED ov{};
    UniqueHandle ev(CreateEventW(nullptr, TRUE, FALSE, nullptr));
    if (!ev) return false;
    ov.hEvent = ev.get();
    BOOL ok = write ? WriteFile(pipe, buf, len, nullptr, &ov) : ReadFile(pipe, buf, len, nullptr, &ov);
    if (!ok && GetLastError() != ERROR_IO_PENDING) return false;
    if (WaitForSingleObject(ov.hEvent, timeoutMs) != WAIT_OBJECT_0) {
        CancelIoEx(pipe, &ov);
        GetOverlappedResult(pipe, &ov, done, TRUE);  // wait for the cancel before `ov` goes out of scope
        return false;
    }
    return GetOverlappedResult(pipe, &ov, done, FALSE) != FALSE;
}

}  // namespace

bool IsTrustedServer(ULONG pid, const wchar_t* expectedImage) {
    if (!expectedImage || !*expectedImage) return false;
    UniqueHandle proc(OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid));
    if (!proc) return false;

    HANDLE rawToken = nullptr;
    if (!OpenProcessToken(proc.get(), TOKEN_QUERY, &rawToken)) return false;
    UniqueHandle token(rawToken);
    BYTE userBuf[SECURITY_MAX_SID_SIZE + sizeof(TOKEN_USER)];
    DWORD needed = 0;
    if (!GetTokenInformation(token.get(), TokenUser, userBuf, sizeof(userBuf), &needed)) return false;
    if (!IsWellKnownSid(reinterpret_cast<TOKEN_USER*>(userBuf)->User.Sid, WinLocalSystemSid)) return false;

    wchar_t image[MAX_PATH * 2];
    DWORD size = ARRAYSIZE(image);
    if (!QueryFullProcessImageNameW(proc.get(), 0, image, &size)) return false;
    return CompareStringOrdinal(image, -1, expectedImage, -1, TRUE) == CSTR_EQUAL;
}

HRESULT CallService(MsgType type, Direction dir, DWORD timeoutMs, Status* status, Reason* reason,
                    uint8_t* flags) {
    uint8_t nonce[kNonceLen];
    if (!BCRYPT_SUCCESS(BCryptGenRandom(nullptr, nonce, sizeof(nonce), BCRYPT_USE_SYSTEM_PREFERRED_RNG))) {
        return E_FAIL;
    }

    // SECURITY_IDENTIFICATION: the service may learn who we are, but can't
    // impersonate our SYSTEM token. Without SQOS flags the default is full
    // impersonation, and that matters a lot when the client is LogonUI.
    const DWORD openFlags = FILE_FLAG_OVERLAPPED | SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION;
    UniqueHandle pipe(CreateFileW(kPipeName, GENERIC_READ | GENERIC_WRITE, 0, nullptr, OPEN_EXISTING, openFlags, nullptr));
    if (pipe.get() == INVALID_HANDLE_VALUE && GetLastError() == ERROR_PIPE_BUSY && WaitNamedPipeW(kPipeName, 2000)) {
        pipe.reset(CreateFileW(kPipeName, GENERIC_READ | GENERIC_WRITE, 0, nullptr, OPEN_EXISTING, openFlags, nullptr));
    }
    if (pipe.get() == INVALID_HANDLE_VALUE) return HRESULT_FROM_WIN32(GetLastError());

    DWORD mode = PIPE_READMODE_MESSAGE;
    if (!SetNamedPipeHandleState(pipe.get(), &mode, nullptr, nullptr)) return HRESULT_FROM_WIN32(GetLastError());

    // Before sending anything: is the other end really our service?
    ULONG serverPid = 0;
    if (!GetNamedPipeServerProcessId(pipe.get(), &serverPid)) return HRESULT_FROM_WIN32(GetLastError());
    if (!IsTrustedServer(serverPid, ExpectedServerImage().c_str())) return E_ACCESSDENIED;

    Request req = MakeRequest(type, dir, nonce);
    DWORD done = 0;
    if (!IoWithTimeout(pipe.get(), true, &req, sizeof(req), 3000, &done) || done != sizeof(req)) return E_FAIL;

    uint8_t buf[kMsgLen + 1];  // +1 so an oversized message is detectable
    if (!IoWithTimeout(pipe.get(), false, buf, sizeof(buf), timeoutMs, &done)) return E_FAIL;
    Response resp;
    if (!ParseResponse(buf, done, type, nonce, &resp)) return E_UNEXPECTED;
    *status = static_cast<Status>(resp.status);
    *reason = static_cast<Reason>(resp.reason);
    if (flags) *flags = resp.flags;
    return S_OK;
}

}  // namespace fu
