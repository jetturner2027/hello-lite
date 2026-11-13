#include "auto_unlock.h"

#include <new>

#include "auto_policy.h"
#include "pipe_client.h"
#include "trace.h"

namespace fu {
namespace {

constexpr DWORD kVerifyTimeoutMs = 20000;  // same as the tile's own verify
constexpr DWORD kPollMs = 250;
constexpr wchar_t kPropState[] = L"FaceUnlockAutoState";
constexpr wchar_t kPropOldProc[] = L"FaceUnlockAutoOldProc";

// Display state from GUID_CONSOLE_DISPLAY_STATE: 0 off, 1 on, 2 dimmed.
struct DisplayWatch {
    DWORD state = 1;
    bool known = false;
    bool woke = false;  // went from off/dimmed to on since last checked
};

LRESULT CALLBACK DisplayProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    auto oldProc = reinterpret_cast<WNDPROC>(GetPropW(hwnd, kPropOldProc));
    if (msg == WM_POWERBROADCAST && wp == PBT_POWERSETTINGCHANGE && lp) {
        auto* ws = static_cast<DisplayWatch*>(GetPropW(hwnd, kPropState));
        const auto* s = reinterpret_cast<const POWERBROADCAST_SETTING*>(lp);
        if (ws && s->PowerSetting == GUID_CONSOLE_DISPLAY_STATE && s->DataLength >= sizeof(DWORD)) {
            DWORD v = *reinterpret_cast<const DWORD*>(s->Data);
            // The first notification just reports the current state; it isn't a wake.
            if (ws->known && v == 1 && ws->state != 1) ws->woke = true;
            ws->state = v;
            ws->known = true;
        }
        return TRUE;
    }
    return oldProc ? CallWindowProcW(oldProc, hwnd, msg, wp, lp) : DefWindowProcW(hwnd, msg, wp, lp);
}

// A hidden, never-shown window that only receives display on/off notifications.
// It uses the built-in STATIC class (subclassed) so we never register a window
// class, which would outlive the DLL if LogonUI unloaded us.
class DisplayWindow {
public:
    explicit DisplayWindow(DisplayWatch* ws) {
        hwnd_ = CreateWindowExW(WS_EX_TOOLWINDOW, L"STATIC", L"", WS_POPUP, 0, 0, 0, 0, nullptr, nullptr, nullptr,
                                nullptr);
        if (!hwnd_) {
            Trace("auto: display window failed %lu", GetLastError());
            return;
        }
        SetPropW(hwnd_, kPropState, ws);
        LONG_PTR old = SetWindowLongPtrW(hwnd_, GWLP_WNDPROC, reinterpret_cast<LONG_PTR>(&DisplayProc));
        SetPropW(hwnd_, kPropOldProc, reinterpret_cast<HANDLE>(old));
        reg_ = RegisterPowerSettingNotification(hwnd_, &GUID_CONSOLE_DISPLAY_STATE, DEVICE_NOTIFY_WINDOW_HANDLE);
        Trace("auto: display window ok, power notify=%d", reg_ != nullptr);
    }
    ~DisplayWindow() {
        if (reg_) UnregisterPowerSettingNotification(reg_);
        if (hwnd_) {
            SetWindowLongPtrW(hwnd_, GWLP_WNDPROC, reinterpret_cast<LONG_PTR>(GetPropW(hwnd_, kPropOldProc)));
            RemovePropW(hwnd_, kPropOldProc);
            RemovePropW(hwnd_, kPropState);
            DestroyWindow(hwnd_);
        }
    }
    DisplayWindow(const DisplayWindow&) = delete;
    DisplayWindow& operator=(const DisplayWindow&) = delete;

private:
    HWND hwnd_ = nullptr;
    HPOWERNOTIFY reg_ = nullptr;
};

DWORD LastInputTick() {
    LASTINPUTINFO li{sizeof(li), 0};
    return GetLastInputInfo(&li) ? li.dwTime : 0;
}

struct ThreadArgs {
    std::shared_ptr<AutoUnlock> self;
    HMODULE module;
};

}  // namespace

std::shared_ptr<AutoUnlock> AutoUnlock::Create() noexcept {
    try {
        std::shared_ptr<AutoUnlock> a(new AutoUnlock());
        a->stop_ = CreateEventW(nullptr, TRUE, FALSE, nullptr);
        if (!a->stop_) return nullptr;
        return a;
    } catch (const std::bad_alloc&) {
        return nullptr;
    }
}

AutoUnlock::~AutoUnlock() {
    ClearEvents();
    if (stop_) CloseHandle(stop_);
}

void AutoUnlock::SetEvents(ICredentialProviderEvents* events, UINT_PTR context) {
    AcquireSRWLockExclusive(&lock_);
    if (events) events->AddRef();
    if (events_) events_->Release();
    events_ = events;
    context_ = context;
    ReleaseSRWLockExclusive(&lock_);
}

void AutoUnlock::ClearEvents() { SetEvents(nullptr, 0); }

