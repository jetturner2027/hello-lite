#pragma once
#include <windows.h>
#include <credentialprovider.h>

#include <memory>
#include <string>

#include "auto_unlock.h"

#include "protocol.h"

namespace fu {

enum FieldId : DWORD { FI_TILE_IMAGE = 0, FI_LABEL, FI_STATUS, FI_SUBMIT, FI_COUNT };

extern const CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR kFields[FI_COUNT];

// One tile, for the one enrolled user. Clicking it runs a verify in
// GetSerialization. Separately, AutoUnlock may verify in the background; then
// LogonUI calls GetSerialization by itself and we submit without verifying twice.
class Credential final : public ICredentialProviderCredential2 {
public:
    Credential(std::wstring sid, CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus, std::shared_ptr<AutoUnlock> autoUnlock);

    // IUnknown
    IFACEMETHODIMP_(ULONG) AddRef() override;
    IFACEMETHODIMP_(ULONG) Release() override;
    IFACEMETHODIMP QueryInterface(REFIID riid, void** ppv) override;

    // ICredentialProviderCredential
    IFACEMETHODIMP Advise(ICredentialProviderCredentialEvents* events) override;
    IFACEMETHODIMP UnAdvise() override;
    IFACEMETHODIMP SetSelected(BOOL* autoLogon) override;
    IFACEMETHODIMP SetDeselected() override;
    IFACEMETHODIMP GetFieldState(DWORD id, CREDENTIAL_PROVIDER_FIELD_STATE* state,
                                 CREDENTIAL_PROVIDER_FIELD_INTERACTIVE_STATE* istate) override;
    IFACEMETHODIMP GetStringValue(DWORD id, PWSTR* value) override;
    IFACEMETHODIMP GetBitmapValue(DWORD id, HBITMAP* bmp) override;
    IFACEMETHODIMP GetCheckboxValue(DWORD, BOOL*, PWSTR*) override { return E_NOTIMPL; }
    IFACEMETHODIMP GetSubmitButtonValue(DWORD id, DWORD* adjacentTo) override;
    IFACEMETHODIMP GetComboBoxValueCount(DWORD, DWORD*, DWORD*) override { return E_NOTIMPL; }
    IFACEMETHODIMP GetComboBoxValueAt(DWORD, DWORD, PWSTR*) override { return E_NOTIMPL; }
    IFACEMETHODIMP SetStringValue(DWORD, PCWSTR) override { return E_NOTIMPL; }
    IFACEMETHODIMP SetCheckboxValue(DWORD, BOOL) override { return E_NOTIMPL; }
    IFACEMETHODIMP SetComboBoxSelectedValue(DWORD, DWORD) override { return E_NOTIMPL; }
    IFACEMETHODIMP CommandLinkClicked(DWORD) override { return E_NOTIMPL; }
    IFACEMETHODIMP GetSerialization(CREDENTIAL_PROVIDER_GET_SERIALIZATION_RESPONSE* response,
                                    CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION* serialization,
                                    PWSTR* optionalStatusText, CREDENTIAL_PROVIDER_STATUS_ICON* optionalIcon) override;
    IFACEMETHODIMP ReportResult(NTSTATUS status, NTSTATUS substatus, PWSTR* optionalStatusText,
                                CREDENTIAL_PROVIDER_STATUS_ICON* optionalIcon) override;

    // ICredentialProviderCredential2
    IFACEMETHODIMP GetUserSid(PWSTR* sid) override;

private:
    ~Credential();
    void NewChallenge();
    void SetStatus(const std::wstring& text);
    std::wstring Instructions() const;

    LONG refs_ = 1;
    std::wstring sid_;
    CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus_;
    std::wstring status_;
    Direction direction_ = Direction::Left;
    bool challenge_ = false;  // learned from the service's STATUS flags
    bool submitted_ = false;
    ICredentialProviderCredentialEvents* events_ = nullptr;
    std::shared_ptr<AutoUnlock> auto_;
};

}  // namespace fu
