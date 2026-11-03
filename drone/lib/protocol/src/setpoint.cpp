#include "sd/setpoint.hpp"

#include "le.hpp"

namespace sd {
namespace {

std::int32_t clamp(std::int32_t v, std::int32_t lo, std::int32_t hi) {
  return v < lo ? lo : (v > hi ? hi : v);
}

// -1000..1000 -> 1000..2000 us (integer, rounds toward the centre).
std::uint16_t axis_to_us(std::int16_t v) {
  return static_cast<std::uint16_t>(kRcMid + clamp(v, -1000, 1000) / 2);
}

} // namespace

std::size_t encode_setpoint(const Setpoint &sp, std::uint8_t *out, std::size_t out_cap) {
  if (out_cap < kSetpointSize) {
    return 0;
  }
  out[0] = sp.mode;
  put_i16(out + 1, sp.roll);
  put_i16(out + 3, sp.pitch);
  put_i16(out + 5, sp.yaw_rate);
  put_i16(out + 7, sp.throttle);
  out[9] = sp.flags;
  return kSetpointSize;
}

bool decode_setpoint(const std::uint8_t *payload, std::size_t len, Setpoint &out) {
  if (payload == nullptr || len != kSetpointSize) {
    return false;
  }
  out.mode = payload[0];
  out.roll = get_i16(payload + 1);
  out.pitch = get_i16(payload + 3);
  out.yaw_rate = get_i16(payload + 5);
  out.throttle = get_i16(payload + 7);
  out.flags = payload[9];
  return true;
}

void setpoint_to_rc(const Setpoint &sp, std::uint16_t channels[kRcChannels]) {
  channels[0] = axis_to_us(sp.roll);
  channels[1] = axis_to_us(sp.pitch);
  channels[2] = static_cast<std::uint16_t>(kRcMin + clamp(sp.throttle, 0, 1000));
  channels[3] = axis_to_us(sp.yaw_rate);
  for (std::size_t i = 4; i < kRcChannels; ++i) {
    channels[i] = kRcMid;
  }
}

} // namespace sd
