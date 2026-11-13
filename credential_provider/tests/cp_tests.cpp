// Minimal self-contained tests (no framework) for code that runs inside LogonUI.
#include <windows.h>
#define SECURITY_WIN32
#include <ntsecapi.h>

#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "auto_policy.h"
#include "cred_store.h"
#include "kerb_pack.h"
#include "pipe_client.h"
#include "protocol.h"
#include "scenario.h"

static int g_failed = 0;
#define CHECK(cond)                                                     \
    do {                                                                \
        if (!(cond)) {                                                  \
            std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
            ++g_failed;                                                 \
        }                                                               \
    } while (0)

using namespace fu;

static const uint8_t kNonce[kNonceLen] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16,
                                          17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32};

static std::vector<uint8_t> Resp(uint8_t type, uint8_t status, uint8_t reason, const uint8_t* nonce,
                                 uint8_t flags = 0) {
    std::vector<uint8_t> v = {'F', 'U', 'r', '1', type, status, reason, flags};
    v.insert(v.end(), nonce, nonce + kNonceLen);
    return v;
}

static void TestProtocol() {
    Request r = MakeRequest(MsgType::Verify, Direction::Right, kNonce);
    const uint8_t* b = reinterpret_cast<const uint8_t*>(&r);
    CHECK(std::memcmp(b, "FUq1", 4) == 0 && b[4] == 1 && b[5] == 2 && b[6] == 0 && b[7] == 0);
    CHECK(MakeRequest(MsgType::Status, Direction::Left, kNonce).direction == 0);

    Response out;
    auto ok = Resp(1, 0, 0, kNonce);
    CHECK(ParseResponse(ok.data(), ok.size(), MsgType::Verify, kNonce, &out));
    CHECK(!ParseResponse(ok.data(), ok.size() - 1, MsgType::Verify, kNonce, &out));
    CHECK(!ParseResponse(ok.data(), ok.size(), MsgType::Status, kNonce, &out));

    uint8_t other[kNonceLen];
    std::memcpy(other, kNonce, kNonceLen);
    other[31] ^= 1;
    CHECK(!ParseResponse(ok.data(), ok.size(), MsgType::Verify, other, &out));  // replayed/foreign response

    auto badStatus = Resp(1, 9, 0, kNonce);
    CHECK(!ParseResponse(badStatus.data(), badStatus.size(), MsgType::Verify, kNonce, &out));
    auto badReason = Resp(1, 1, 99, kNonce);
    CHECK(!ParseResponse(badReason.data(), badReason.size(), MsgType::Verify, kNonce, &out));

    auto challenge = Resp(3, 0, 0, kNonce, kFlagChallenge);
    CHECK(ParseResponse(challenge.data(), challenge.size(), MsgType::Status, kNonce, &out));
    CHECK((out.flags & kFlagChallenge) != 0);
    auto unknownFlag = Resp(3, 0, 0, kNonce, 0x80);
    CHECK(!ParseResponse(unknownFlag.data(), unknownFlag.size(), MsgType::Status, kNonce, &out));
}

static void Put(std::vector<uint8_t>* v, const std::wstring& s) {
    uint32_t n = static_cast<uint32_t>(s.size());
    v->insert(v->end(), reinterpret_cast<uint8_t*>(&n), reinterpret_cast<uint8_t*>(&n) + 4);
    v->insert(v->end(), reinterpret_cast<const uint8_t*>(s.data()),
              reinterpret_cast<const uint8_t*>(s.data()) + s.size() * 2);
}

static void TestCredentialParse() {
    std::vector<uint8_t> v = {'F', 'U', 'C', '1'};
    Put(&v, L"LAPTOP");
    Put(&v, L"jet");
    Put(&v, L"S-1-5-21-1-2-3-1001");
    Put(&v, L"pässwörd");
    StoredCredential c;
    CHECK(ParseCredential(v.data(), v.size(), &c));
    CHECK(c.domain == L"LAPTOP" && c.user == L"jet" && c.sid == L"S-1-5-21-1-2-3-1001");
    CHECK(std::wstring(c.password.c_str()) == L"pässwörd");

    StoredCredential c2;
    CHECK(!ParseCredential(v.data(), v.size() - 1, &c2));  // truncated
    auto trailing = v;
    trailing.push_back(0);
    CHECK(!ParseCredential(trailing.data(), trailing.size(), &c2));
    auto huge = v;
    huge[4] = 0xFF;
    huge[5] = 0xFF;  // absurd length must not read out of bounds
    CHECK(!ParseCredential(huge.data(), huge.size(), &c2));
    CHECK(!ParseCredential(v.data(), 3, &c2));
}

static void TestKerbPack() {
    BYTE* blob = nullptr;
    ULONG cb = 0;
    HRESULT hr = PackLogon(CPUS_UNLOCK_WORKSTATION, L"LAPTOP", L"jet", L"secret", &blob, &cb);
    CHECK(SUCCEEDED(hr));
    if (FAILED(hr)) return;
    auto* k = reinterpret_cast<KERB_INTERACTIVE_UNLOCK_LOGON*>(blob);
    CHECK(k->Logon.MessageType == KerbWorkstationUnlockLogon);
    auto str = [&](const UNICODE_STRING& us) {
        size_t off = reinterpret_cast<size_t>(us.Buffer);
        CHECK(off >= sizeof(KERB_INTERACTIVE_UNLOCK_LOGON) && off + us.Length <= cb);
        return std::wstring(reinterpret_cast<wchar_t*>(blob + off), us.Length / 2);
    };
    CHECK(str(k->Logon.LogonDomainName) == L"LAPTOP");
    CHECK(str(k->Logon.UserName) == L"jet");
    CHECK(str(k->Logon.Password) != L"secret");  // CredProtect'ed, not plaintext
    SecureZeroMemory(blob, cb);
    CoTaskMemFree(blob);
}

