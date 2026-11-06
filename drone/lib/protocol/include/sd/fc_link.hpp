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
constexpr std::uint32_t kReplyTimeoutMs = 50;

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
};

struct FcCounters {
  std::uint32_t requests;
  std::uint32_t replies;      // replies to the request in flight
  std::uint32_t timeouts;     // no reply within kReplyTimeoutMs
  std::uint32_t bad_checksum; // frames dropped by the parser
  std::uint32_t too_long;
  std::uint32_t error_replies; // '!' from the FC
  std::uint32_t unexpected;    // a reply nobody asked for, or undecodable
};

class FcLink {
public:
  FcLink();

  // Writes the next due request into `out` when none is in flight. Returns
  // its size, or 0 when nothing is due (or out_cap is too small). Call it
  // every loop and write the bytes to the UART.
  std::size_t poll(std::uint32_t now_ms, std::uint8_t *out,
                   std::size_t out_cap);

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

  Poll polls_[3];
  msp::Parser parser_;
  bool in_flight_ = false;
  std::uint8_t in_flight_cmd_ = 0;
  std::uint32_t in_flight_ms_ = 0;
  FcTelemetry telemetry_ = {};
  FcCounters counters_ = {};
};

} // namespace sd
