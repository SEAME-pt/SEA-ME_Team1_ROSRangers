#include "sd/fc_link.hpp"

namespace sd {

FcLink::FcLink()
    : polls_{{msp::kAttitude, kAttitudePeriodMs, 0, false},
             {msp::kStatus, kStatusPeriodMs, 0, false},
             {msp::kAnalog, kAnalogPeriodMs, 0, false},
             // Last: at start-up the polls above go first, as before.
             {msp::kApiVersion, kApiVersionPeriodMs, 0, false}} {}

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
}

bool FcLink::any_poll_due(std::uint32_t now_ms) const {
  for (const Poll &p : polls_) {
    if (!p.sent_once || now_ms - p.last_sent_ms >= p.period_ms) {
      return true;
    }
  }
  return false;
}

std::size_t FcLink::poll(std::uint32_t now_ms, std::uint8_t *out,
                         std::size_t out_cap) {
  expire(now_ms);
  if (in_flight_) {
    if (now_ms - in_flight_ms_ < kReplyTimeoutMs) {
      return 0;
    }
    ++counters_.timeouts;
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
  if (rc_pending_ && !(poll_first_ && any_poll_due(now_ms))) {
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
  // The most overdue poll goes first, so a slow FC still gets every kind of
  // request in turn. A poll never sent is the most overdue.
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
  if (next == nullptr) {
    return 0;
  }
  const std::size_t n =
      msp::encode_request(next->cmd, nullptr, 0, out, out_cap);
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
  if (reply.error) {
    ++counters_.error_replies;
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
  case msp::kSetRawRc:
    ok = true; // empty acknowledgement
    break;
  default:
    break;
  }
  if (!ok) {
    ++counters_.unexpected;
  }
}

bool FcLink::status_fresh(std::uint32_t now_ms,
                          std::uint32_t max_age_ms) const {
  return telemetry_.has_status && now_ms - telemetry_.status_ms < max_age_ms;
}

} // namespace sd
