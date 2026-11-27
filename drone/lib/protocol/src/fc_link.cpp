#include "sd/fc_link.hpp"

#include <algorithm>
#include <iterator>

namespace sd {

FcLink::FcLink()
    : polls_{{msp::kAttitude, kAttitudePeriodMs, 0, false, 0, 0},
             {msp::kStatus, kStatusPeriodMs, 0, false, 0, 0},
             {msp::kAnalog, kAnalogPeriodMs, 0, false, 0, 0},
             // Last: at start-up the polls above go first, as before.
             {msp::kApiVersion, kApiVersionPeriodMs, 0, false, 0, 0},
             {msp::kBoxIds, kBoxIdsPeriodMs, 0, false, 0, 0}} {}

void FcLink::set_rc(const std::uint16_t channels[msp::kRcChannels]) {
  if (rc_pending_) {
    ++counters_.rc_replaced;
  }
  for (std::size_t i = 0; i < msp::kRcChannels; ++i) {
    rc_[i] = channels[i];
  }
  rc_pending_ = true;
}

void FcLink::stop_rc() { rc_pending_ = false; }

void FcLink::expire(std::uint32_t now_ms) {
  if (telemetry_.has_attitude &&
      now_ms - telemetry_.attitude_ms > kMaxValueAgeMs) {
    telemetry_.has_attitude = false;
  }
  if (telemetry_.has_status && now_ms - telemetry_.status_ms > kMaxValueAgeMs) {
    telemetry_.has_status = false;
  }
  if (telemetry_.has_analog && now_ms - telemetry_.analog_ms > kMaxValueAgeMs) {
    telemetry_.has_analog = false;
  }
  if (telemetry_.has_api_version &&
      now_ms - telemetry_.api_version_ms > kMaxValueAgeMs) {
    telemetry_.has_api_version = false;
  }
  if (has_boxes_ && now_ms - boxes_ms_ > kMaxValueAgeMs) {
    forget_boxes();
  }
  // Like the values: never "alive" again just because millis() wrapped.
  if (any_reply_ && now_ms - last_reply_ms_ > kMaxValueAgeMs) {
    any_reply_ = false;
    alive_run_ = false;
  }
  if (rc_run_ && now_ms - last_rc_ack_ms_ > kMaxValueAgeMs) {
    rc_run_ = false;
  }
}

bool FcLink::any_poll_due(std::uint32_t now_ms) const {
  return std::any_of(
      std::begin(polls_), std::end(polls_), [now_ms](const Poll &p) {
        return !p.sent_once || now_ms - p.last_sent_ms >= p.period_ms;
      });
}

FcLink::Poll *FcLink::most_overdue_poll(std::uint32_t now_ms) {
  Poll *next = nullptr;
  std::uint32_t most_late = 0;
  for (Poll &p : polls_) {
    const std::uint32_t since = now_ms - p.last_sent_ms;
    if (p.sent_once && since < p.period_ms) {
      continue;
    }
    const std::uint32_t late = p.sent_once ? since - p.period_ms : UINT32_MAX;
    if (next == nullptr || late > most_late) {
      next = &p;
      most_late = late;
    }
  }
  return next;
}

FcLink::Poll *FcLink::find_poll(std::uint8_t cmd) {
  Poll *const found =
      std::find_if(std::begin(polls_), std::end(polls_),
                   [cmd](const Poll &p) { return p.cmd == cmd; });
  if (found == std::end(polls_)) {
    return nullptr;
  }
  return found;
}

void FcLink::note_failure(std::uint8_t cmd, std::uint32_t now_ms) {
  Poll *const p = find_poll(cmd);
  if (p == nullptr) {
    return; // RC is not a poll
  }
  if (p->failures < UINT8_MAX) {
    ++p->failures;
  }
  p->failed_ms = now_ms;
}

void FcLink::note_success(std::uint8_t cmd) {
  Poll *const p = find_poll(cmd);
  if (p != nullptr) {
    p->failures = 0;
  }
}

std::uint32_t FcLink::urgent_backoff_ms(std::uint8_t failures) {
  // 0 or 1 failure: no wait (one lost reply is retried at once). Then
  // kUrgentBackoffFirstMs, doubled per extra failure, capped. Example:
  // 4 failures -> 400, 800, 1600 ms.
  if (failures < 2) {
    return 0;
  }
  std::uint32_t wait = kUrgentBackoffFirstMs;
  for (std::uint8_t n = 2; n < failures && wait < kUrgentBackoffMs; ++n) {
    wait = wait * 2;
  }
  return std::min(wait, kUrgentBackoffMs);
}

FcLink::Poll *FcLink::urgent_poll(std::uint32_t now_ms) {
  const auto is_urgent = [this, now_ms](const Poll &p) {
    // Requests of this kind failed in a row: not urgent while it backs off (an
    // FC that never answers it must not cost RC ~100 ms every cycle, #188).
    if (now_ms - p.failed_ms < urgent_backoff_ms(p.failures)) {
      return false;
    }
    // The next page of a box list being read is urgent too, or a list longer
    // than one page would never be read again on a slow FC. A failed page
    // sets box_page_ back to 0, so this cannot repeat forever.
    if (p.cmd == msp::kBoxIds && box_page_ > 0) {
      return true;
    }
    // Never before the first request of its kind (start-up keeps the usual
    // order: attitude first, RC first). Only when due, except the retry after
    // one failure, which goes at once.
    if (!p.sent_once) {
      return false;
    }
    const bool retry = p.failures == 1;
    if (!retry && now_ms - p.last_sent_ms < p.period_ms) {
      return false;
    }
    if (p.cmd == msp::kStatus) {
      return !status_fresh(now_ms, kStatusUrgentAgeMs);
    }
    if (p.cmd == msp::kBoxIds) {
      return !has_boxes_ || now_ms - boxes_ms_ >= kBoxIdsUrgentAgeMs;
    }
    return false;
  };
  // polls_ lists MSP_STATUS before MSP_BOXIDS, so the status goes first.
  Poll *const found =
      std::find_if(std::begin(polls_), std::end(polls_), is_urgent);
  if (found == std::end(polls_)) {
    return nullptr;
  }
  return found;
}

std::size_t FcLink::poll(std::uint32_t now_ms, std::uint8_t *out,
                         std::size_t out_cap) {
  expire(now_ms);
  if (in_flight_) {
    if (now_ms - in_flight_ms_ < kReplyTimeoutMs) {
      return 0;
    }
    ++counters_.timeouts;
    alive_run_ = false; // #188: a missed reply ends the run of good replies
    if (in_flight_cmd_ == msp::kSetRawRc) {
      rc_run_ = false;
    }
    if (in_flight_cmd_ == msp::kBoxIds) {
      box_page_ = 0; // a lost page: read the list again from the start
    }
    note_failure(in_flight_cmd_, now_ms); // #252
    // A silent FC must not starve the polls: after an RC timeout one due poll
    // goes before the next RC, so the FC is seen when it answers again.
    poll_first_ = in_flight_cmd_ == msp::kSetRawRc;
    in_flight_ = false;
    draining_ = true;
    drain_ms_ = now_ms;
  }
  if (draining_) {
    if (now_ms - drain_ms_ < kDrainMs) {
      return 0;
    }
    draining_ = false;
  }
  // RC goes first: it is the control output (50 Hz), the polls fit around it.
  // Two exceptions: after an RC timeout one due poll goes first, and an urgent
  // status or box list poll goes first on a slow FC (#252).
  Poll *const urgent = urgent_poll(now_ms);
  const bool poll_turn = poll_first_ && any_poll_due(now_ms);
  if (rc_pending_ && urgent == nullptr && !poll_turn) {
    const std::size_t n = msp::encode_set_raw_rc(rc_, out, out_cap);
    if (n == 0) {
      return 0;
    }
    rc_pending_ = false;
    in_flight_ = true;
    in_flight_cmd_ = msp::kSetRawRc;
    in_flight_ms_ = now_ms;
    ++counters_.requests;
    ++counters_.rc_sent;
    return n;
  }
  // An urgent poll goes as it is. Otherwise the most overdue poll goes first,
  // so a slow FC still gets every kind of request in turn. A poll never sent
  // is the most overdue.
  Poll *next = urgent;
  if (next == nullptr) {
    next = most_overdue_poll(now_ms);
  }
  if (next == nullptr) {
    return 0;
  }
  // MSP_BOXIDS page 0 goes without a payload (Betaflight's default), later
  // pages with the page number.
  const bool paged = next->cmd == msp::kBoxIds && box_page_ > 0;
  const std::size_t n = msp::encode_request(
      next->cmd, paged ? &box_page_ : nullptr, paged ? 1 : 0, out, out_cap);
  if (n == 0) {
    return 0;
  }
  next->last_sent_ms = now_ms;
  next->sent_once = true;
  poll_first_ = false;
  in_flight_ = true;
  in_flight_cmd_ = next->cmd;
  in_flight_ms_ = now_ms;
  ++counters_.requests;
  return n;
}

void FcLink::on_byte(std::uint8_t byte, std::uint32_t now_ms) {
  switch (parser_.feed(byte)) {
  case msp::Parser::Event::kNone:
    return;
  case msp::Parser::Event::kBadChecksum:
    ++counters_.bad_checksum;
    return;
  case msp::Parser::Event::kTooLong:
    ++counters_.too_long;
    return;
  case msp::Parser::Event::kReply:
    on_reply(parser_.reply(), now_ms);
    return;
  }
}

void FcLink::on_reply(const msp::Reply &reply, std::uint32_t now_ms) {
  if (!in_flight_ || reply.cmd != in_flight_cmd_) {
    ++counters_.unexpected;
    return;
  }
  in_flight_ = false;
  ++counters_.replies;
  // #188: a new run after a timeout, or after a silence of kFcLostMs with no
  // request in flight (a stalled loop): checked before last_reply_ms_ moves.
  if (!alive_run_ || !any_reply_ || now_ms - last_reply_ms_ >= kFcLostMs) {
    alive_run_ = true;
    alive_since_ms_ = now_ms;
  }
  // #176: after kFcLostMs of silence the FC may have rebooted with another
  // box list, so the old one is never matched with a new MSP_STATUS.
  if (any_reply_ && now_ms - last_reply_ms_ >= kFcLostMs) {
    forget_boxes();
  }
  any_reply_ = true; // an error reply too: the FC is answering (#179)
  last_reply_ms_ = now_ms;
  if (reply.error) {
    ++counters_.error_replies;
    if (reply.cmd == msp::kSetRawRc) {
      rc_run_ = false; // '!': the FC refused the RC frame (#188)
    }
    if (reply.cmd == msp::kBoxIds) {
      box_page_ = 0; // a refused page: read the list again from the start
    }
    note_failure(reply.cmd, now_ms); // #252
    return;
  }
  bool ok = false;
  switch (reply.cmd) {
  case msp::kAttitude:
    ok = msp::decode_attitude(reply, telemetry_.attitude);
    if (ok) {
      telemetry_.has_attitude = true;
      telemetry_.attitude_ms = now_ms;
    }
    break;
  case msp::kStatus:
    ok = msp::decode_status(reply, telemetry_.status);
    if (ok) {
      telemetry_.has_status = true;
      telemetry_.status_ms = now_ms;
    }
    break;
  case msp::kAnalog:
    ok = msp::decode_analog(reply, telemetry_.analog);
    if (ok) {
      telemetry_.has_analog = true;
      telemetry_.analog_ms = now_ms;
    }
    break;
  case msp::kApiVersion:
    // [MSP protocol version, API major, API minor]
    ok = reply.len >= 3;
    if (ok) {
      telemetry_.api_major = reply.payload[1];
      telemetry_.api_minor = reply.payload[2];
      telemetry_.has_api_version = true;
      telemetry_.api_version_ms = now_ms;
    }
    break;
  case msp::kBoxIds:
    ok = true; // any list of IDs is valid, an empty one too
    on_box_ids(reply, now_ms);
    break;
  case msp::kSetRawRc:
    ok = true; // empty acknowledgement
    // #188: a new run after a gap (RC stopped, e.g. the ADR-004 output)
    if (!rc_run_ || now_ms - last_rc_ack_ms_ >= kRcMaxGapMs) {
      rc_run_ = true;
      rc_since_ms_ = now_ms;
    }
    last_rc_ack_ms_ = now_ms;
    break;
  default:
    break;
  }
  if (!ok) {
    ++counters_.unexpected;
    note_failure(reply.cmd, now_ms); // #252
    return;
  }
  // #252: a box list counts as read only when its last page came (box_page_
  // back to 0), so a list whose later page keeps failing still backs off.
  const bool list_done = reply.cmd != msp::kBoxIds || box_page_ == 0;
  if (list_done) {
    note_success(reply.cmd);
  }
}

void FcLink::on_box_ids(const msp::Reply &reply, std::uint32_t now_ms) {
  for (std::size_t i = 0; i < reply.len; ++i) {
    if (reply.payload[i] == msp::kBoxMspOverride) {
      override_box_ = static_cast<int>(box_page_ * msp::kBoxesPerPage + i);
      if (!has_boxes_) {
        boxes_since_ms_ = now_ms;
      }
      has_boxes_ = true;
      boxes_ms_ = now_ms;
      box_page_ = 0;
      return;
    }
  }
  // A full page: the box may be on the next one (Betaflight sends at most
  // 128 mode bits, 4 pages). Ask for it at once.
  if (reply.len == msp::kBoxesPerPage &&
      box_page_ + 1U < msp::kMaxModeBytes * 8 / msp::kBoxesPerPage) {
    ++box_page_;
    for (Poll &p : polls_) {
      if (p.cmd == msp::kBoxIds) {
        p.sent_once = false;
      }
    }
    return;
  }
  override_box_ = -1; // the FC has no MSP OVERRIDE box
  if (!has_boxes_) {
    boxes_since_ms_ = now_ms;
  }
  has_boxes_ = true;
  boxes_ms_ = now_ms;
  box_page_ = 0;
}

void FcLink::forget_boxes() {
  has_boxes_ = false;
  override_box_ = -1;
  box_page_ = 0;
}

OverrideState FcLink::override_state(std::uint32_t now_ms) const {
  // The STATUS must come from the same FC boot as the box list (#198 review).
  if (!has_boxes_ || override_box_ < 0 ||
      !status_fresh(now_ms, kOverrideStatusMaxAgeMs) ||
      static_cast<std::int32_t>(telemetry_.status_ms - boxes_since_ms_) < 0) {
    return OverrideState::kUnknown;
  }
  const std::size_t box = static_cast<std::size_t>(override_box_);
  if (box / 8 >= telemetry_.status.mode_bytes) {
    return OverrideState::kUnknown; // the FC sent no bit for it
  }
  return msp::mode_active(telemetry_.status, box) ? OverrideState::kOn
                                                  : OverrideState::kOff;
}

bool FcLink::fc_up(bool was_up, std::uint32_t now_ms) const {
  if (!fc_alive(now_ms)) {
    return false;
  }
  return was_up || (alive_run_ && now_ms - alive_since_ms_ >= kFcRestoredMs);
}

bool FcLink::rc_accepted(std::uint32_t now_ms) const {
  return rc_run_ && now_ms - last_rc_ack_ms_ < kRcMaxGapMs &&
         now_ms - rc_since_ms_ >= kRcStableMs;
}

bool FcLink::status_fresh(std::uint32_t now_ms,
                          std::uint32_t max_age_ms) const {
  return telemetry_.has_status && now_ms - telemetry_.status_ms < max_age_ms;
}

} // namespace sd
