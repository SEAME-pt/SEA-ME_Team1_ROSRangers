#pragma once

// The ESP's side of the FC link (ADR-009): one scheduler owns the UART, so
// polling requests never collide. It sends one MSP request at a time, waits
// for its reply or a timeout, and keeps the latest values with the time they
// arrived. It only polls: it never sends RC (that is the link state machine,
// #102), so it is safe from power-up on (#101).
//
// Host-testable: time is passed in (`now_ms`, wraps every 49 days), bytes go
// in and out through the caller, nothing here touches the hardware.

#include <cstddef>
#include <cstdint>

#include "sd/msp.hpp"

namespace sd {

// Poll periods (ADR-009) and the reply timeout. At 115200 baud a request is
// about 0.5 ms and the longest reply (MSP_STATUS) about 3 ms.
constexpr std::uint32_t kAttitudePeriodMs = 40; // 25 Hz
constexpr std::uint32_t kStatusPeriodMs = 200;  // 5 Hz
constexpr std::uint32_t kAnalogPeriodMs = 200;  // 5 Hz
// MSP_API_VERSION: the FC answers, so the link works (BOOT -> NO_SESSION of
// the ESP Link State Machine, #102). Kept at 1 Hz to notice a silent FC.
constexpr std::uint32_t kApiVersionPeriodMs = 1000;
constexpr std::uint32_t kReplyTimeoutMs = 50;
// After a timeout, nothing is sent for this long and any reply that arrives is
// dropped, so a late reply that comes within the drain window is never taken as
// the answer to a newer request. Replies are matched by command ID only: one
// more than 100 ms late (the FC answers in ~3 ms) could still be.
constexpr std::uint32_t kDrainMs = kReplyTimeoutMs;
// A value older than this is forgotten (has_* false), so an old "disarmed"
// can never look fresh again when millis() wraps after 49.7 days.
constexpr std::uint32_t kMaxValueAgeMs = 10000;
// The FC counts as lost when no reply to a request came for this long (#179,
// proposal D1). A healthy FC answers ~85 times a second (an RC ack every
// 20 ms plus the polls), so this is ~20 missed replies in a row.
constexpr std::uint32_t kFcLostMs = 250;

// The latest value of each poll and when it arrived (`*_ms`). A value never
// received has `has_*` false.
struct FcTelemetry {
  bool has_attitude;
  std::uint32_t attitude_ms;
  msp::Attitude attitude;
  bool has_status;
  std::uint32_t status_ms;
  msp::Status status;
  bool has_analog;
  std::uint32_t analog_ms;
  msp::Analog analog;
  bool has_api_version; // an MSP_API_VERSION reply arrived
  std::uint32_t api_version_ms;
  std::uint8_t api_major; // 1.46 on 4.5.1
  std::uint8_t api_minor;
};

struct FcCounters {
  std::uint32_t requests;
  std::uint32_t replies;      // replies to the request in flight
  std::uint32_t timeouts;     // no reply within kReplyTimeoutMs
  std::uint32_t bad_checksum; // frames dropped by the parser
  std::uint32_t too_long;
  std::uint32_t error_replies; // '!' from the FC
  std::uint32_t unexpected;    // a reply nobody asked for, or undecodable
  std::uint32_t rc_sent;       // MSP_SET_RAW_RC frames written
  std::uint32_t rc_replaced;   // RC replaced by a newer one before it was sent
};

class FcLink {
public:
  FcLink();

  // Writes the next due request into `out` when none is in flight. Returns
  // its size, or 0 when nothing is due (or out_cap is too small). Call it
  // every loop and write the bytes to the UART.
  std::size_t poll(std::uint32_t now_ms, std::uint8_t *out,
                   std::size_t out_cap);

  // The RC channels (AETR) to send as MSP_SET_RAW_RC, ahead of every poll.
  // Only the latest set is kept: one not sent yet is replaced (rc_replaced).
  // The firmware calls it at 50 Hz with what sd::Link::fc_output() returns.
  void set_rc(const std::uint16_t channels[msp::kRcChannels]);
  // Nothing to send (sd::Link::fc_output() returned false).
  void stop_rc();

  // The FC answered MSP_API_VERSION within kMaxValueAgeMs.
  bool fc_ready() const { return telemetry_.has_api_version; }

  // A reply to one of our requests came less than kFcLostMs ago. An error
  // reply ('!') counts: the FC is there and answering. A reply nobody asked
  // for does not.
  bool fc_alive(std::uint32_t now_ms) const {
    return any_reply_ && now_ms - last_reply_ms_ < kFcLostMs;
  }

  // Feed every byte read from the UART.
  void on_byte(std::uint8_t byte, std::uint32_t now_ms);

  const FcTelemetry &telemetry() const { return telemetry_; }
  const FcCounters &counters() const { return counters_; }

  // True if an MSP_STATUS arrived less than `max_age_ms` ago. The OTA gate of
  // ADR-024 needs a fresh "disarmed" (500 ms).
  bool status_fresh(std::uint32_t now_ms, std::uint32_t max_age_ms) const;

private:
  struct Poll {
    std::uint8_t cmd;
    std::uint32_t period_ms;
    std::uint32_t last_sent_ms;
    bool sent_once;
  };

  void on_reply(const msp::Reply &reply, std::uint32_t now_ms);
  void expire(std::uint32_t now_ms);
  bool any_poll_due(std::uint32_t now_ms) const;

  Poll polls_[4];
  bool rc_pending_ = false;
  bool poll_first_ = false; // an RC timed out: let one due poll go next
  std::uint16_t rc_[msp::kRcChannels] = {};
  msp::Parser parser_;

  bool in_flight_ = false;
  bool any_reply_ = false; // expired with the values (kMaxValueAgeMs)
  std::uint32_t last_reply_ms_ = 0;
  bool draining_ = false;
  std::uint32_t drain_ms_ = 0;
  std::uint8_t in_flight_cmd_ = 0;
  std::uint32_t in_flight_ms_ = 0;
  FcTelemetry telemetry_ = {};
  FcCounters counters_ = {};
};

} // namespace sd