static void TestKerbPackScenario() {
    BYTE* blob = nullptr;
    ULONG cb = 0;
    CHECK(SUCCEEDED(PackLogon(CPUS_LOGON, L"LAPTOP", L"jet", L"secret", &blob, &cb)));
    if (blob) {
        CHECK(reinterpret_cast<KERB_INTERACTIVE_UNLOCK_LOGON*>(blob)->Logon.MessageType == KerbInteractiveLogon);
        SecureZeroMemory(blob, cb);
        CoTaskMemFree(blob);
        blob = nullptr;
    }
    CHECK(FAILED(PackLogon(CPUS_CREDUI, L"LAPTOP", L"jet", L"secret", &blob, &cb)));
    CHECK(blob == nullptr);
}

static void TestScenario() {
    const SessionState lockedUser{true, true, true};
    const SessionState unlockedUser{true, true, false};
    const SessionState noUser{true, false, true};  // boot / switch-user screen
    const SessionState unknown{};
    CHECK(ShouldParticipate(CPUS_LOGON, lockedUser));
    CHECK(!ShouldParticipate(CPUS_LOGON, unlockedUser));
    CHECK(!ShouldParticipate(CPUS_LOGON, noUser));
    CHECK(!ShouldParticipate(CPUS_LOGON, unknown));  // fail closed
    CHECK(ShouldParticipate(CPUS_UNLOCK_WORKSTATION, unknown));
    CHECK(!ShouldParticipate(CPUS_CREDUI, lockedUser));
    CHECK(!ShouldParticipate(CPUS_CHANGE_PASSWORD, lockedUser));
    // This test process runs in an unlocked session with a user: must not count as a lock screen.
    SessionState here = CurrentSessionState();
    CHECK(here.known && here.hasUser && !here.locked);
    CHECK(!ShouldParticipate(CPUS_LOGON, here));
}

static void TestAutoPolicy() {
    using O = AutoPolicy::Outcome;
    const ULONGLONG t0 = 1000000;
    {
        AutoPolicy p(t0);
        CHECK(!p.ShouldAttempt(true, t0 + 100));  // Win+L key-up: inside the grace period
        CHECK(!p.ShouldAttempt(false, t0 + 5000));  // no trigger, no camera
        CHECK(p.ShouldAttempt(true, t0 + AutoPolicy::kGraceMs));
    }
    {  // nobody there: retries, spaced out, capped
        AutoPolicy p(t0);
        ULONGLONG now = t0 + AutoPolicy::kGraceMs;
        for (int i = 0; i < AutoPolicy::kMaxAttempts; ++i) {
            CHECK(p.ShouldAttempt(true, now));
            p.OnStarted();
            p.OnFinished(O::KeepTrying, now + 1000);
            CHECK(!p.ShouldAttempt(true, now + 1000 + AutoPolicy::kCooldownMs - 1));
            now += 1000 + AutoPolicy::kCooldownMs;
        }
        CHECK(p.Done());
        CHECK(!p.ShouldAttempt(true, now + 60000));
    }
    {  // a counted failure (stranger, photo) ends automatic tries for this lock screen
        AutoPolicy p(t0);
        p.OnStarted();
        p.OnFinished(O::Stop, t0 + 3000);
        CHECK(p.Done());
        CHECK(!p.ShouldAttempt(true, t0 + 60000));
    }
    {  // success is final too
        AutoPolicy p(t0);
        p.OnStarted();
        p.OnFinished(O::Success, t0 + 3000);
        CHECK(p.Done());
    }
    CHECK(AutoPolicy::Classify(S_OK, Status::Ok, Reason::None) == O::Success);
    CHECK(AutoPolicy::Classify(S_OK, Status::Fail, Reason::NoFace) == O::KeepTrying);
    CHECK(AutoPolicy::Classify(S_OK, Status::Fail, Reason::NoMatch) == O::Stop);
    CHECK(AutoPolicy::Classify(S_OK, Status::Fail, Reason::Spoof) == O::Stop);
    CHECK(AutoPolicy::Classify(S_OK, Status::Fail, Reason::MultipleFaces) == O::Stop);
    CHECK(AutoPolicy::Classify(S_OK, Status::Fail, Reason::NotLocked) == O::Stop);
    CHECK(AutoPolicy::Classify(S_OK, Status::Disabled, Reason::RateLimited) == O::Stop);
    CHECK(AutoPolicy::Classify(S_OK, Status::Error, Reason::Internal) == O::Stop);
    CHECK(AutoPolicy::Classify(E_FAIL, Status::Ok, Reason::None) == O::Stop);  // no service: never "ok"
}

static void TestServerCheck() {
    // We're not SYSTEM, so our own process must never pass as the service.
    wchar_t self[MAX_PATH];
    GetModuleFileNameW(nullptr, self, MAX_PATH);
    CHECK(!IsTrustedServer(GetCurrentProcessId(), self));
    CHECK(!IsTrustedServer(GetCurrentProcessId(), L""));
}

static void TestNoServiceFailsClosed() {
    Status st;
    Reason rs;
    // With no service running (or a pipe we can't open), this must fail, not hang or succeed.
    CHECK(FAILED(CallService(MsgType::Status, Direction::None, 1000, &st, &rs)));
}

int main() {
    TestProtocol();
    TestCredentialParse();
    TestKerbPack();
    TestKerbPackScenario();
    TestScenario();
    TestAutoPolicy();
    TestServerCheck();
    TestNoServiceFailsClosed();
    std::printf(g_failed ? "%d FAILED\n" : "all passed\n", g_failed);
    return g_failed ? 1 : 0;
}
