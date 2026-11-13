#include "kerb_pack.h"

#define SECURITY_WIN32
#include <ntsecapi.h>
#include <security.h>
#include <wincred.h>

#include <cstring>
#include <cwchar>
#include <vector>

namespace fu {
namespace {

// The serialized form stores each UNICODE_STRING's Buffer as an *offset* from
// the start of the blob, because the blob crosses into LSA's address space.
void PutString(BYTE* base, size_t* pos, const wchar_t* s, USHORT cb, UNICODE_STRING* us) {
    std::memcpy(base + *pos, s, cb);
    us->Length = cb;
    us->MaximumLength = cb;
    us->Buffer = reinterpret_cast<PWSTR>(*pos);
    *pos += cb;
}

// CredProtect the password in place of the plaintext; LSA unprotects it.
HRESULT ProtectPassword(const wchar_t* password, std::vector<wchar_t>* out) {
    CRED_PROTECTION_TYPE type;
    if (CredIsProtectedW(const_cast<LPWSTR>(password), &type) && type != CredUnprotected) {
        out->assign(password, password + wcslen(password) + 1);
        return S_OK;
    }
    DWORD cch = 0;
    CredProtectW(FALSE, const_cast<LPWSTR>(password), static_cast<DWORD>(wcslen(password) + 1), nullptr, &cch,
                 nullptr);
    if (GetLastError() != ERROR_INSUFFICIENT_BUFFER || cch == 0) return HRESULT_FROM_WIN32(GetLastError());
    out->resize(cch);
    if (!CredProtectW(FALSE, const_cast<LPWSTR>(password), static_cast<DWORD>(wcslen(password) + 1), out->data(),
                      &cch, nullptr)) {
        return HRESULT_FROM_WIN32(GetLastError());
    }
    return S_OK;
}

}  // namespace

HRESULT PackLogon(CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus, const wchar_t* domain, const wchar_t* user,
                  const wchar_t* password, BYTE** out, ULONG* cb) {
    *out = nullptr;
    *cb = 0;
    if (cpus != CPUS_LOGON && cpus != CPUS_UNLOCK_WORKSTATION) return E_INVALIDARG;
    std::vector<wchar_t> protectedPw;
    HRESULT hr = ProtectPassword(password, &protectedPw);
    if (FAILED(hr)) return hr;

    size_t cbDomain = wcslen(domain) * sizeof(wchar_t);
    size_t cbUser = wcslen(user) * sizeof(wchar_t);
    size_t cbPw = wcslen(protectedPw.data()) * sizeof(wchar_t);
    if (cbDomain > USHRT_MAX || cbUser > USHRT_MAX || cbPw > USHRT_MAX) {
        SecureZeroMemory(protectedPw.data(), protectedPw.size() * sizeof(wchar_t));
        return E_INVALIDARG;
    }
    size_t total = sizeof(KERB_INTERACTIVE_UNLOCK_LOGON) + cbDomain + cbUser + cbPw;
    BYTE* buf = static_cast<BYTE*>(CoTaskMemAlloc(total));
    if (!buf) {
        SecureZeroMemory(protectedPw.data(), protectedPw.size() * sizeof(wchar_t));
        return E_OUTOFMEMORY;
    }
    ZeroMemory(buf, total);

    auto* kiul = reinterpret_cast<KERB_INTERACTIVE_UNLOCK_LOGON*>(buf);
    KERB_INTERACTIVE_LOGON* kil = &kiul->Logon;
    // LogonId stays zero; LogonUI fills in the session.
    kil->MessageType = cpus == CPUS_UNLOCK_WORKSTATION ? KerbWorkstationUnlockLogon : KerbInteractiveLogon;
    size_t pos = sizeof(KERB_INTERACTIVE_UNLOCK_LOGON);
    PutString(buf, &pos, domain, static_cast<USHORT>(cbDomain), &kil->LogonDomainName);
    PutString(buf, &pos, user, static_cast<USHORT>(cbUser), &kil->UserName);
    PutString(buf, &pos, protectedPw.data(), static_cast<USHORT>(cbPw), &kil->Password);
    SecureZeroMemory(protectedPw.data(), protectedPw.size() * sizeof(wchar_t));

    *out = buf;
    *cb = static_cast<ULONG>(total);
    return S_OK;
}

HRESULT NegotiatePackage(ULONG* package) {
    HANDLE lsa = nullptr;
    NTSTATUS st = LsaConnectUntrusted(&lsa);
    if (st != 0) return HRESULT_FROM_NT(st);
    char name[] = NEGOSSP_NAME_A;
    LSA_STRING ls{static_cast<USHORT>(strlen(name)), static_cast<USHORT>(sizeof(name)), name};
    st = LsaLookupAuthenticationPackage(lsa, &ls, package);
    LsaDeregisterLogonProcess(lsa);
    return st == 0 ? S_OK : HRESULT_FROM_NT(st);
}

}  // namespace fu
