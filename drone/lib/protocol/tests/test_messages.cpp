#include <gtest/gtest.h>

#include <cstring>
#include <vector>

#include "sd/messages.hpp"

namespace {

std::vector<std::uint8_t> bytes(const std::uint8_t *p, std::size_t n) {
  return {p, p + n};
}

} // namespace

TEST(Hello, BytesAndRoundTrip) {
  sd::Hello in{1, {}};
  for (std::size_t i = 0; i < sd::kNonceSize; ++i) {
    in.ground_nonce[i] = static_cast<std::uint8_t>(0xA0 + i);
  }
  std::uint8_t buf[sd::kHelloSize];
  ASSERT_EQ(sd::encode_hello(in, buf, sizeof(buf)), sd::kHelloSize);
  EXPECT_EQ(buf[0], 1);
  EXPECT_EQ(buf[1], 0xA0);
  EXPECT_EQ(buf[16], 0xAF);
  sd::Hello out{};
  ASSERT_TRUE(sd::decode_hello(buf, sizeof(buf), out));
  EXPECT_EQ(out.proto, 1);
  EXPECT_EQ(0, std::memcmp(out.ground_nonce, in.ground_nonce, sd::kNonceSize));
}

TEST(HelloAck, BytesAndRoundTrip) {
  sd::HelloAck in{0x11223344, {}, {'v', '0', '.', '1'}};
  in.esp_nonce[0] = 0xEE;
  in.esp_nonce[15] = 0xFF;
  std::uint8_t buf[sd::kHelloAckSize];
  ASSERT_EQ(sd::encode_hello_ack(in, buf, sizeof(buf)), sd::kHelloAckSize);
  EXPECT_EQ(bytes(buf, 4), (std::vector<std::uint8_t>{0x44, 0x33, 0x22, 0x11}));
  EXPECT_EQ(buf[4], 0xEE);
  EXPECT_EQ(buf[19], 0xFF);
  EXPECT_EQ(buf[20], 'v');
  EXPECT_EQ(buf[31], 0);
  sd::HelloAck out{};
  ASSERT_TRUE(sd::decode_hello_ack(buf, sizeof(buf), out));
  EXPECT_EQ(out.session_id, in.session_id);
  EXPECT_EQ(0, std::memcmp(out.esp_nonce, in.esp_nonce, sd::kNonceSize));
  EXPECT_EQ(0, std::memcmp(out.fw_version, in.fw_version, sd::kFwVersionSize));
}

TEST(U8Payload, RoundTrip) {
  std::uint8_t buf[1];
  ASSERT_EQ(sd::encode_u8_payload(
                static_cast<std::uint8_t>(sd::ByeReason::kReplaced), buf, 1),
            1U);
  std::uint8_t out = 0;
  ASSERT_TRUE(sd::decode_u8_payload(buf, 1, out));
  EXPECT_EQ(out, 3);
}

TEST(ConfigKv, BytesAndRoundTrip) {
  const sd::ConfigKv in{0x80, -2};
  std::uint8_t buf[sd::kConfigKvSize];
  ASSERT_EQ(sd::encode_config_kv(in, buf, sizeof(buf)), sd::kConfigKvSize);
  EXPECT_EQ(bytes(buf, sizeof(buf)),
            (std::vector<std::uint8_t>{0x80, 0xFE, 0xFF, 0xFF, 0xFF}));
  sd::ConfigKv out{};
  ASSERT_TRUE(sd::decode_config_kv(buf, sizeof(buf), out));
  EXPECT_EQ(out.key, 0x80);
  EXPECT_EQ(out.value, -2);
}

TEST(ConfigAck, BytesAndRoundTrip) {
  const sd::ConfigAck in{
      0x40, static_cast<std::uint8_t>(sd::ConfigResult::kReadOnly), 250};
  std::uint8_t buf[sd::kConfigAckSize];
  ASSERT_EQ(sd::encode_config_ack(in, buf, sizeof(buf)), sd::kConfigAckSize);
  EXPECT_EQ(bytes(buf, sizeof(buf)),
            (std::vector<std::uint8_t>{0x40, 2, 0xFA, 0, 0, 0}));
  sd::ConfigAck out{};
  ASSERT_TRUE(sd::decode_config_ack(buf, sizeof(buf), out));
  EXPECT_EQ(out.key, 0x40);
  EXPECT_EQ(out.result, 2);
  EXPECT_EQ(out.value, 250);
}

TEST(Event, EmptyAndFullText) {
  sd::Event in{
      static_cast<std::uint16_t>(sd::EventCode::kSetpointStale), 0, {}};
  std::uint8_t buf[sd::kEventMaxSize];
  ASSERT_EQ(sd::encode_event(in, buf, sizeof(buf)), sd::kEventMinSize);
  EXPECT_EQ(bytes(buf, 2), (std::vector<std::uint8_t>{0x01, 0x01}));

  in.text_len = sd::kEventTextMax;
  std::memset(in.text, 'x', sd::kEventTextMax);
  ASSERT_EQ(sd::encode_event(in, buf, sizeof(buf)), sd::kEventMaxSize);
  sd::Event out{};
  ASSERT_TRUE(sd::decode_event(buf, sd::kEventMaxSize, out));
  EXPECT_EQ(out.code, 0x0101);
  EXPECT_EQ(out.text_len, sd::kEventTextMax);
  EXPECT_EQ(0, std::memcmp(out.text, in.text, sd::kEventTextMax));
}

