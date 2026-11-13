#pragma once
#include <windows.h>

#include <cstddef>
#include <string>

namespace fu {

// std::wstring that wipes its buffer on destruction. Copies are deleted so the
// password can't silently spread into unwiped temporaries.
class SecretWString {
public:
    SecretWString() = default;
    SecretWString(const SecretWString&) = delete;
    SecretWString& operator=(const SecretWString&) = delete;
    ~SecretWString() { Wipe(); }
    void Assign(const wchar_t* p, size_t n) { Wipe(); s_.assign(p, n); }
    const wchar_t* c_str() const { return s_.c_str(); }
    size_t size() const { return s_.size(); }
    void Wipe() {
        if (!s_.empty()) SecureZeroMemory(&s_[0], s_.size() * sizeof(wchar_t));
        s_.clear();
    }

private:
    std::wstring s_;
};

struct StoredCredential {
    std::wstring domain;
    std::wstring user;
    std::wstring sid;
    SecretWString password;
};

// Parse the plaintext layout written by face_unlock/stores.py:
// "FUC1" | (u32 char count, UTF-16LE chars) x 4: domain, user, sid, password.
bool ParseCredential(const BYTE* data, size_t len, StoredCredential* out);

// Read %ProgramData%\FaceUnlock\secrets\credential.bin and DPAPI-decrypt it.
HRESULT LoadCredential(StoredCredential* out);

}  // namespace fu
