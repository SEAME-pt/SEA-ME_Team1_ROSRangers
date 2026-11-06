#include "sd/msp.hpp"

#include <cstring>

#include "le.hpp"

namespace sd {
namespace msp {

bool allowed(std::uint8_t cmd) {
  switch (cmd) {
  case kApiVersion:
  case kStatus:
  case kRc:
  case kAttitude:
  case kAnalog:
  case kSetRawRc:
    return true;
  default:
    return false;
  }
}

std::size_t encode_request(std::uint8_t cmd, const std::uint8_t *payload,
                           std::size_t len, std::uint8_t *out,
                           std::size_t out_cap) {
  const std::size_t size = kHeaderSize + len + 1;
  if (!allowed(cmd) || out == nullptr || (payload == nullptr && len > 0) ||
      len > kMaxPayload || out_cap < size) {
    return 0;
  }
  out[0] = '$';
  out[1] = 'M';
  out[2] = '<';
  out[3] = static_cast<std::uint8_t>(len);
  out[4] = cmd;
  std::uint8_t checksum = static_cast<std::uint8_t>(len) ^ cmd;
  for (std::size_t i = 0; i < len; ++i) {
    out[kHeaderSize + i] = payload[i];
    checksum ^= payload[i];
  }
  out[kHeaderSize + len] = checksum;
  return size;
}

std::size_t encode_set_raw_rc(const std::uint16_t channels[kRcChannels],
                              std::uint8_t *out, std::size_t out_cap) {
  if (channels == nullptr) {
    return 0;
  }
  std::uint8_t payload[2 * kRcChannels];
  for (std::size_t i = 0; i < kRcChannels; ++i) {
    put_u16(payload + 2 * i, channels[i]);
  }
  return encode_request(kSetRawRc, payload, sizeof(payload), out, out_cap);
}

Parser::Event Parser::feed(std::uint8_t byte) {
  switch (state_) {
  case State::kIdle:
    if (byte == '$') {
      state_ = State::kM;
    }
    return Event::kNone;
  case State::kM:
    state_ =
        byte == 'M' ? State::kDir : (byte == '$' ? State::kM : State::kIdle);
    return Event::kNone;
  case State::kDir:
    if (byte == '>' || byte == '!') {
      error_ = byte == '!';
      state_ = State::kLen;
    } else {
      // A request ('<') or noise: not a reply.
      state_ = byte == '$' ? State::kM : State::kIdle;
    }
    return Event::kNone;
  case State::kLen:
    pending_len_ = byte;
    checksum_ = byte;
    too_long_ = byte > kMaxPayload;
    state_ = State::kCmd;
    return Event::kNone;
  case State::kCmd:
    checksum_ ^= byte;
    if (too_long_) {
      // Skip the payload and the checksum without storing them.
      skip_ = static_cast<std::uint16_t>(pending_len_ + 1U);
      state_ = State::kSkip;
      return Event::kNone;
    }
    reply_.cmd = byte;
    reply_.error = error_;
    reply_.len = pending_len_;
    pos_ = 0;
    state_ = pending_len_ == 0 ? State::kChecksum : State::kPayload;
    return Event::kNone;
  case State::kPayload:
    reply_.payload[pos_++] = byte;
    checksum_ ^= byte;
    if (pos_ == pending_len_) {
      state_ = State::kChecksum;
    }
    return Event::kNone;
  case State::kChecksum:
    state_ = State::kIdle;
    return byte == checksum_ ? Event::kReply : Event::kBadChecksum;
  case State::kSkip:
    if (--skip_ == 0) {
      state_ = State::kIdle;
      return Event::kTooLong;
    }
    return Event::kNone;
  }
  state_ = State::kIdle;
  return Event::kNone;
}

bool decode_attitude(const Reply &reply, Attitude &out) {
  if (reply.error || reply.cmd != kAttitude || reply.len < 6) {
    return false;
  }
  out.roll = get_i16(reply.payload);
  out.pitch = get_i16(reply.payload + 2);
  out.heading = get_u16(reply.payload + 4);
  return true;
}

bool decode_analog(const Reply &reply, Analog &out) {
  if (reply.error || reply.cmd != kAnalog || reply.len < 9) {
    return false;
  }
  out.vbat = get_u16(reply.payload + 7);
  return true;
}

bool decode_status(const Reply &reply, Status &out) {
  // u16 cycle time, u16 I2C errors, u16 sensors, u32 flight mode flags,
  // u8 profile, u16 load, u16 gyro cycle time, u8 n, n extra flag bytes,
  // u8 arming-disable flag count, u32 arming-disable flags, ...
  constexpr std::size_t kModeFlags = 6;
  constexpr std::size_t kExtraCount = 15;
  if (reply.error || reply.cmd != kStatus || reply.len < kExtraCount + 1) {
    return false;
  }
  const std::size_t extra = reply.payload[kExtraCount] & 0x0F;
  const std::size_t arming = kExtraCount + 1 + extra + 1;
  if (reply.len < arming + 4) {
    return false;
  }
  out.armed = (reply.payload[kModeFlags] & 0x01) != 0;
  out.arming_disable_flags = get_u32(reply.payload + arming);
  return true;
}

} // namespace msp
} // namespace sd
