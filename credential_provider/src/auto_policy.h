#pragma once
#include <windows.h>

#include "protocol.h"

namespace fu {

// When the background ("look and go") attempt may run. Pure logic, no Win32
// calls, so it is unit-tested in cp_tests.
//
// A trigger is new keyboard/mouse input or the display turning on. We ignore
// triggers for a short grace period after the lock screen appears (so the
// key-up of Win+L doesn't unlock you on the spot), space attempts apart, cap
// them per lock screen, and stop for good after anything except "no face":
// a stranger or a photo gets one automatic try per lock, not an endless stream.
class AutoPolicy {
public:
    static constexpr ULONGLONG kGraceMs = 2000;
    static constexpr ULONGLONG kCooldownMs = 3000;
    static constexpr int kMaxAttempts = 4;

    enum class Outcome { Success, KeepTrying, Stop };

    explicit AutoPolicy(ULONGLONG startMs) : start_(startMs) {}

    bool ShouldAttempt(bool trigger, ULONGLONG nowMs) const {
        if (done_ || !trigger || attempts_ >= kMaxAttempts) return false;
        if (nowMs - start_ < kGraceMs) return false;
        return attempts_ == 0 || nowMs - lastEnd_ >= kCooldownMs;
    }
    void OnStarted() { ++attempts_; }
    void OnFinished(Outcome o, ULONGLONG nowMs) {
        lastEnd_ = nowMs;
        if (o != Outcome::KeepTrying || attempts_ >= kMaxAttempts) done_ = true;
    }
    bool Done() const { return done_; }

    // Only "nobody in front of the camera" is worth another automatic try; it
    // also isn't counted as a failure by the service. Everything else stops.
    static Outcome Classify(HRESULT hr, Status st, Reason rs) {
        if (FAILED(hr)) return Outcome::Stop;
        if (st == Status::Ok) return Outcome::Success;
        if (st == Status::Fail && rs == Reason::NoFace) return Outcome::KeepTrying;
        return Outcome::Stop;
    }

private:
    ULONGLONG start_;
    ULONGLONG lastEnd_ = 0;
    int attempts_ = 0;
    bool done_ = false;
};

}  // namespace fu
