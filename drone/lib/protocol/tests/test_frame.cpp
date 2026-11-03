#include <gtest/gtest.h>

#include <cstring>
#include <random>
#include <string>
#include <vector>

#include "sd/frame.hpp"
#include "sd/setpoint.hpp"

namespace {

// Test key 00 01 .. 1f (spec §6). Never the real PSK.
sd::Key test_key() {
  sd::Key k{};
  for (std::size_t i = 0; i < sd::kKeySize; ++i) {
    k.bytes[i] = static_cast<std::uint8_t>(i);
  }
  return k;
}

std::vector<std::uint8_t> from_hex(const std::string &s) {
  std::vector<std::uint8_t> out;
  for (std::size_t i = 0; i + 1 < s.size(); i += 2) {
    out.push_back(static_cast<std::uint8_t>(std::stoi(s.substr(i, 2), nullptr, 16)));
  }
  return out;
}

constexpr std::uint32_t kSession = 0x12345678;

// Reference SETPOINT: mode 1, roll 100, pitch -200, yaw 0, throttle 500, seq 7, t_send 1000.
const char *const kSetpointBasic =
    "5344016000000a007856341207000000e803000001640038ff0000f40100fb703d824f5b589e";

std::vector<std::uint8_t> encode_reference() {
  const sd::Setpoint sp{1, 100, -200, 0, 500, 0};
  std::uint8_t payload[sd::kSetpointSize];
  sd::encode_setpoint(sp, payload, sizeof(payload));
  const sd::Header h{static_cast<std::uint8_t>(sd::MsgType::kSetpoint), 0, 0, kSession, 7, 1000};
  std::vector<std::uint8_t> out(sd::kMaxFrame);
  out.resize(sd::encode_frame(h, payload, sizeof(payload), test_key(), out.data(), out.size()));
  return out;
}

} // namespace

TEST(Frame, EncodesTheReferenceSetpointByteForByte) {
  EXPECT_EQ(encode_reference(), from_hex(kSetpointBasic));
}

TEST(Frame, DecodesTheReferenceSetpoint) {
  const auto bytes = from_hex(kSetpointBasic);
  sd::Frame f{};
  ASSERT_EQ(sd::decode_frame(bytes.data(), bytes.size(), test_key(), f), sd::RxError::kOk);
  EXPECT_EQ(f.header.type, 0x60);
  EXPECT_EQ(f.header.flags, 0);
  EXPECT_EQ(f.header.payload_len, sd::kSetpointSize);
  EXPECT_EQ(f.header.session_id, kSession);
  EXPECT_EQ(f.header.seq, 7U);
  EXPECT_EQ(f.header.t_send_us, 1000U);

  sd::Setpoint sp{};
  ASSERT_TRUE(sd::decode_setpoint(f.payload, f.header.payload_len, sp));
  EXPECT_EQ(sp.mode, 1);
  EXPECT_EQ(sp.roll, 100);
  EXPECT_EQ(sp.pitch, -200);
  EXPECT_EQ(sp.yaw_rate, 0);
  EXPECT_EQ(sp.throttle, 500);
}

TEST(Frame, EmptyPayloadRoundTrip) {
  const sd::Header h{static_cast<std::uint8_t>(sd::MsgType::kResumeRequest), 0, 0, kSession, 3, 0};
  std::uint8_t buf[sd::kMaxFrame];
  const std::size_t n = sd::encode_frame(h, nullptr, 0, test_key(), buf, sizeof(buf));
  ASSERT_EQ(n, sd::kHeaderSize + sd::kTagSize);
  sd::Frame f{};
  ASSERT_EQ(sd::decode_frame(buf, n, test_key(), f), sd::RxError::kOk);
  EXPECT_EQ(f.header.type, 0x05);
  EXPECT_EQ(f.header.payload_len, 0);
}

TEST(Frame, MaxPayloadRoundTripAndOneMoreRefused) {
  std::uint8_t payload[sd::kMaxPayload + 1] = {};
  const sd::Header h{static_cast<std::uint8_t>(sd::MsgType::kEvent), 0, 0, kSession, 1, 0};
  std::uint8_t buf[sd::kMaxFrame + 1];
  const std::size_t n = sd::encode_frame(h, payload, sd::kMaxPayload, test_key(), buf, sizeof(buf));
  ASSERT_EQ(n, sd::kMaxFrame);
  sd::Frame f{};
  EXPECT_EQ(sd::decode_frame(buf, n, test_key(), f), sd::RxError::kOk);
  EXPECT_EQ(sd::encode_frame(h, payload, sd::kMaxPayload + 1, test_key(), buf, sizeof(buf)), 0U);
}

TEST(Frame, EncodeRefusesSmallBuffer) {
  const sd::Header h{0x05, 0, 0, kSession, 1, 0};
  std::uint8_t buf[sd::kHeaderSize + sd::kTagSize - 1];
  EXPECT_EQ(sd::encode_frame(h, nullptr, 0, test_key(), buf, sizeof(buf)), 0U);
}

// Group 2 (HMAC): one flipped bit anywhere is rejected.
TEST(FrameReject, PayloadBitFlipped) {
  auto bytes = from_hex(kSetpointBasic);
  bytes[21] ^= 0x01; // roll low byte 0x64 -> 0x65
  sd::Frame f{};
  EXPECT_EQ(sd::decode_frame(bytes.data(), bytes.size(), test_key(), f), sd::RxError::kBadTag);
}

