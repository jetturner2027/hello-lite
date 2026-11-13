#include "credential.h"

#include <bcrypt.h>
#include <shlguid.h>
#include <shlwapi.h>

#include <cmath>
#include <new>

#include "cred_store.h"
#include "dll.h"
#include "guid.h"
#include "kerb_pack.h"
#include "pipe_client.h"
#include "trace.h"

namespace fu {

const CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR kFields[FI_COUNT] = {
    {FI_TILE_IMAGE, CPFT_TILE_IMAGE, const_cast<PWSTR>(L"Face Unlock"), CPFG_CREDENTIAL_PROVIDER_LOGO},
    {FI_LABEL, CPFT_LARGE_TEXT, const_cast<PWSTR>(L"Face Unlock"), CPFG_CREDENTIAL_PROVIDER_LABEL},
    {FI_STATUS, CPFT_SMALL_TEXT, const_cast<PWSTR>(L"Status"), GUID_NULL},
    {FI_SUBMIT, CPFT_SUBMIT_BUTTON, const_cast<PWSTR>(L"Unlock"), GUID_NULL},
};

namespace {

constexpr DWORD kVerifyTimeoutMs = 20000;  // service-side verify timeout is 8 s; leave margin for model load
constexpr NTSTATUS kStatusSuccess = 0;

const wchar_t* ReasonText(Status st, Reason r) {
    if (st == Status::Disabled) return L"Face unlock is off until you unlock once with your password.";
    switch (r) {
        case Reason::NoFace: return L"No face seen.";
        case Reason::MultipleFaces: return L"More than one face in view.";
        case Reason::NoMatch: return L"Face not recognized.";
        case Reason::Spoof: return L"Liveness check failed.";
        case Reason::ChallengeFailed: return L"Head turn not detected.";
        case Reason::Timeout: return L"Timed out.";
        case Reason::CameraError: return L"Camera unavailable.";
        case Reason::NotEnrolled: return L"No face enrolled.";
        case Reason::NotLocked: return L"Face unlock only works on a locked session.";
        default: return L"Face unlock failed.";
    }
}

// --- logo -----------------------------------------------------------------------
// Shapes are described by distance functions on a 128x128 canvas and rendered with
// 4x4 supersampling, so edges are anti-aliased without GDI+ or an image file.
constexpr int kLogoPx = 128;
constexpr float kStroke = 7.0f;  // line width in px

float Clamp(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }

// Distance from p to the segment a-b (gives a capsule when thresholded: round caps).
float SegDist(float px, float py, float ax, float ay, float bx, float by) {
    float vx = bx - ax, vy = by - ay, wx = px - ax, wy = py - ay;
    float t = Clamp((wx * vx + wy * vy) / (vx * vx + vy * vy), 0.0f, 1.0f);
    float dx = wx - t * vx, dy = wy - t * vy;
    return sqrtf(dx * dx + dy * dy);
}

// Signed distance to a rounded square centered on the canvas (half-size s, corner radius r).
float RoundSquareDist(float px, float py, float s, float r) {
    float qx = fabsf(px - 64.0f) - (s - r), qy = fabsf(py - 64.0f) - (s - r);
    float ox = qx > 0 ? qx : 0, oy = qy > 0 ? qy : 0;
    float inside = qx > qy ? qx : qy;
    return sqrtf(ox * ox + oy * oy) + (inside < 0 ? inside : 0) - r;
}

bool LogoHit(float x, float y) {
    const float half = kStroke / 2;
    // Viewfinder: only the corners of a rounded square outline.
    float dx = fabsf(x - 64.0f), dy = fabsf(y - 64.0f);
    if (dx > 26.0f && dy > 26.0f && fabsf(RoundSquareDist(x, y, 50.0f, 18.0f)) <= half) return true;
    // Eyes: two short vertical capsules.
    if (SegDist(x, y, 49, 50, 49, 59) <= half || SegDist(x, y, 79, 50, 79, 59) <= half) return true;
    // Smile: the lower part of a circle, with rounded ends.
    const float cx = 64, cy = 64, r = 19;
    float vx = x - cx, vy = y - cy;
    float ang = atan2f(vy, vx);  // y grows downward, so the bottom is +pi/2
    const float a0 = 0.62f, a1 = 3.14159265f - 0.62f;
    if (ang >= a0 && ang <= a1 && fabsf(sqrtf(vx * vx + vy * vy) - r) <= half) return true;
    for (float a : {a0, a1}) {
        float ex = cx + r * cosf(a), ey = cy + r * sinf(a);
        if ((x - ex) * (x - ex) + (y - ey) * (y - ey) <= half * half) return true;
    }
    return false;
}

void DrawLogo(DWORD* px) {
    for (int y = 0; y < kLogoPx; ++y) {
        for (int x = 0; x < kLogoPx; ++x) {
            int hits = 0;
            for (int sy = 0; sy < 4; ++sy) {
                for (int sx = 0; sx < 4; ++sx) {
                    hits += LogoHit(x + (sx + 0.5f) / 4, y + (sy + 0.5f) / 4);
                }
            }
            // White glyph blended onto an opaque background: LogonUI ignores the
            // alpha channel (transparent pixels showed up black), so no transparency.
            const DWORD bg[3] = {0xC0, 0x67, 0x00};  // B, G, R of Windows blue #0067C0
            DWORD c[3];
            for (int i = 0; i < 3; ++i) c[i] = (bg[i] * (16 - hits) + 255 * hits) / 16;
            px[y * kLogoPx + x] = 0xFF000000 | (c[2] << 16) | (c[1] << 8) | c[0];
        }
    }
}

}  // namespace

Credential::Credential(std::wstring sid, CREDENTIAL_PROVIDER_USAGE_SCENARIO cpus, std::shared_ptr<AutoUnlock> autoUnlock)
    : sid_(std::move(sid)), cpus_(cpus), auto_(std::move(autoUnlock)) {
    DllAddRef();
    NewChallenge();
}

Credential::~Credential() {
    if (events_) events_->Release();
    DllRelease();
}

IFACEMETHODIMP_(ULONG) Credential::AddRef() { return InterlockedIncrement(&refs_); }

IFACEMETHODIMP_(ULONG) Credential::Release() {
    LONG r = InterlockedDecrement(&refs_);
    if (r == 0) delete this;
    return r;
}

IFACEMETHODIMP Credential::QueryInterface(REFIID riid, void** ppv) {
    static const QITAB qit[] = {
        QITABENT(Credential, ICredentialProviderCredential),
        QITABENT(Credential, ICredentialProviderCredential2),
        {},
    };
    return QISearch(this, qit, riid, ppv);
}

void Credential::NewChallenge() {
    BYTE b = 0;
    BCryptGenRandom(nullptr, &b, 1, BCRYPT_USE_SYSTEM_PREFERRED_RNG);
    direction_ = (b & 1) ? Direction::Left : Direction::Right;
}

void Credential::SetStatus(const std::wstring& text) {
    status_ = text;
    if (events_) events_->SetFieldString(this, FI_STATUS, status_.c_str());
}

std::wstring Credential::Instructions() const {
    if (!challenge_) return L"Click the arrow and look at the camera.";
    return std::wstring(L"Click the arrow, look at the camera, then turn your head to your ") +
           (direction_ == Direction::Left ? L"LEFT" : L"RIGHT") + L" and back.";
}

IFACEMETHODIMP Credential::Advise(ICredentialProviderCredentialEvents* events) {
    if (events_) events_->Release();
    events_ = events;
    if (events_) events_->AddRef();
    return S_OK;
}

IFACEMETHODIMP Credential::UnAdvise() {
    if (events_) events_->Release();
    events_ = nullptr;
    return S_OK;
}

IFACEMETHODIMP Credential::SetSelected(BOOL* autoLogon) {
    *autoLogon = FALSE;  // selecting the tile never submits by itself; auto-logon only follows a
                         // successful background verify (see AutoUnlock / GetCredentialCount)
    NewChallenge();
    // Ask whether a head turn is required, so the prompt matches what the service will
    // check. STATUS never touches the camera and returns in milliseconds. If the service
    // is unreachable we keep the last known setting; GetSerialization reports the error.
    Status st;
    Reason rs;
    uint8_t flags = 0;
    if (SUCCEEDED(CallService(MsgType::Status, Direction::None, 1500, &st, &rs, &flags))) {
        challenge_ = (flags & kFlagChallenge) != 0;
    }
    try {
        SetStatus(Instructions());
    } catch (const std::bad_alloc&) {
        return E_OUTOFMEMORY;
    }
    return S_OK;
}

IFACEMETHODIMP Credential::SetDeselected() { return S_OK; }

IFACEMETHODIMP Credential::GetFieldState(DWORD id, CREDENTIAL_PROVIDER_FIELD_STATE* state,
                                         CREDENTIAL_PROVIDER_FIELD_INTERACTIVE_STATE* istate) {
    if (id >= FI_COUNT) return E_INVALIDARG;
    *state = (id == FI_TILE_IMAGE || id == FI_LABEL) ? CPFS_DISPLAY_IN_BOTH : CPFS_DISPLAY_IN_SELECTED_TILE;
    *istate = CPFIS_NONE;
    return S_OK;
}

IFACEMETHODIMP Credential::GetStringValue(DWORD id, PWSTR* value) {
    *value = nullptr;
    switch (id) {
        case FI_LABEL: return SHStrDupW(L"Face Unlock", value);
        case FI_STATUS:
            try {
                return SHStrDupW(status_.empty() ? Instructions().c_str() : status_.c_str(), value);
            } catch (const std::bad_alloc&) {
                return E_OUTOFMEMORY;
            }
        default: return E_INVALIDARG;
    }
}

IFACEMETHODIMP Credential::GetBitmapValue(DWORD id, HBITMAP* bmp) {
    *bmp = nullptr;
    if (id != FI_TILE_IMAGE) return E_INVALIDARG;
    // Drawn in code (no resource file): a white face glyph on Windows blue.
    // LogonUI takes ownership of the bitmap.
    BITMAPINFO bi{};
    bi.bmiHeader = {sizeof(BITMAPINFOHEADER), kLogoPx, -kLogoPx, 1, 32, BI_RGB};
    void* bits = nullptr;
    HBITMAP h = CreateDIBSection(nullptr, &bi, DIB_RGB_COLORS, &bits, nullptr, 0);
    if (!h) return E_OUTOFMEMORY;
    DrawLogo(static_cast<DWORD*>(bits));
    *bmp = h;
    return S_OK;
}

IFACEMETHODIMP Credential::GetSubmitButtonValue(DWORD id, DWORD* adjacentTo) {
    if (id != FI_SUBMIT) return E_INVALIDARG;
    *adjacentTo = FI_STATUS;
    return S_OK;
}

IFACEMETHODIMP Credential::GetSerialization(CREDENTIAL_PROVIDER_GET_SERIALIZATION_RESPONSE* response,
                                            CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION* serialization,
                                            PWSTR* optionalStatusText,
                                            CREDENTIAL_PROVIDER_STATUS_ICON* optionalIcon) {
    *response = CPGSR_NO_CREDENTIAL_NOT_FINISHED;
    *optionalStatusText = nullptr;
    *optionalIcon = CPSI_NONE;
    ZeroMemory(serialization, sizeof(*serialization));
    submitted_ = false;
    try {
        Status st = Status::Error;
        Reason rs = Reason::Internal;
        uint8_t flags = 0;
        HRESULT hr = S_OK;
        if (auto_ && auto_->TakeVerified()) {
            // The background check just passed (seconds ago, same lock screen); this
            // call is LogonUI acting on our auto-logon request. Don't run the camera again.
            st = Status::Ok;
            Trace("GetSerialization: using background verify");
        } else {
            Trace("GetSerialization: verify on click");
            hr = CallService(MsgType::Verify, challenge_ ? direction_ : Direction::None, kVerifyTimeoutMs, &st, &rs,
                             &flags);
            if (SUCCEEDED(hr)) challenge_ = (flags & kFlagChallenge) != 0;  // keep the prompt in sync
        }
        NewChallenge();  // never reuse a direction after an attempt
        if (FAILED(hr)) {
            SetStatus(L"Face Unlock service unavailable. Use your password.");
            return S_OK;
        }
        if (st != Status::Ok) {
            SetStatus(std::wstring(ReasonText(st, rs)) +
                      (st == Status::Disabled ? L"" : L" " + Instructions()));
            return S_OK;
        }

        StoredCredential cred;
        if (FAILED(LoadCredential(&cred)) || CompareStringOrdinal(cred.sid.c_str(), -1, sid_.c_str(), -1, TRUE) != CSTR_EQUAL) {
            SetStatus(L"No stored password for this account. Run tools\\set_password.py.");
            return S_OK;
        }
        ULONG package = 0;
        hr = NegotiatePackage(&package);
        if (SUCCEEDED(hr)) {
            hr = PackLogon(cpus_, cred.domain.c_str(), cred.user.c_str(), cred.password.c_str(),
                           &serialization->rgbSerialization, &serialization->cbSerialization);
        }
        cred.password.Wipe();
        if (FAILED(hr)) {
            SetStatus(L"Face unlock failed (internal error). Use your password.");
            return S_OK;
        }
        serialization->ulAuthenticationPackage = package;
        serialization->clsidCredentialProvider = CLSID_FaceUnlockProvider;
        *response = CPGSR_RETURN_CREDENTIAL_FINISHED;
        submitted_ = true;
        return S_OK;
    } catch (const std::bad_alloc&) {
        return E_OUTOFMEMORY;
    }
}

IFACEMETHODIMP Credential::ReportResult(NTSTATUS status, NTSTATUS, PWSTR* optionalStatusText,
                                        CREDENTIAL_PROVIDER_STATUS_ICON* optionalIcon) {
    *optionalStatusText = nullptr;
    *optionalIcon = CPSI_NONE;
    if (submitted_ && status != kStatusSuccess) {
        // Most likely the Windows password changed. Tell the service to switch face
        // unlock off so we don't keep submitting a bad password toward a lockout.
        Status st;
        Reason rs;
        CallService(MsgType::ReportLogonFailed, Direction::None, 3000, &st, &rs);
        try {
            SetStatus(L"Windows rejected the stored password. Unlock with your password, then re-run set_password.");
        } catch (const std::bad_alloc&) {
        }
    }
    submitted_ = false;
    return S_OK;
}

IFACEMETHODIMP Credential::GetUserSid(PWSTR* sid) { return SHStrDupW(sid_.c_str(), sid); }

}  // namespace fu
