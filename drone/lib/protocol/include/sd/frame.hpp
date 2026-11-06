#pragma once

#include <cstddef>
#include <cstdint>

#include "sd/hmac.hpp"

namespace sd {

// Protocol Spec v0 §1: 20-byte header + payload + 8-byte tag. Little-endian.
constexpr std::uint8_t kMagic0 = 0x53; // 'S'
constexpr std::uint8_t kMagic1 = 0x44; // 'D'
constexpr std::uint8_t kWireVersion = 1;
constexpr std::size_t kHeaderSize = 20;
constexpr std::size_t kTagSize = 8;
constexpr std::size_t kMaxPayload = 256;
constexpr std::size_t kMaxFrame = kHeaderSize + kMaxPayload + kTagSize;

// Spec §3. Values are part of the wire format: never renumber.
enum class MsgType : std::uint8_t {
  kHello = 0x01,
  kHelloAck = 0x02,
  kArmControlRequest = 0x03,
  kArmControlAck = 0x04,
  kResumeRequest = 0x05,
  kResumeAck = 0x06,
  kBye = 0x07,
  kConfigGet = 0x20,
  kConfigSet = 0x21,
  kConfigAck = 0x22,
  kEvent = 0x40,
  kSetpoint = 0x60,
  kState = 0x80,
};

struct Header {
  std::uint8_t type;
  std::uint8_t flags;
  std::uint16_t payload_len;
  std::uint32_t session_id;
  std::uint32_t seq;
  std::uint32_t t_send_us;
};

// Why a frame was rejected. Every reject is dropped and counted (REQ-COM-004).
enum class RxError : std::uint8_t {
  kOk = 0,
  kTruncated,  // shorter than header + tag, or than payload_len says
  kBadMagic,   // not "SD"
  kBadVersion, // version byte is not kWireVersion
  kBadLength,  // payload_len > kMaxPayload, or bytes after the frame
  kBadTag,     // HMAC tag does not match
};

// A decoded frame. `payload` points into the buffer passed to decode_frame.
struct Frame {
  Header header;
  const std::uint8_t *payload;
};

// Builds header + payload + tag into `out`. header.payload_len is set from
// `payload_len`. Returns the frame size, or 0 if payload_len > kMaxPayload,
// `out_cap` is too small, or a pointer is null (payload may be null only when
// payload_len is 0).
std::size_t encode_frame(const Header &header, const std::uint8_t *payload,
                         std::size_t payload_len, const Key &key,
                         std::uint8_t *out, std::size_t out_cap);

// Checks one complete frame (a UDP datagram, or a TCP frame cut with
// frame_size()). Order of checks follows spec §4 rule 1: minimum length,
// magic, version, then payload_len and the exact length, then the tag. Session
// and sequence checks are the caller's (§4 rules 2 and 3).
RxError decode_frame(const std::uint8_t *buf, std::size_t len, const Key &key,
                     Frame &out);

// TCP framing (OD-03): given at least kHeaderSize bytes, the size of the whole
// frame. Returns 0 if fewer than kHeaderSize bytes are given (wait for more)
// or payload_len > kMaxPayload. A 0 with len >= kHeaderSize is a bad length:
// close the TCP connection (spec §4 rule 5), never wait for more bytes. Magic
// and version are not checked here; decode_frame() does that.
std::size_t frame_size(const std::uint8_t *buf, std::size_t len);

} // namespace sd