TEST(Event, TooLongRefused) {
  sd::Event in{0, sd::kEventTextMax + 1, {}};
  std::uint8_t buf[sd::kEventMaxSize + 1] = {};
  EXPECT_EQ(sd::encode_event(in, buf, sizeof(buf)), 0U);
  sd::Event out{};
  EXPECT_FALSE(sd::decode_event(buf, sd::kEventMaxSize + 1, out));
  EXPECT_FALSE(sd::decode_event(buf, sd::kEventMinSize - 1, out));
  in.text_len = 4;
  EXPECT_EQ(sd::encode_event(in, buf, sd::kEventMinSize + 3), 0U);
}

TEST(State, BytesAndRoundTrip) {
  const sd::State in{static_cast<std::uint8_t>(sd::EspState::kStreaming),
                     1,
                     0x01020304,
                     0x05060708,
                     -1,
                     900,
                     359,
                     1110,
                     -300,
                     300,
                     200,
                     0,
                     7,
                     0xBEEF};
  std::uint8_t buf[sd::kStateSize];
  ASSERT_EQ(sd::encode_state(in, buf, sizeof(buf)), sd::kStateSize);
  const std::vector<std::uint8_t> want{
      3,    1,    0x04, 0x03, 0x02, 0x01, 0x08, 0x07, 0x06, 0x05,
      0xFF, 0xFF, 0x84, 0x03, 0x67, 0x01, 0x56, 0x04, 0xD4, 0xFE,
      0x2C, 0x01, 200,  0,    7,    0,    0xEF, 0xBE};
  EXPECT_EQ(bytes(buf, sizeof(buf)), want);
  sd::State out{};
  ASSERT_TRUE(sd::decode_state(buf, sizeof(buf), out));
  EXPECT_EQ(out.esp_state, in.esp_state);
  EXPECT_EQ(out.last_ctrl_seq, in.last_ctrl_seq);
  EXPECT_EQ(out.last_ctrl_t_send_us, in.last_ctrl_t_send_us);
  EXPECT_EQ(out.roll, in.roll);
  EXPECT_EQ(out.heading, in.heading);
  EXPECT_EQ(out.vbat, in.vbat);
  EXPECT_EQ(out.flow_x, in.flow_x);
  EXPECT_EQ(out.flow_y, in.flow_y);
  EXPECT_EQ(out.flow_quality, in.flow_quality);
  EXPECT_EQ(out.rx_rejected, in.rx_rejected);
  EXPECT_EQ(out.msp_errors, in.msp_errors);
}

// Every fixed-size decoder refuses one byte short, one byte long and nullptr;
// every encoder refuses a buffer one byte short.
TEST(Messages, WrongSizesRefused) {
  std::uint8_t buf[64] = {};
  sd::Hello h{};
  sd::HelloAck ha{};
  sd::ConfigKv kv{};
  sd::ConfigAck ca{};
  sd::State st{};
  std::uint8_t b = 0;
  for (const int d : {-1, 1}) {
    EXPECT_FALSE(sd::decode_hello(buf, sd::kHelloSize + d, h));
    EXPECT_FALSE(sd::decode_hello_ack(buf, sd::kHelloAckSize + d, ha));
    EXPECT_FALSE(sd::decode_u8_payload(buf, 1 + d, b));
    EXPECT_FALSE(sd::decode_config_kv(buf, sd::kConfigKvSize + d, kv));
    EXPECT_FALSE(sd::decode_config_ack(buf, sd::kConfigAckSize + d, ca));
    EXPECT_FALSE(sd::decode_state(buf, sd::kStateSize + d, st));
  }
  sd::Event ev{};
  EXPECT_FALSE(sd::decode_hello(nullptr, sd::kHelloSize, h));
  EXPECT_FALSE(sd::decode_hello_ack(nullptr, sd::kHelloAckSize, ha));
  EXPECT_FALSE(sd::decode_u8_payload(nullptr, 1, b));
  EXPECT_FALSE(sd::decode_config_kv(nullptr, sd::kConfigKvSize, kv));
  EXPECT_FALSE(sd::decode_config_ack(nullptr, sd::kConfigAckSize, ca));
  EXPECT_FALSE(sd::decode_event(nullptr, sd::kEventMinSize, ev));
  EXPECT_FALSE(sd::decode_state(nullptr, sd::kStateSize, st));

  EXPECT_EQ(sd::encode_hello(h, buf, sd::kHelloSize - 1), 0U);
  EXPECT_EQ(sd::encode_hello_ack(ha, buf, sd::kHelloAckSize - 1), 0U);
  EXPECT_EQ(sd::encode_u8_payload(0, buf, 0), 0U);
  EXPECT_EQ(sd::encode_config_kv(kv, buf, sd::kConfigKvSize - 1), 0U);
  EXPECT_EQ(sd::encode_config_ack(ca, buf, sd::kConfigAckSize - 1), 0U);
  EXPECT_EQ(sd::encode_event(ev, buf, sd::kEventMinSize - 1), 0U);
  EXPECT_EQ(sd::encode_state(st, buf, sd::kStateSize - 1), 0U);
}
