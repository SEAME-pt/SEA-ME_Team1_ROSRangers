#include "sd/frame.hpp"

#include <cstring>

#include "le.hpp"

namespace sd {
namespace {

void compute_tag(const std::uint8_t *frame, std::size_t len, const Key &key,
                 std::uint8_t tag[kTagSize]) {
  std::uint8_t mac[kSha256Size];
  hmac_sha256(key.bytes, kKeySize, frame, len, mac);
  std::memcpy(tag, mac, kTagSize);
}

} // namespace

std::size_t encode_frame(const Header &header, const std::uint8_t *payload,
                         std::size_t payload_len, const Key &key,
                         std::uint8_t *out, std::size_t out_cap) {
  if (payload_len > kMaxPayload || out == nullptr ||
      (payload == nullptr && payload_len > 0)) {
    return 0;
  }
  const std::size_t total = kHeaderSize + payload_len + kTagSize;
  if (out_cap < total) {
    return 0;
  }
  out[0] = kMagic0;
  out[1] = kMagic1;
  out[2] = kWireVersion;
  out[3] = header.type;
  out[4] = header.flags;
  out[5] = 0; // reserved
  put_u16(out + 6, static_cast<std::uint16_t>(payload_len));
  put_u32(out + 8, header.session_id);
  put_u32(out + 12, header.seq);
  put_u32(out + 16, header.t_send_us);
  if (payload_len > 0) {
    std::memcpy(out + kHeaderSize, payload, payload_len);
  }
  compute_tag(out, kHeaderSize + payload_len, key,
              out + kHeaderSize + payload_len);
  return total;
}

RxError decode_frame(const std::uint8_t *buf, std::size_t len, const Key &key,
                     Frame &out) {
  if (buf == nullptr || len < kHeaderSize + kTagSize) {
    return RxError::kTruncated;
  }
  if (buf[0] != kMagic0 || buf[1] != kMagic1) {
    return RxError::kBadMagic;
  }
  if (buf[2] != kWireVersion) {
    return RxError::kBadVersion;
  }
  const std::size_t payload_len = get_u16(buf + 6);
  if (payload_len > kMaxPayload) {
    return RxError::kBadLength;
  }
  const std::size_t total = kHeaderSize + payload_len + kTagSize;
  if (len < total) {
    return RxError::kTruncated;
  }
  if (len > total) {
    return RxError::kBadLength;
  }
  std::uint8_t tag[kTagSize];
  compute_tag(buf, kHeaderSize + payload_len, key, tag);
  if (!equal_constant_time(tag, buf + kHeaderSize + payload_len, kTagSize)) {
    return RxError::kBadTag;
  }
  out.header.type = buf[3];
  out.header.flags = buf[4];
  out.header.payload_len = static_cast<std::uint16_t>(payload_len);
  out.header.session_id = get_u32(buf + 8);
  out.header.seq = get_u32(buf + 12);
  out.header.t_send_us = get_u32(buf + 16);
  out.payload = buf + kHeaderSize;
  return RxError::kOk;
}

std::size_t frame_size(const std::uint8_t *buf, std::size_t len) {
  if (buf == nullptr || len < kHeaderSize) {
    return 0;
  }
  const std::size_t payload_len = get_u16(buf + 6);
  if (payload_len > kMaxPayload) {
    return 0;
  }
  return kHeaderSize + payload_len + kTagSize;
}

} // namespace sd
