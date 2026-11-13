#pragma once
// Mirror of face_unlock/protocol.py. Fixed 40-byte messages; see that file for rationale.
#include <cstddef>
#include <cstdint>
#include <cstring>

namespace fu {

constexpr wchar_t kPipeName[] = L"\\\\.\\pipe\\FaceUnlock";
constexpr size_t kMsgLen = 40;
constexpr size_t kNonceLen = 32;

enum class MsgType : uint8_t { Verify = 1, ReportLogonFailed = 2, Status = 3 };
enum class Status : uint8_t { Ok = 0, Fail = 1, Disabled = 2, Error = 3 };
enum class Direction : uint8_t { None = 0, Left = 1, Right = 2 };
enum class Reason : uint8_t {
    None = 0, NoFace = 1, MultipleFaces = 2, NoMatch = 3, Spoof = 4, ChallengeFailed = 5, Timeout = 6,
    CameraError = 7, NotEnrolled = 8, RateLimited = 9, BadRequest = 10, Internal = 11, NotLocked = 12,
};
constexpr uint8_t kMaxStatus = 3;
constexpr uint8_t kMaxReason = 12;
constexpr uint8_t kFlagChallenge = 0x01;  // service requires a head-turn direction in Verify

#pragma pack(push, 1)
struct Request {
    char magic[4];
    uint8_t type;
    uint8_t direction;
    uint16_t reserved;
    uint8_t nonce[kNonceLen];
};
struct Response {
    char magic[4];
    uint8_t type;
    uint8_t status;
    uint8_t reason;
    uint8_t flags;
    uint8_t nonce[kNonceLen];
};
#pragma pack(pop)
static_assert(sizeof(Request) == kMsgLen && sizeof(Response) == kMsgLen, "wire size");

inline Request MakeRequest(MsgType type, Direction dir, const uint8_t (&nonce)[kNonceLen]) {
    Request r{};
    std::memcpy(r.magic, "FUq1", 4);
    r.type = static_cast<uint8_t>(type);
    r.direction = static_cast<uint8_t>(type == MsgType::Verify ? dir : Direction::None);
    std::memcpy(r.nonce, nonce, kNonceLen);
    return r;
}

// Accepts only an exact-length response for our nonce and request type, with known enum values.
inline bool ParseResponse(const uint8_t* data, size_t len, MsgType expected, const uint8_t (&nonce)[kNonceLen],
                          Response* out) {
    if (len != kMsgLen) return false;
    Response r;
    std::memcpy(&r, data, kMsgLen);
    if (std::memcmp(r.magic, "FUr1", 4) != 0 || (r.flags & ~kFlagChallenge) != 0) return false;
    if (r.type != static_cast<uint8_t>(expected)) return false;
    if (r.status > kMaxStatus || r.reason > kMaxReason) return false;
    // Not constant-time; it doesn't need to be. The nonce isn't secret, only fresh.
    if (std::memcmp(r.nonce, nonce, kNonceLen) != 0) return false;
    *out = r;
    return true;
}

}  // namespace fu
