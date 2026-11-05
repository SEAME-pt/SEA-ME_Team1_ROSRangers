#pragma once

#include <cstddef>
#include <cstdint>

namespace sd {

// SETPOINT (0x60) payload, spec §3.
struct Setpoint {
  std::uint8_t mode;     // 0 neutral, 1 attitude
  std::int16_t roll;     // -1000..1000
  std::int16_t pitch;    // -1000..1000
  std::int16_t yaw_rate; // -1000..1000
  std::int16_t throttle; // 0..1000
  std::uint8_t flags;    // reserved in v0: sent as 0, ignored (spec §3)
};

constexpr std::size_t kSetpointSize = 10;
constexpr std::size_t kRcChannels = 8;
constexpr std::uint16_t kRcMin = 1000;
constexpr std::uint16_t kRcMid = 1500;
constexpr std::uint16_t kRcMax = 2000;

// Writes kSetpointSize bytes. Returns kSetpointSize, or 0 if `out` is null or
// out_cap is too small.
std::size_t encode_setpoint(const Setpoint &sp, std::uint8_t *out,
                            std::size_t out_cap);

// Reads a payload of exactly kSetpointSize bytes. Values are not range-checked
// here.
bool decode_setpoint(const std::uint8_t *payload, std::size_t len,
                     Setpoint &out);

// Spec §4 rule 4 and §5: clamps and maps to MSP_SET_RAW_RC channels in AETR
// order [roll, pitch, throttle, yaw, aux1..aux4]. -1000..1000 -> 1000..2000 us,
// throttle 0..1000 -> 1000..2000 us, aux always 1500 (never driven by the
// ground). `mode` is not used here: deciding what a neutral or stale setpoint
// sends is the link state machine's job (#102).
void setpoint_to_rc(const Setpoint &sp, std::uint16_t channels[kRcChannels]);

} // namespace sd
