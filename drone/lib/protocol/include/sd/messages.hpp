#pragma once

// Payload codecs for the spec §3 messages other than SETPOINT (setpoint.hpp).
// Same contract as encode_setpoint/decode_setpoint: encode returns the payload
// size (0 if `out` is null or out_cap is too small), decode checks the size
// only, values are not range-checked. check_payload() is the receive check that
// Receiver runs on every frame.

#include <cstddef>
#include <cstdint>

#include "sd/hmac.hpp" // kNonceSize

namespace sd {

// --- Code values, spec §3.1 (decided on #142, 2026-11-05). Values are part of
// the wire format: never renumber or reuse. A code no longer used is retired.

// ARM_CONTROL_ACK.result and RESUME_ACK.result.
enum class AckResult : std::uint8_t {
  kOk = 0,
  kWrongState = 1,
  kMaskMismatch = 2,
  kResumeConditionNotMet = 3,
};

enum class ByeReason : std::uint8_t {
  kNormal = 0,
  kTimeout = 1,
  kProtocolError = 2,
  kReplaced = 3, // replaced by a new session
};

// CONFIG_ACK.result. A CONFIG_SET is checked in the order kControlEnabled, then
// 1, 2, 3, 4; the first refusal that applies is returned (spec §3.1). The
// check itself is the link state machine's job (#102).
enum class ConfigResult : std::uint8_t {
  kOk = 0,
  kUnknownKey = 1,
  kReadOnly = 2,        // Info key (0x01-0x3F)
  kSafetyParameter = 3, // Safety key (0x40-0x7F): firmware only
  kOutOfRange = 4,      // never clamped (spec §7.3 rule 4)
  kControlEnabled = 5,  // refused while STREAMING (OD-16)
};

// STATE.esp_state. A stale setpoint is an event, not a state.
enum class EspState : std::uint8_t {
  kBoot = 0,
  kNoSession = 1,
  kSessionIdle = 2,
  kStreaming = 3,
  kLinkFailsafe = 4,
};

// EVENT.code, grouped by range: 0x00xx system, 0x01xx link, 0x02xx FC / MSP.
enum class EventCode : std::uint16_t {
  kSetpointStale = 0x0101,
  kLinkFailsafeEntered = 0x0102,
  kLinkResumed = 0x0103,
};

// --- Payloads.

constexpr std::size_t kFwVersionSize = 12;
constexpr std::size_t kEventTextMax = 64;

// HELLO (0x01).
struct Hello {
  std::uint8_t proto;
  std::uint8_t ground_nonce[kNonceSize];
};
constexpr std::size_t kHelloSize = 1 + kNonceSize;

// HELLO_ACK (0x02).
struct HelloAck {
  std::uint32_t session_id;
  std::uint8_t esp_nonce[kNonceSize];
  char fw_version[kFwVersionSize]; // raw bytes, not NUL-terminated
};
constexpr std::size_t kHelloAckSize = 4 + kNonceSize + kFwVersionSize;

// CONFIG_GET (0x20) and CONFIG_SET (0x21): same payload.
struct ConfigKv {
  std::uint8_t key;
  std::int32_t value;
};
constexpr std::size_t kConfigKvSize = 5;

// CONFIG_ACK (0x22), reply to CONFIG_GET and CONFIG_SET.
struct ConfigAck {
  std::uint8_t key;
  std::uint8_t result; // ConfigResult
  std::int32_t value;
};
constexpr std::size_t kConfigAckSize = 6;

// EVENT (0x40). text_len = payload_len - 2, up to kEventTextMax. Not
// NUL-terminated.
struct Event {
  std::uint16_t code; // EventCode
  std::uint8_t text_len;
  char text[kEventTextMax];
};
constexpr std::size_t kEventMinSize = 2;
constexpr std::size_t kEventMaxSize = 2 + kEventTextMax;

// STATE (0x80).
struct State {
  std::uint8_t esp_state; // EspState
  std::uint8_t override_active;
  std::uint32_t last_ctrl_seq;
  std::uint32_t last_ctrl_t_send_us;
  std::int16_t roll;     // 0.1 deg
  std::int16_t pitch;    // 0.1 deg
  std::uint16_t heading; // deg
  std::uint16_t vbat;    // 0.01 V
  std::int16_t flow_x;
  std::int16_t flow_y;
  std::uint8_t flow_quality;
  std::uint8_t flow_status; // ThoneFlow §8.1
  std::uint16_t rx_rejected;
  std::uint16_t msp_errors;
};
constexpr std::size_t kStateSize = 28;

std::size_t encode_hello(const Hello &m, std::uint8_t *out,
                         std::size_t out_cap);
bool decode_hello(const std::uint8_t *payload, std::size_t len, Hello &out);

std::size_t encode_hello_ack(const HelloAck &m, std::uint8_t *out,
                             std::size_t out_cap);
bool decode_hello_ack(const std::uint8_t *payload, std::size_t len,
                      HelloAck &out);

// One-byte payloads: ARM_CONTROL_REQUEST.mask, ARM_CONTROL_ACK.result,
// RESUME_ACK.result, BYE.reason. RESUME_REQUEST has an empty payload
// (payload_len 0), no codec needed.
std::size_t encode_u8_payload(std::uint8_t v, std::uint8_t *out,
                              std::size_t out_cap);
bool decode_u8_payload(const std::uint8_t *payload, std::size_t len,
                       std::uint8_t &out);

std::size_t encode_config_kv(const ConfigKv &m, std::uint8_t *out,
                             std::size_t out_cap);
bool decode_config_kv(const std::uint8_t *payload, std::size_t len,
                      ConfigKv &out);

std::size_t encode_config_ack(const ConfigAck &m, std::uint8_t *out,
                              std::size_t out_cap);
bool decode_config_ack(const std::uint8_t *payload, std::size_t len,
                       ConfigAck &out);

// Returns 0 also if m.text_len > kEventTextMax.
std::size_t encode_event(const Event &m, std::uint8_t *out,
                         std::size_t out_cap);
bool decode_event(const std::uint8_t *payload, std::size_t len, Event &out);

std::size_t encode_state(const State &m, std::uint8_t *out,
                         std::size_t out_cap);
bool decode_state(const std::uint8_t *payload, std::size_t len, State &out);

// --- Receive check: spec §3 types and sizes, and SETPOINT.mode (REQ-COM-001,
// REQ-COM-002). Receiver runs it on a frame whose header, tag and session are
// good, before the sequence window, so a malformed frame changes no state
// (REQ-COM-004).
enum class PayloadCheck : std::uint8_t {
  kOk = 0,
  kUnknownType, // not a spec §3 type
  kBadSize,     // payload_len is not the §3 size of the type (EVENT: 2..66)
  kBadMode,     // SETPOINT.mode is not 0 (neutral) or 1 (attitude)
};

PayloadCheck check_payload(std::uint8_t type, const std::uint8_t *payload,
                           std::size_t len);

// Spec §2: 0x01-0x5F travel on TCP, 0x60-0x9F on UDP.
enum class Transport : std::uint8_t { kUdp = 0, kTcp = 1 };

// The transport a type belongs to. False for a type outside both ranges.
bool transport_of(std::uint8_t type, Transport &out);

// Who owns a Receiver (#152). Each side accepts only the types the other side
// sends (spec §2), so a frame a node sent cannot be fed back to it, whatever
// the key.
enum class Role : std::uint8_t { kEsp = 0, kGround = 1 };

// In-session types that `role` receives (spec §2):
//   ESP:    SETPOINT; ARM_CONTROL_REQUEST, RESUME_REQUEST, BYE, CONFIG_GET,
//           CONFIG_SET
//   ground: STATE; ARM_CONTROL_ACK, RESUME_ACK, BYE, CONFIG_ACK, EVENT
// HELLO and HELLO_ACK come before a session: see receive_handshake().
bool receives(Role role, std::uint8_t type);

} // namespace sd
