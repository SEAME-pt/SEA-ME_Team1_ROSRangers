#include "sd/messages.hpp"

#include <cstring>

#include "le.hpp"
#include "sd/frame.hpp"
#include "sd/setpoint.hpp"

namespace sd {

std::size_t encode_hello(const Hello &m, std::uint8_t *out,
                         std::size_t out_cap) {
  if (out == nullptr || out_cap < kHelloSize) {
    return 0;
  }
  out[0] = m.proto;
  std::memcpy(out + 1, m.ground_nonce, kNonceSize);
  return kHelloSize;
}

bool decode_hello(const std::uint8_t *payload, std::size_t len, Hello &out) {
  if (payload == nullptr || len != kHelloSize) {
    return false;
  }
  out.proto = payload[0];
  std::memcpy(out.ground_nonce, payload + 1, kNonceSize);
  return true;
}

std::size_t encode_hello_ack(const HelloAck &m, std::uint8_t *out,
                             std::size_t out_cap) {
  if (out == nullptr || out_cap < kHelloAckSize) {
    return 0;
  }
  put_u32(out, m.session_id);
  std::memcpy(out + 4, m.esp_nonce, kNonceSize);
  std::memcpy(out + 4 + kNonceSize, m.fw_version, kFwVersionSize);
  return kHelloAckSize;
}

bool decode_hello_ack(const std::uint8_t *payload, std::size_t len,
                      HelloAck &out) {
  if (payload == nullptr || len != kHelloAckSize) {
    return false;
  }
  out.session_id = get_u32(payload);
  std::memcpy(out.esp_nonce, payload + 4, kNonceSize);
  std::memcpy(out.fw_version, payload + 4 + kNonceSize, kFwVersionSize);
  return true;
}

std::size_t encode_u8_payload(std::uint8_t v, std::uint8_t *out,
                              std::size_t out_cap) {
  if (out == nullptr || out_cap < 1) {
    return 0;
  }
  out[0] = v;
  return 1;
}

bool decode_u8_payload(const std::uint8_t *payload, std::size_t len,
                       std::uint8_t &out) {
  if (payload == nullptr || len != 1) {
    return false;
  }
  out = payload[0];
  return true;
}

std::size_t encode_config_kv(const ConfigKv &m, std::uint8_t *out,
                             std::size_t out_cap) {
  if (out == nullptr || out_cap < kConfigKvSize) {
    return 0;
  }
  out[0] = m.key;
  put_i32(out + 1, m.value);
  return kConfigKvSize;
}

bool decode_config_kv(const std::uint8_t *payload, std::size_t len,
                      ConfigKv &out) {
  if (payload == nullptr || len != kConfigKvSize) {
    return false;
  }
  out.key = payload[0];
  out.value = get_i32(payload + 1);
  return true;
}

std::size_t encode_config_ack(const ConfigAck &m, std::uint8_t *out,
                              std::size_t out_cap) {
  if (out == nullptr || out_cap < kConfigAckSize) {
    return 0;
  }
  out[0] = m.key;
  out[1] = m.result;
  put_i32(out + 2, m.value);
  return kConfigAckSize;
}

bool decode_config_ack(const std::uint8_t *payload, std::size_t len,
                       ConfigAck &out) {
  if (payload == nullptr || len != kConfigAckSize) {
    return false;
  }
  out.key = payload[0];
  out.result = payload[1];
  out.value = get_i32(payload + 2);
  return true;
}

std::size_t encode_event(const Event &m, std::uint8_t *out,
                         std::size_t out_cap) {
  const std::size_t size = kEventMinSize + m.text_len;
  if (out == nullptr || m.text_len > kEventTextMax || out_cap < size) {
    return 0;
  }
  put_u16(out, m.code);
  std::memcpy(out + 2, m.text, m.text_len);
  return size;
}

bool decode_event(const std::uint8_t *payload, std::size_t len, Event &out) {
  if (payload == nullptr || len < kEventMinSize || len > kEventMaxSize) {
    return false;
  }
  out.code = get_u16(payload);
  out.text_len = static_cast<std::uint8_t>(len - kEventMinSize);
  std::memcpy(out.text, payload + 2, out.text_len);
  return true;
}

std::size_t encode_state(const State &m, std::uint8_t *out,
                         std::size_t out_cap) {
  if (out == nullptr || out_cap < kStateSize) {
    return 0;
  }
  out[0] = m.esp_state;
  out[1] = m.override_active;
  put_u32(out + 2, m.last_ctrl_seq);
  put_u32(out + 6, m.last_ctrl_t_send_us);
  put_i16(out + 10, m.roll);
  put_i16(out + 12, m.pitch);
  put_u16(out + 14, m.heading);
  put_u16(out + 16, m.vbat);
  put_i16(out + 18, m.flow_x);
  put_i16(out + 20, m.flow_y);
  out[22] = m.flow_quality;
  out[23] = m.flow_status;
  put_u16(out + 24, m.rx_rejected);
  put_u16(out + 26, m.msp_errors);
  return kStateSize;
}

bool decode_state(const std::uint8_t *payload, std::size_t len, State &out) {
  if (payload == nullptr || len != kStateSize) {
    return false;
  }
  out.esp_state = payload[0];
  out.override_active = payload[1];
  out.last_ctrl_seq = get_u32(payload + 2);
  out.last_ctrl_t_send_us = get_u32(payload + 6);
  out.roll = get_i16(payload + 10);
  out.pitch = get_i16(payload + 12);
  out.heading = get_u16(payload + 14);
  out.vbat = get_u16(payload + 16);
  out.flow_x = get_i16(payload + 18);
  out.flow_y = get_i16(payload + 20);
  out.flow_quality = payload[22];
  out.flow_status = payload[23];
  out.rx_rejected = get_u16(payload + 24);
  out.msp_errors = get_u16(payload + 26);
  return true;
}

PayloadCheck check_payload(std::uint8_t type, const std::uint8_t *payload,
                           std::size_t len) {
  std::size_t min = 0;
  std::size_t max = 0;
  switch (static_cast<MsgType>(type)) {
  case MsgType::kHello:
    min = max = kHelloSize;
    break;
  case MsgType::kHelloAck:
    min = max = kHelloAckSize;
    break;
  case MsgType::kArmControlRequest:
  case MsgType::kArmControlAck:
  case MsgType::kResumeAck:
  case MsgType::kBye:
    min = max = 1;
    break;
  case MsgType::kResumeRequest:
    break; // empty payload
  case MsgType::kConfigGet:
  case MsgType::kConfigSet:
    min = max = kConfigKvSize;
    break;
  case MsgType::kConfigAck:
    min = max = kConfigAckSize;
    break;
  case MsgType::kEvent:
    min = kEventMinSize;
    max = kEventMaxSize;
    break;
  case MsgType::kSetpoint:
    min = max = kSetpointSize;
    break;
  case MsgType::kState:
    min = max = kStateSize;
    break;
  default:
    return PayloadCheck::kUnknownType;
  }
  if (len < min || len > max || (len > 0 && payload == nullptr)) {
    return PayloadCheck::kBadSize;
  }
  if (static_cast<MsgType>(type) == MsgType::kSetpoint && payload[0] > 1) {
    return PayloadCheck::kBadMode;
  }
  return PayloadCheck::kOk;
}

} // namespace sd
