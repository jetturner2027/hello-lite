#pragma once
#include <windows.h>
#include <credentialprovider.h>

#include <atomic>
#include <memory>

namespace fu {

// Background face check while the lock screen is up, so you don't have to click
// the tile. Same pattern as Microsoft's hardware-event sample: a worker thread
// does the work, then calls ICredentialProviderEvents::CredentialsChanged; LogonUI
// re-asks GetCredentialCount, we answer "default = our tile, auto-logon = yes",
// and LogonUI calls GetSerialization, which submits without verifying again.
//
// The PIN/password tiles are untouched and stay usable the whole time.
//
// Lifetime: the thread holds a shared_ptr to this object and a reference on the
// DLL module (released with FreeLibraryAndExitThread), so LogonUI can release the
// provider at any moment, even mid-verify, without waiting and without the DLL
// being unloaded under a running thread.
class AutoUnlock {
public:
    static std::shared_ptr<AutoUnlock> Create() noexcept;
    ~AutoUnlock();
    AutoUnlock(const AutoUnlock&) = delete;
    AutoUnlock& operator=(const AutoUnlock&) = delete;

    void SetEvents(ICredentialProviderEvents* events, UINT_PTR context);
    void ClearEvents();
    // Starts the worker once (no-op afterwards or if there are no events yet).
    void Start(const std::shared_ptr<AutoUnlock>& self);
    void Stop();

    // A background verify succeeded within the last kVerifiedTtlMs.
    bool PeekVerified() const;
    // Same, and consumes it: one verify, one submission.
    bool TakeVerified();

    static constexpr ULONGLONG kVerifiedTtlMs = 15000;

private:
    AutoUnlock() = default;
    static DWORD WINAPI ThreadMain(void* param);
    void Run();
    void Notify();

    mutable SRWLOCK lock_ = SRWLOCK_INIT;
    ICredentialProviderEvents* events_ = nullptr;  // guarded by lock_
    UINT_PTR context_ = 0;                         // guarded by lock_
    bool started_ = false;                         // guarded by lock_
    HANDLE stop_ = nullptr;                        // manual-reset event
    std::atomic<ULONGLONG> verifiedAt_{0};
};

}  // namespace fu
