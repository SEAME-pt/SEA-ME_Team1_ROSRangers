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
  kWrongSession, // session_id is not the current session (spec §4 rule 2)
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
  std::uint32_t duplicate_seq;
  std::uint32_t old_seq;
  std::uint32_t seq_too_high;
};

// Receive rules of spec §4 (1 to 3) for one transport inside one session: frame
// checks, session check, then the sequence window (ADR-003). Use one Receiver
// per transport (UDP control, TCP session), as suggested in spec §7.5.
//
// HELLO is not handled here: it arrives before a session exists and is tagged
// with the PSK, so the link state machine (#102) checks it with decode_frame()
// and then calls start_session(). Rejected frames are dropped and counted
// (REQ-COM-004); the caller must not reset the link-loss timer for them
// (REQ-SAF-011).
class Receiver {
public:
  // Opens a session: the key and session_id every frame must match. Resets the
  // window. session_id 0 is reserved for HELLO and is refused (returns false).
  bool start_session(std::uint32_t session_id, const Key &session_key);
  void end_session();

  // `out` is written only when the result is accepted (is_accepted()).
  RxResult receive(const std::uint8_t *buf, std::size_t len, Frame &out);

  bool in_session() const { return in_session_; }
  std::uint32_t session_id() const { return session_id_; }
  std::uint32_t last_seq() const { return window_.last(); }
  // counters() last for the Receiver's lifetime; seq_counters() restart with
  // each session. How they are reported to the ground is open (spec §7.5).
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
