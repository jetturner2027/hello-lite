#include "provider.h"

#include <shlwapi.h>

#include <new>

#include "cred_store.h"
#include "credential.h"
#include "dll.h"
#include "scenario.h"
#include "trace.h"

namespace fu {

Provider::Provider() : auto_(AutoUnlock::Create()) { DllAddRef(); }

Provider::~Provider() {
    if (auto_) {
        auto_->ClearEvents();
        auto_->Stop();
    }
    if (credential_) credential_->Release();
    DllRelease();
}

IFACEMETHODIMP Provider::Advise(ICredentialProviderEvents* events, UINT_PTR context) {
    Trace("Provider::Advise events=%d auto=%d", events != nullptr, auto_ != nullptr);
    if (auto_) auto_->SetEvents(events, context);
    MaybeStartAuto();
    return S_OK;
}

IFACEMETHODIMP Provider::UnAdvise() {
    Trace("Provider::UnAdvise");
    if (auto_) {
        auto_->ClearEvents();
        auto_->Stop();  // LogonUI is done with us; never notify after this
    }
    return S_OK;
}

// Start the background check only once we know this is a locked session's
// lock screen and the locked user is the one with a stored credential.
void Provider::MaybeStartAuto() {
    if (auto_ && unlockScenario_ && credential_) auto_->Start(auto_);
}

IFACEMETHODIMP_(ULONG) Provider::AddRef() { return InterlockedIncrement(&refs_); }

IFACEMETHODIMP_(ULONG) Provider::Release() {
    LONG r = InterlockedDecrement(&refs_);
    if (r == 0) delete this;
    return r;
}

IFACEMETHODIMP Provider::QueryInterface(REFIID riid, void** ppv) {
    static const QITAB qit[] = {
        QITABENT(Provider, ICredentialProvider),
        QITABENT(Provider, ICredentialProviderSetUserArray),
        {},
    };
    return QISearch(this, qit, riid, ppv);
}

IFACEMETHODIMP Provider::SetUsageScenario(CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus, DWORD) {
    // Unlock only. Boot / first logon, "switch user", CPUS_CREDUI (UAC and app
    // prompts) and CPUS_CHANGE_PASSWORD get E_NOTIMPL, which tells LogonUI this
    // provider doesn't take part, so no tile appears there at all. Windows 10+
    // reports the lock screen as CPUS_LOGON, hence the session check.
    const SessionState s = CurrentSessionState();
    unlockScenario_ = ShouldParticipate(cpus, s);
    Trace("SetUsageScenario cpus=%d known=%d user=%d locked=%d -> %s", static_cast<int>(cpus), s.known, s.hasUser,
          s.locked, unlockScenario_ ? "participate" : "E_NOTIMPL");
    cpus_ = cpus;
    return unlockScenario_ ? S_OK : E_NOTIMPL;
}

IFACEMETHODIMP Provider::GetFieldDescriptorCount(DWORD* count) {
    *count = FI_COUNT;
    return S_OK;
}

IFACEMETHODIMP Provider::GetFieldDescriptorAt(DWORD index, CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR** out) {
    *out = nullptr;
    if (index >= FI_COUNT) return E_INVALIDARG;
    auto* d = static_cast<CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR*>(CoTaskMemAlloc(sizeof(**out)));
    if (!d) return E_OUTOFMEMORY;
    *d = kFields[index];
    HRESULT hr = SHStrDupW(kFields[index].pszLabel, &d->pszLabel);
    if (FAILED(hr)) {
        CoTaskMemFree(d);
        return hr;
    }
    *out = d;
    return S_OK;
}

IFACEMETHODIMP Provider::SetUserArray(ICredentialProviderUserArray* users) {
    if (credential_) {
        credential_->Release();
        credential_ = nullptr;
    }
    if (!unlockScenario_ || !users) return S_OK;

    // Only the SID is needed here; the password is wiped as `stored` goes out of scope.
    StoredCredential stored;
    if (FAILED(LoadCredential(&stored))) {
        Trace("SetUserArray: no stored credential");
        return S_OK;  // not set up: show nothing
    }
    stored.password.Wipe();

    DWORD n = 0;
    if (FAILED(users->GetCount(&n))) return S_OK;
    for (DWORD i = 0; i < n; ++i) {
        ICredentialProviderUser* user = nullptr;
        if (FAILED(users->GetAt(i, &user))) continue;
        PWSTR sid = nullptr;
        bool match = SUCCEEDED(user->GetSid(&sid)) &&
                     CompareStringOrdinal(sid, -1, stored.sid.c_str(), -1, TRUE) == CSTR_EQUAL;
        CoTaskMemFree(sid);
        user->Release();
        if (match) {
            try {
                credential_ = new (std::nothrow) Credential(stored.sid, cpus_, auto_);
            } catch (...) {  // std::wstring copy; never throw into LogonUI
                credential_ = nullptr;
            }
            break;
        }
    }
    Trace("SetUserArray users=%lu tile=%d", n, credential_ != nullptr);
    MaybeStartAuto();
    return S_OK;
}

IFACEMETHODIMP Provider::GetCredentialCount(DWORD* count, DWORD* defaultIndex, BOOL* autoLogonWithDefault) {
    *count = credential_ ? 1 : 0;
    // Normally the PIN/password tile stays the default and we never auto-logon.
    // Only right after a background verify succeeded (AutoUnlock called
    // CredentialsChanged) do we ask LogonUI to submit our tile now.
    const bool go = credential_ && auto_ && auto_->PeekVerified();
    *defaultIndex = go ? 0 : CREDENTIAL_PROVIDER_NO_DEFAULT;
    *autoLogonWithDefault = go ? TRUE : FALSE;
    Trace("GetCredentialCount count=%lu autologon=%d", *count, go);
    return S_OK;
}

IFACEMETHODIMP Provider::GetCredentialAt(DWORD index, ICredentialProviderCredential** out) {
    *out = nullptr;
    if (index != 0 || !credential_) return E_INVALIDARG;
    return credential_->QueryInterface(IID_PPV_ARGS(out));
}

}  // namespace fu
