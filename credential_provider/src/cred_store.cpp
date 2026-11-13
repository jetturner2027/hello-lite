#include "cred_store.h"

#include <dpapi.h>
#include <shlobj.h>

#include <cstring>
#include <vector>

namespace fu {
namespace {

constexpr char kEntropy[] = "FaceUnlock.Credential.v1";  // must match stores.CREDENTIAL_ENTROPY
constexpr uint32_t kMaxFieldChars = 512;
constexpr DWORD kMaxFileBytes = 64 * 1024;

bool ReadField(const BYTE* data, size_t len, size_t* pos, const wchar_t** chars, uint32_t* count) {
    if (len - *pos < 4) return false;
    uint32_t n;
    std::memcpy(&n, data + *pos, 4);
    *pos += 4;
    if (n < 1 || n > kMaxFieldChars || (len - *pos) / 2 < n) return false;
    *chars = reinterpret_cast<const wchar_t*>(data + *pos);
    *count = n;
    *pos += size_t{n} * 2;
    return true;
}

}  // namespace

bool ParseCredential(const BYTE* data, size_t len, StoredCredential* out) {
    if (len < 4 || std::memcmp(data, "FUC1", 4) != 0) return false;
    size_t pos = 4;
    const wchar_t* f[4];
    uint32_t n[4];
    for (int i = 0; i < 4; ++i) {
        if (!ReadField(data, len, &pos, &f[i], &n[i])) return false;
    }
    if (pos != len) return false;
    // Fields start at 4-byte headers plus whole UTF-16 units, so every wchar_t is
    // 2-byte aligned given an aligned buffer (DPAPI output is LocalAlloc'ed).
    out->domain.assign(f[0], n[0]);
    out->user.assign(f[1], n[1]);
    out->sid.assign(f[2], n[2]);
    out->password.Assign(f[3], n[3]);
    for (const std::wstring* s : {&out->domain, &out->user, &out->sid}) {
        if (s->find(L'\0') != std::wstring::npos) return false;
    }
    return true;
}

HRESULT LoadCredential(StoredCredential* out) {
    PWSTR pd = nullptr;
    if (FAILED(SHGetKnownFolderPath(FOLDERID_ProgramData, 0, nullptr, &pd))) return E_FAIL;
    std::wstring path = std::wstring(pd) + L"\\FaceUnlock\\secrets\\credential.bin";
    CoTaskMemFree(pd);

    HANDLE f = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING, 0, nullptr);
    if (f == INVALID_HANDLE_VALUE) return HRESULT_FROM_WIN32(GetLastError());
    std::vector<BYTE> blob;
    LARGE_INTEGER size{};
    BOOL ok = GetFileSizeEx(f, &size) && size.QuadPart > 0 && size.QuadPart <= kMaxFileBytes;
    DWORD read = 0;
    if (ok) {
        blob.resize(static_cast<size_t>(size.QuadPart));
        ok = ReadFile(f, blob.data(), static_cast<DWORD>(blob.size()), &read, nullptr) && read == blob.size();
    }
    CloseHandle(f);
    if (!ok) return E_FAIL;

    DATA_BLOB in{static_cast<DWORD>(blob.size()), blob.data()};
    DATA_BLOB entropy{sizeof(kEntropy) - 1, reinterpret_cast<BYTE*>(const_cast<char*>(kEntropy))};
    DATA_BLOB plain{};
    if (!CryptUnprotectData(&in, nullptr, &entropy, nullptr, nullptr, CRYPTPROTECT_UI_FORBIDDEN, &plain)) {
        return HRESULT_FROM_WIN32(GetLastError());
    }
    bool parsed = ParseCredential(plain.pbData, plain.cbData, out);
    SecureZeroMemory(plain.pbData, plain.cbData);
    LocalFree(plain.pbData);
    if (!parsed) {
        out->password.Wipe();
        return E_UNEXPECTED;
    }
    return S_OK;
}

}  // namespace fu
