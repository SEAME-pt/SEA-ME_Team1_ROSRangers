#include "sd/ota_lock.hpp"

namespace sd {

namespace {

// Sends one request and waits for the reply with the same command. Other
// replies are ignored. False on timeout, error reply ('!') or no output.
bool exchange(FcUart &uart, const std::uint8_t *frame, std::size_t len,
              std::uint8_t cmd, msp::Reply &reply) {
  if (len == 0) {
    return false;
  }
  uart.write(frame, len);
  msp::Parser parser;
  const std::uint32_t start = uart.now_ms();
  while (uart.now_ms() - start < kOtaReplyTimeoutMs) {
    const int b = uart.read();
    if (b < 0) {
      continue;
    }
    if (parser.feed(static_cast<std::uint8_t>(b)) ==
            msp::Parser::Event::kReply &&
        parser.reply().cmd == cmd) {
      reply = parser.reply();
      return !reply.error;
    }
  }
  return false;
}

// Waits until nothing has arrived for kOtaQuietMs, throwing bytes away.
// False if the FC keeps talking for kOtaQuietMaxMs.
bool wait_quiet(FcUart &uart) {
  const std::uint32_t start = uart.now_ms();
  std::uint32_t last = start;
  while (uart.now_ms() - last < kOtaQuietMs) {
    if (uart.now_ms() - start >= kOtaQuietMaxMs) {
      return false;
    }
    if (uart.read() >= 0) {
      last = uart.now_ms();
    }
  }
  uart.discard_input();
  return true;
}

bool fresh_status(FcUart &uart, msp::Status &status) {
  std::uint8_t frame[msp::kMaxFrame];
  const std::size_t n =
      msp::encode_request(msp::kStatus, nullptr, 0, frame, sizeof frame);
  msp::Reply reply{};
  return exchange(uart, frame, n, msp::kStatus, reply) &&
         msp::decode_status(reply, status);
}

bool set_arming_disabled(FcUart &uart, bool disable) {
  std::uint8_t frame[msp::kMaxFrame];
  const std::size_t n =
      msp::encode_set_arming_disabled(disable, frame, sizeof frame);
  msp::Reply reply{};
  return exchange(uart, frame, n, msp::kSetArmingDisabled, reply);
}

} // namespace

bool ota_gate_open(const Link &link, const FcLink &fc, std::uint32_t now_ms) {
  return link.state() == EspState::kNoSession && !link.has_pending() &&
         fc.status_fresh(now_ms, kOtaStatusMaxAgeMs) &&
         !fc.telemetry().status.armed;
}

OtaLockResult ota_lock(FcUart &uart) {
  // Step 1: a fresh status, never a buffered or late one.
  uart.discard_input();
  msp::Status status{};
  if (!wait_quiet(uart) || !fresh_status(uart, status)) {
    return OtaLockResult::kNoReply;
  }
  if (status.armed) {
    return OtaLockResult::kArmed; // value 1 would disarm it: never sent
  }
  // Step 2: lock, then read it back.
  if (!set_arming_disabled(uart, true) || !fresh_status(uart, status) ||
      (status.arming_disable_flags & msp::kArmingDisabledMsp) == 0) {
    return OtaLockResult::kNotLocked;
  }
  return OtaLockResult::kLocked;
}

bool ota_release(FcUart &uart) {
  uart.discard_input();
  return set_arming_disabled(uart, false);
}

} // namespace sd
