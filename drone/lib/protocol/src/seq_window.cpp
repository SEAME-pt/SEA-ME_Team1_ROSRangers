#include "sd/seq_window.hpp"

namespace sd {

SeqResult SeqWindow::accept(std::uint32_t seq) {
  if (seq >= kMaxSeq) {
    ++counters_.too_high;
    return SeqResult::kNeedNewSession;
  }
  if (seq == last_ && seq != 0) {
    ++counters_.duplicates;
    return SeqResult::kDuplicate;
  }
  if (seq <= last_) {
    ++counters_.old;
    return SeqResult::kOld;
  }
  const std::uint32_t skipped = seq - last_ - 1;
  last_ = seq;
  if (skipped > 0) {
    ++counters_.gaps;
    counters_.missing += skipped;
    return SeqResult::kAcceptedGap;
  }
  return SeqResult::kAccepted;
}

void SeqWindow::reset() {
  last_ = 0;
  counters_ = {};
}

} // namespace sd
