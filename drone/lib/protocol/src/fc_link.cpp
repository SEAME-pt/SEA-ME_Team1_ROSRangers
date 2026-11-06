#include "sd/fc_link.hpp"

namespace sd {

FcLink::FcLink()
    : polls_{{msp::kAttitude, kAttitudePeriodMs, 0, false},
             {msp::kStatus, kStatusPeriodMs, 0, false},
             {msp::kAnalog, kAnalogPeriodMs, 0, false}} {}

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
}

std::size_t FcLink::poll(std::uint32_t now_ms, std::uint8_t *out,
                         std::size_t out_cap) {
  expire(now_ms);
  if (in_flight_) {
    if (now_ms - in_flight_ms_ < kReplyTimeoutMs) {
      return 0;
    }
    ++counters_.timeouts;
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
