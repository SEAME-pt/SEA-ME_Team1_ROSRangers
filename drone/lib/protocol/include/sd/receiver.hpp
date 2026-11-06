#pragma once

#include <cstddef>
#include <cstdint>

#include "sd/frame.hpp"
#include "sd/hmac.hpp"
#include "sd/messages.hpp"
#include "sd/seq_window.hpp"

namespace sd {

// Result of Receiver::receive. Only kAccepted and kAcceptedGap may be used.
// Values match the Python twin's RxResult (receiver.py). New values are only
// ever appended, never inserted or renumbered, so a number in a log or a
// message means the same reason on both sides.
enum class RxResult : std::uint8_t {
  kAccepted = 0,
  kAcceptedGap = 1, // accepted; some seq numbers were skipped (counted, not an
                    // error)
  kNoSession = 2,   // no session is open
  kTruncated = 3,
  kBadMagic = 4,
  kBadVersion = 5,
  kBadLength = 6,
  kBadTag = 7,
  kWrongSession = 8, // session_id is not the current session (spec §4 rule 2)
  kDuplicateSeq = 9,
  kOldSeq = 10,
  kSeqTooHigh = 11,     // seq >= 2^31: the sender must open a new session
  kBadType = 12,        // not a spec §3 message type
  kBadPayloadSize = 13, // payload_len is not the spec §3 size of the type
  kBadMode = 14,        // SETPOINT.mode is not 0 or 1
  kWrongTransport = 15, // a TCP type on the UDP Receiver or the other way (§2)
  kWrongDirection = 16, // a type this side never receives: its own, sent back
                        // (§2)
};

inline bool is_accepted(RxResult r) {
  return r == RxResult::kAccepted || r == RxResult::kAcceptedGap;
}

struct RxCounters {
  std::uint32_t accepted;
  std::uint32_t rejected; // every reject below; reported as STATE.rx_rejected
  std::uint32_t no_session;
  std::uint32_t truncated;
  std::uint32_t bad_magic;
  std::uint32_t bad_version;
  std::uint32_t bad_length;
  std::uint32_t bad_tag;
  std::uint32_t wrong_session;
  std::uint32_t bad_type;
  std::uint32_t wrong_transport;
  std::uint32_t wrong_direction;
  std::uint32_t bad_payload_size;
  std::uint32_t bad_mode;
  std::uint32_t duplicate_seq;
  std::uint32_t old_seq;
  std::uint32_t seq_too_high;
};

// Receive rules of spec §4 for one side (ESP or ground) and one transport
// inside one session, in this order: frame checks (rule 1), session check
// (rule 2), message type, transport (§2), direction (§2: only the types the
// other side sends), payload size and SETPOINT.mode (REQ-COM-001/002), then the
// sequence window (rule 3, ADR-003). Everything before the window is checked
// first, so a malformed frame does not use up a seq number. Use one Receiver
// per transport (UDP control, TCP session): `seq` is counted per transport.
//
// Rejected frames are dropped and counted (REQ-COM-004); the caller must not
// reset the link-loss timer for them (REQ-SAF-011).
class Receiver {
public:
  // The role and the transport are fixed for the Receiver's lifetime (#152).
  Receiver(Role role, Transport transport);
  ~Receiver(); // wipes the key
  Receiver(const Receiver &) = delete;
  Receiver &operator=(const Receiver &) = delete;

  // Opens a session. `rx_key` is this side's receiving direction key from
  // derive_keys(): ground_to_esp on the ESP, esp_to_ground on the ground. The
  // window starts at 1 on TCP (HELLO and HELLO_ACK used seq 1, spec §1) and at
  // 0 on UDP. Returns false for session_id 0 (reserved for HELLO); then nothing
  // changes, and a session that was open stays open. The window restarts here,
  // so the caller (#102) must never open the same (session_id, key) twice.
  [[nodiscard]] bool start_session(std::uint32_t session_id, const Key &rx_key);
  void end_session();

  // `out` is written only when the result is accepted (is_accepted()).
  RxResult receive(const std::uint8_t *buf, std::size_t len, Frame &out);

  Role role() const { return role_; }
  Transport transport() const { return transport_; }
  bool in_session() const { return in_session_; }
  std::uint32_t session_id() const { return session_id_; }
  std::uint32_t last_seq() const { return window_.last(); }
  // counters() last for the Receiver's lifetime; seq_counters() restart with
  // each session. How they are reported to the ground is open (#149).
  const RxCounters &counters() const { return counters_; }
  const SeqCounters &seq_counters() const { return window_.counters(); }

private:
  RxResult reject(RxResult r);

  Role role_;
  Transport transport_;
  Key key_ = {};
  std::uint32_t session_id_ = 0;
  bool in_session_ = false;
  SeqWindow window_;
  RxCounters counters_ = {};
};

// The handshake (spec §1, #152): the ESP accepts only HELLO and the ground only
// HELLO_ACK, both tagged with the PSK, so a reflected or out-of-place handshake
// frame does nothing. The ESP sends every HELLO (type 0x01) here, also during a
// session (a new HELLO opens a pending session, #156); a Receiver never
// accepts HELLO. Only frames from the TCP connection come here (HELLO and
// HELLO_ACK are TCP types).
// Checked in this order: tag, type, payload size, session_id, seq.
enum class HandshakeResult : std::uint8_t {
  kAccepted = 0,
  kTruncated,
  kBadMagic,
  kBadVersion,
  kBadLength,
  kBadTag,         // not tagged with the PSK
  kWrongDirection, // not the one handshake type this side receives
  kBadPayloadSize, // HELLO is 17 bytes, HELLO_ACK 32
  kWrongSession,   // HELLO: session_id not 0; HELLO_ACK: 0, or header and
                   // payload session_id differ
  kBadSeq,         // seq is not 1
};

// Checks one handshake frame for `role`: the ESP gets HELLO, the ground gets
// HELLO_ACK. `out` is written only when the result is kAccepted. Not counted:
// the caller (#102) counts or rate-limits handshake attempts.
HandshakeResult receive_handshake(Role role, const std::uint8_t *buf,
                                  std::size_t len, const Key &psk, Frame &out);

} // namespace sd
