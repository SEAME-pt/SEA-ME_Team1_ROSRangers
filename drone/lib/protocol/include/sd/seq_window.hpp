#pragma once

#include <cstdint>

namespace sd {

// ADR-003: accept a message only if its seq is newer than the last accepted one.
enum class SeqResult : std::uint8_t {
  kAccepted = 0,
  kAcceptedGap,     // newer, but some seq numbers were skipped (counted, not an error)
  kDuplicate,       // same seq as the last accepted one
  kOld,             // older than the last accepted one (reordered or replayed), or seq 0
  kNeedNewSession,  // seq >= 2^31: the sender must open a new session
};

struct SeqCounters {
  std::uint32_t duplicates;
  std::uint32_t old;
  std::uint32_t gaps;          // number of accepted messages that came after a gap
  std::uint32_t missing;       // total seq numbers skipped over all gaps
  std::uint32_t too_high;
};

// One window per sender, per session (and per transport, spec §7.5 suggestion).
// Reset it when a new session starts.
class SeqWindow {
public:
  static constexpr std::uint32_t kMaxSeq = 0x80000000U; // 2^31

  SeqResult accept(std::uint32_t seq);
  void reset();

  std::uint32_t last() const { return last_; }
  const SeqCounters &counters() const { return counters_; }

private:
  std::uint32_t last_ = 0;
  SeqCounters counters_ = {};
};

} // namespace sd