void AutoUnlock::Start(const std::shared_ptr<AutoUnlock>& self) {
    AcquireSRWLockExclusive(&lock_);
    const bool go = !started_ && events_ != nullptr;
    if (go) started_ = true;
    ReleaseSRWLockExclusive(&lock_);
    Trace("auto: Start go=%d", go);
    if (!go) return;

    // Pin the DLL for the thread's lifetime; FreeLibraryAndExitThread drops it.
    HMODULE module = nullptr;
    if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS, reinterpret_cast<LPCWSTR>(&ThreadMain), &module)) {
        return;
    }
    auto* args = new (std::nothrow) ThreadArgs{self, module};
    if (!args) {
        FreeLibrary(module);
        return;
    }
    HANDLE t = CreateThread(nullptr, 0, &ThreadMain, args, 0, nullptr);
    if (!t) {
        Trace("auto: CreateThread failed %lu", GetLastError());
        delete args;
        FreeLibrary(module);
        return;
    }
    CloseHandle(t);
}

void AutoUnlock::Stop() { SetEvent(stop_); }

bool AutoUnlock::PeekVerified() const {
    ULONGLONG at = verifiedAt_.load();
    return at != 0 && GetTickCount64() - at <= kVerifiedTtlMs;
}

bool AutoUnlock::TakeVerified() {
    ULONGLONG at = verifiedAt_.exchange(0);
    return at != 0 && GetTickCount64() - at <= kVerifiedTtlMs;
}

void AutoUnlock::Notify() {
    AcquireSRWLockShared(&lock_);
    ICredentialProviderEvents* ev = events_;
    UINT_PTR ctx = context_;
    if (ev) ev->AddRef();
    ReleaseSRWLockShared(&lock_);
    // Called outside the lock: LogonUI may call back into the provider (UnAdvise) meanwhile.
    if (ev) {
        ev->CredentialsChanged(ctx);
        ev->Release();
    }
}

DWORD WINAPI AutoUnlock::ThreadMain(void* param) {
    auto* args = static_cast<ThreadArgs*>(param);
    HMODULE module = args->module;
    Trace("auto: thread start");
    try {
        args->self->Run();
    } catch (...) {
        // Never let anything escape into LogonUI.
        Trace("auto: exception");
    }
    Trace("auto: thread exit");
    delete args;  // drops our shared_ptr; may destroy the AutoUnlock
    FreeLibraryAndExitThread(module, 0);
}

void AutoUnlock::Run() {
    // Off unless the service says face unlock is usable right now, and never in
    // head-turn challenge mode (that needs the user to read an instruction first).
    Status st = Status::Error;
    Reason rs = Reason::Internal;
    uint8_t flags = 0;
    HRESULT shr = CallService(MsgType::Status, Direction::None, 3000, &st, &rs, &flags);
    Trace("auto: status hr=0x%08lx st=%d flags=%d", static_cast<unsigned long>(shr), static_cast<int>(st), flags);
    if (FAILED(shr) || st != Status::Ok || (flags & kFlagChallenge)) return;

    DisplayWatch ws;
    DisplayWindow window(&ws);  // best effort: without it, input still triggers
    AutoPolicy policy(GetTickCount64());
    DWORD lastInput = LastInputTick();
    Trace("auto: watching (lastInput=%lu now=%lu)", lastInput, GetTickCount());

    while (!policy.Done()) {
        DWORD w = MsgWaitForMultipleObjects(1, &stop_, FALSE, kPollMs, QS_ALLINPUT);
        if (w == WAIT_OBJECT_0) {
            Trace("auto: stopped");
            return;
        }
        MSG m;
        while (PeekMessageW(&m, nullptr, 0, 0, PM_REMOVE)) DispatchMessageW(&m);

        bool trigger = ws.woke;
        if (ws.woke) Trace("auto: display on");
        ws.woke = false;
        DWORD li = LastInputTick();
        if (li != lastInput) {
            lastInput = li;
            trigger = true;
        }
        if (!policy.ShouldAttempt(trigger, GetTickCount64())) continue;

        policy.OnStarted();
        Trace("auto: verify");
        st = Status::Error;
        rs = Reason::Internal;
        HRESULT hr = CallService(MsgType::Verify, Direction::None, kVerifyTimeoutMs, &st, &rs);
        Trace("auto: verify hr=0x%08lx st=%d reason=%d", static_cast<unsigned long>(hr), static_cast<int>(st),
              static_cast<int>(rs));
        if (WaitForSingleObject(stop_, 0) == WAIT_OBJECT_0) return;  // lock screen already gone
        AutoPolicy::Outcome out = AutoPolicy::Classify(hr, st, rs);
        // Input during the attempt (e.g. typing a PIN) is not a new trigger.
        lastInput = LastInputTick();
        ws.woke = false;
        policy.OnFinished(out, GetTickCount64());
        if (out == AutoPolicy::Outcome::Success) {
            verifiedAt_.store(GetTickCount64());
            Notify();
        }
    }
}

}  // namespace fu
