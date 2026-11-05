#pragma once

#include <cstddef>
#include <cstdint>

#include "sd/frame.hpp"
#include "sd/hmac.hpp"
#include "sd/seq_window.hpp"

namespace sd {

// Result of Receiver::receive. Only kAccepted and kAcceptedGap may be used.
enum class RxResult : std::uint8_t {
  kAccepted = 0,
  kAcceptedGap, // accepted; some seq numbers were skipped (counted, not an
                // error)
  kNoSession,   // no session is open
  kTruncated,
  kBadMagic,
  kBadVersion,
  kBadLength,
  kBadTag,
  kWrongSession,   // session_id is not the current session (spec §4 rule 2)
  kBadType,        // not a spec §3 message type
  kBadPayloadSize, // payload_len is not the spec §3 size of the type
  kBadMode,        // SETPOINT.mode is not 0 or 1
  kDuplicateSeq,
  kOldSeq,
  kSeqTooHigh, // seq >= 2^31: the sender must open a new session
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
  std::uint32_t bad_payload_size;
  std::uint32_t bad_mode;
  std::uint32_t duplicate_seq;
  std::uint32_t old_seq;
  std::uint32_t seq_too_high;
};

// Receive rules of spec §4 for one transport inside one session, in this order:
// frame checks (rule 1), session check (rule 2), message type, payload size and
// SETPOINT.mode (check_payload(), REQ-COM-001/002), then the sequence window
// (rule 3, ADR-003). The payload check runs before the window, so a malformed
// frame does not use up a seq number. Use one Receiver
// per transport (UDP control, TCP session): `seq` is counted per transport
// (spec §4 rule 3).
//
// HELLO is not handled here: it arrives before a session exists and is tagged
// with the PSK, so the link state machine (#102) checks it with decode_frame()
// and then calls start_session(). Rejected frames are dropped and counted
// (REQ-COM-004); the caller must not reset the link-loss timer for them
// (REQ-SAF-011).
class Receiver {
public:
  // Opens a session: the key and session_id every frame must match. Resets the
  // window to `last_seq`, the seq already used on this transport: pass 1 for
  // the TCP Receiver (HELLO and HELLO_ACK use seq 1, spec §1), 0 for UDP.
  // Returns false (and changes nothing) for session_id 0, which is reserved
  // for HELLO, or for last_seq >= 2^31.
  bool start_session(std::uint32_t session_id, const Key &session_key,
                     std::uint32_t last_seq = 0);
  void end_session();

  // `out` is written only when the result is accepted (is_accepted()).
  RxResult receive(const std::uint8_t *buf, std::size_t len, Frame &out);

  bool in_session() const { return in_session_; }
  std::uint32_t session_id() const { return session_id_; }
  std::uint32_t last_seq() const { return window_.last(); }
  // counters() last for the Receiver's lifetime; seq_counters() restart with
  // each session. How they are reported to the ground is open (#149).
  const RxCounters &counters() const { return counters_; }
  const SeqCounters &seq_counters() const { return window_.counters(); }

private:
  RxResult reject(RxResult r);

  Key key_ = {};
  std::uint32_t session_id_ = 0;
  bool in_session_ = false;
  SeqWindow window_;
  RxCounters counters_ = {};
};

} // namespace sd