TEST(FrameReject, TagBitFlipped) {
  auto bytes = from_hex(kSetpointBasic);
  bytes.back() ^= 0x80;
  sd::Frame f{};
  EXPECT_EQ(sd::decode_frame(bytes.data(), bytes.size(), test_key(), f), sd::RxError::kBadTag);
}

TEST(FrameReject, WrongKey) {
  const auto bytes = from_hex(kSetpointBasic);
  sd::Key other = test_key();
  other.bytes[0] ^= 0x01;
  sd::Frame f{};
  EXPECT_EQ(sd::decode_frame(bytes.data(), bytes.size(), other, f), sd::RxError::kBadTag);
}

TEST(FrameReject, EveryHeaderOrPayloadBitIsCovered) {
  const auto good = from_hex(kSetpointBasic);
  for (std::size_t byte = 0; byte < good.size() - sd::kTagSize; ++byte) {
    for (int bit = 0; bit < 8; ++bit) {
      auto bytes = good;
      bytes[byte] ^= static_cast<std::uint8_t>(1U << bit);
      sd::Frame f{};
      EXPECT_NE(sd::decode_frame(bytes.data(), bytes.size(), test_key(), f), sd::RxError::kOk)
          << "byte " << byte << " bit " << bit;
    }
  }
}

// Group 3 (header): rejected before the tag is checked.
TEST(FrameReject, WrongMagicWithValidTag) {
  const auto bytes =
      from_hex("5345016000000a007856341207000000e803000001640038ff0000f40100de307f06c4dd38a3");
  sd::Frame f{};
  EXPECT_EQ(sd::decode_frame(bytes.data(), bytes.size(), test_key(), f), sd::RxError::kBadMagic);
}

TEST(FrameReject, WrongVersion) {
  auto bytes = from_hex(kSetpointBasic);
  bytes[2] = 2;
  sd::Frame f{};
  EXPECT_EQ(sd::decode_frame(bytes.data(), bytes.size(), test_key(), f), sd::RxError::kBadVersion);
}

TEST(FrameReject, PayloadLenOver256) {
  auto bytes = from_hex(kSetpointBasic);
  bytes[6] = 0x01;
  bytes[7] = 0x01; // 257
  sd::Frame f{};
  EXPECT_EQ(sd::decode_frame(bytes.data(), bytes.size(), test_key(), f), sd::RxError::kBadLength);
}

TEST(FrameReject, LengthMismatchExtraBytes) {
  auto bytes = from_hex(kSetpointBasic);
  bytes.push_back(0);
  sd::Frame f{};
  EXPECT_EQ(sd::decode_frame(bytes.data(), bytes.size(), test_key(), f), sd::RxError::kBadLength);
}

TEST(FrameReject, TruncatedFrame) {
  const auto bytes = from_hex(kSetpointBasic);
  sd::Frame f{};
  EXPECT_EQ(sd::decode_frame(bytes.data(), bytes.size() - 1, test_key(), f),
            sd::RxError::kTruncated);
  EXPECT_EQ(sd::decode_frame(bytes.data(), 10, test_key(), f), sd::RxError::kTruncated);
  EXPECT_EQ(sd::decode_frame(nullptr, 0, test_key(), f), sd::RxError::kTruncated);
}

TEST(FrameSize, ReadsTcpFrameLengthFromHeader) {
  const auto bytes = from_hex(kSetpointBasic);
  EXPECT_EQ(sd::frame_size(bytes.data(), sd::kHeaderSize), bytes.size());
  EXPECT_EQ(sd::frame_size(bytes.data(), sd::kHeaderSize - 1), 0U);
  EXPECT_EQ(sd::frame_size(nullptr, 0), 0U);
  auto big = bytes;
  big[7] = 0x02; // payload_len 0x020a > 256
  EXPECT_EQ(sd::frame_size(big.data(), big.size()), 0U);
}

// REQ-COM-005: malformed input never crashes. Fixed seed so the test is repeatable.
TEST(FrameFuzz, RandomBytesNeverCrashAndAreRejected) {
  std::mt19937 rng(97);
  std::uniform_int_distribution<int> len_dist(0, static_cast<int>(sd::kMaxFrame) + 16);
  std::uniform_int_distribution<int> byte_dist(0, 255);
  std::vector<std::uint8_t> buf(sd::kMaxFrame + 16);
  for (int i = 0; i < 20000; ++i) {
    const auto len = static_cast<std::size_t>(len_dist(rng));
    for (std::size_t j = 0; j < len; ++j) {
      buf[j] = static_cast<std::uint8_t>(byte_dist(rng));
    }
    if (len >= 3 && i % 2 == 0) { // half the cases get a valid magic and version
      buf[0] = sd::kMagic0;
      buf[1] = sd::kMagic1;
      buf[2] = sd::kWireVersion;
    }
    sd::Frame f{};
    EXPECT_NE(sd::decode_frame(buf.data(), len, test_key(), f), sd::RxError::kOk);
    (void)sd::frame_size(buf.data(), len);
  }
}

TEST(FrameFuzz, MutatedValidFramesAreRejected) {
  std::mt19937 rng(99);
  const auto good = encode_reference();
  std::uniform_int_distribution<std::size_t> pos(0, good.size() - 1);
  std::uniform_int_distribution<int> val(1, 255);
  for (int i = 0; i < 5000; ++i) {
    auto bytes = good;
    bytes[pos(rng)] ^= static_cast<std::uint8_t>(val(rng));
    sd::Frame f{};
    EXPECT_NE(sd::decode_frame(bytes.data(), bytes.size(), test_key(), f), sd::RxError::kOk);
  }
}
