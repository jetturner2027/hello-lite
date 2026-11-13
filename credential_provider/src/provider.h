#pragma once
#include <windows.h>
#include <credentialprovider.h>

#include <memory>
#include <string>

#include "auto_unlock.h"

namespace fu {

class Credential;

// Shows one tile, only on the lock screen of a locked session (see scenario.h), and only for the user whose SID
// is in the stored credential file. Everything else: no tiles. The password
// provider is untouched, because we never implement ICredentialProviderFilter.
class Provider final : public ICredentialProvider, public ICredentialProviderSetUserArray {
public:
    Provider();

    IFACEMETHODIMP_(ULONG) AddRef() override;
    IFACEMETHODIMP_(ULONG) Release() override;
    IFACEMETHODIMP QueryInterface(REFIID riid, void** ppv) override;

    // ICredentialProvider
    IFACEMETHODIMP SetUsageScenario(CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus, DWORD flags) override;
    IFACEMETHODIMP SetSerialization(const CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION*) override { return E_NOTIMPL; }
    IFACEMETHODIMP Advise(ICredentialProviderEvents* events, UINT_PTR context) override;
    IFACEMETHODIMP UnAdvise() override;
    IFACEMETHODIMP GetFieldDescriptorCount(DWORD* count) override;
    IFACEMETHODIMP GetFieldDescriptorAt(DWORD index, CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR** out) override;
    IFACEMETHODIMP GetCredentialCount(DWORD* count, DWORD* defaultIndex, BOOL* autoLogonWithDefault) override;
    IFACEMETHODIMP GetCredentialAt(DWORD index, ICredentialProviderCredential** out) override;

    // ICredentialProviderSetUserArray
    IFACEMETHODIMP SetUserArray(ICredentialProviderUserArray* users) override;

private:
    ~Provider();
    void MaybeStartAuto();

    LONG refs_ = 1;
    bool unlockScenario_ = false;
    CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus_ = CPUS_INVALID;
    Credential* credential_ = nullptr;
    std::shared_ptr<AutoUnlock> auto_;  // null if allocation failed: then click-only
};

}  // namespace fu
