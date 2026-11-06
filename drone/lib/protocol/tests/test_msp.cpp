#include <gtest/gtest.h>

#include <cstring>
#include <vector>

#include "sd/msp.hpp"

namespace {

using Bytes = std::vector<std::uint8_t>;

// A reply frame as the FC sends it: '$' 'M' dir len cmd payload checksum.
Bytes reply_frame(std::uint8_t cmd, const Bytes &payload, char dir = '>') {
  Bytes out = {'$', 'M', static_cast<std::uint8_t>(dir),
               static_cast<std::uint8_t>(payload.size()), cmd};
  std::uint8_t checksum = static_cast<std::uint8_t>(payload.size()) ^ cmd;
  for (const std::uint8_t b : payload) {
    out.push_back(b);
    checksum ^= b;
  }
  out.push_back(checksum);
  return out;
}

// Feeds every byte; returns the events that were not kNone.
std::vector<sd::msp::Parser::Event> feed(sd::msp::Parser &p, const Bytes &in) {
  std::vector<sd::msp::Parser::Event> events;
  for (const std::uint8_t b : in) {
    const auto e = p.feed(b);
    if (e != sd::msp::Parser::Event::kNone) {
      events.push_back(e);
    }
  }
  return events;
}

// MSP_STATUS payload laid out as Betaflight 2026.6.2 writes it (msp.c):
// cycle u16, i2c u16, sensors u16, flight mode flags u32, profile u8, load u16,
// gyro cycle u16, n u8, n extra bytes, flag count u8, arming flags u32, ...
Bytes status_payload(bool armed, std::uint32_t arming_flags,
                     std::uint8_t extra = 1) {
  Bytes p = {0x7D, 0x00, 0, 0, 0x23, 0x00};
  p.insert(p.end(), {static_cast<std::uint8_t>(armed ? 0x01 : 0x00), 0, 0, 0});
  p.insert(p.end(), {0, 0x10, 0x00, 0, 0});
  p.push_back(extra);
  for (std::uint8_t i = 0; i < extra; ++i) {
    p.push_back(0xAA);
  }
  p.push_back(30); // ARMING_DISABLE_FLAGS_COUNT
  for (int i = 0; i < 4; ++i) {
    p.push_back(static_cast<std::uint8_t>(arming_flags >> (8 * i)));
  }
  p.insert(p.end(), {0, 0x28, 0x00, 4, 3, 0});
  return p;
}

} // namespace

TEST(Msp, KnownRequestBytes) {
  std::uint8_t out[sd::msp::kMaxFrame];
  ASSERT_EQ(sd::msp::encode_request(sd::msp::kApiVersion, nullptr, 0, out,
                                    sizeof(out)),
            6U);
  EXPECT_EQ(Bytes(out, out + 6), (Bytes{'$', 'M', '<', 0x00, 0x01, 0x01}));
  ASSERT_EQ(
      sd::msp::encode_request(sd::msp::kStatus, nullptr, 0, out, sizeof(out)),
      6U);
  EXPECT_EQ(Bytes(out, out + 6), (Bytes{'$', 'M', '<', 0x00, 0x65, 0x65}));
}

TEST(Msp, AllowListRefusesEverythingElse) {
  std::uint8_t out[sd::msp::kMaxFrame];
  for (int cmd = 0; cmd < 256; ++cmd) {
    const bool ok = cmd == 1 || cmd == 101 || cmd == 105 || cmd == 108 ||
                    cmd == 110 || cmd == 200;
    EXPECT_EQ(sd::msp::allowed(static_cast<std::uint8_t>(cmd)), ok) << cmd;
    const std::size_t n = sd::msp::encode_request(
        static_cast<std::uint8_t>(cmd), nullptr, 0, out, sizeof(out));
    // 200 is allowed, but only through encode_set_raw_rc().
    EXPECT_EQ(n != 0, ok && cmd != sd::msp::kSetRawRc) << cmd;
  }
  // The dangerous ones by name (MSP Cheat Sheet): motors, EEPROM, reboot,
  // arming disable (ESP-only, ADR-024, not on this list yet).
  for (const int cmd : {214, 250, 68, 99}) {
    EXPECT_FALSE(sd::msp::allowed(static_cast<std::uint8_t>(cmd))) << cmd;
  }
}

TEST(Msp, EncodeRefusesBadArguments) {
  std::uint8_t out[sd::msp::kMaxFrame];
  const std::uint8_t one = 1;
  EXPECT_EQ(sd::msp::encode_request(sd::msp::kRc, nullptr, 0, nullptr, 64), 0U);
  EXPECT_EQ(sd::msp::encode_request(sd::msp::kRc, nullptr, 1, out, sizeof(out)),
            0U);
  EXPECT_EQ(sd::msp::encode_request(sd::msp::kRc, &one, 1, out, 6), 0U);
  EXPECT_EQ(sd::msp::encode_request(sd::msp::kRc, &one,
                                    sd::msp::kMaxPayload + 1, out, sizeof(out)),
            0U);
  EXPECT_EQ(sd::msp::encode_set_raw_rc(nullptr, out, sizeof(out)), 0U);
}

TEST(Msp, SetRawRcBytes) {
  const std::uint16_t ch[sd::msp::kRcChannels] = {1500, 1600, 1000, 1500,
                                                  1500, 1500, 1500, 1500};
  std::uint8_t out[sd::msp::kMaxFrame];
  ASSERT_EQ(sd::msp::encode_set_raw_rc(ch, out, sizeof(out)), 22U);
  EXPECT_EQ(out[3], 16);
  EXPECT_EQ(out[4], 200);
  EXPECT_EQ(out[5], 0xDC); // 1500 little-endian
  EXPECT_EQ(out[6], 0x05);
  EXPECT_EQ(out[7], 0x40); // 1600
  EXPECT_EQ(out[8], 0x06);
  std::uint8_t checksum = 0;
  for (int i = 3; i < 21; ++i) {
    checksum ^= out[i];
  }
  EXPECT_EQ(out[21], checksum);
}

TEST(MspParser, ReadsRepliesAndSkipsNoise) {
  sd::msp::Parser p;
  Bytes in = {0x00, 'x', '$', 'q'}; // noise and a false start
  const Bytes attitude =
      reply_frame(sd::msp::kAttitude, {0x9C, 0xFF, 0x2C, 0x01, 0x67, 0x01});
  in.insert(in.end(), attitude.begin(), attitude.end());
  ASSERT_EQ(feed(p, in), (std::vector<sd::msp::Parser::Event>{
                             sd::msp::Parser::Event::kReply}));
  sd::msp::Attitude a{};
  ASSERT_TRUE(sd::msp::decode_attitude(p.reply(), a));
  EXPECT_EQ(a.roll, -100);
  EXPECT_EQ(a.pitch, 300);
  EXPECT_EQ(a.heading, 359);
}

TEST(MspParser, BadChecksumTooLongAndRequestsAreDropped) {
  sd::msp::Parser p;
  Bytes bad = reply_frame(sd::msp::kAttitude, {1, 2, 3, 4, 5, 6});
  bad.back() ^= 0xFF;
  EXPECT_EQ(feed(p, bad), (std::vector<sd::msp::Parser::Event>{
                              sd::msp::Parser::Event::kBadChecksum}));

  // A 100-byte reply is longer than kMaxPayload: skipped as a whole, even if
  // its payload is full of '$M>' look-alikes, then the next reply is read.
  Bytes big = reply_frame(sd::msp::kStatus, Bytes(100, '$'));
  const Bytes next = reply_frame(sd::msp::kAnalog, Bytes(9, 0));
  big.insert(big.end(), next.begin(), next.end());
  EXPECT_EQ(feed(p, big), (std::vector<sd::msp::Parser::Event>{
                              sd::msp::Parser::Event::kTooLong,
                              sd::msp::Parser::Event::kReply}));
  EXPECT_EQ(p.reply().cmd, sd::msp::kAnalog);

  // A request echoed back ('<') is not a reply.
  std::uint8_t req[sd::msp::kMaxFrame];
  const std::size_t n =
      sd::msp::encode_request(sd::msp::kStatus, nullptr, 0, req, sizeof(req));
  EXPECT_TRUE(feed(p, Bytes(req, req + n)).empty());
}

TEST(MspParser, ErrorReplyAndEmptyReply) {
  sd::msp::Parser p;
  EXPECT_EQ(feed(p, reply_frame(sd::msp::kRc, {}, '!')).size(), 1U);
  EXPECT_TRUE(p.reply().error);
  EXPECT_EQ(p.reply().len, 0);
  sd::msp::Attitude a{};
  EXPECT_FALSE(sd::msp::decode_attitude(p.reply(), a));
  EXPECT_EQ(feed(p, reply_frame(sd::msp::kSetRawRc, {})).size(), 1U);
  EXPECT_FALSE(p.reply().error);
  EXPECT_EQ(p.reply().cmd, sd::msp::kSetRawRc);
}

TEST(MspParser, RandomBytesNeverCrashAndAlwaysResync) {
  sd::msp::Parser p;
  std::uint32_t x = 12345;
  for (int i = 0; i < 200000; ++i) {
    x = x * 1103515245U + 12345U;
    p.feed(static_cast<std::uint8_t>(x >> 16));
  }
  // Whatever state the noise left it in, a valid reply is read within the
  // longest possible skip (len 255 + checksum) plus the frame itself.
  Bytes in(260, 0);
  const Bytes ok = reply_frame(sd::msp::kAttitude, Bytes(6, 0));
  in.insert(in.end(), ok.begin(), ok.end());
  const auto events = feed(p, in);
  ASSERT_FALSE(events.empty());
  EXPECT_EQ(events.back(), sd::msp::Parser::Event::kReply);
  EXPECT_EQ(p.reply().cmd, sd::msp::kAttitude);
}

TEST(MspDecode, StatusArmedAndArmingFlags) {
  sd::msp::Parser p;
  feed(p, reply_frame(sd::msp::kStatus,
                      status_payload(true, sd::msp::kArmingDisabledMsp)));
  sd::msp::Status s{};
  ASSERT_TRUE(sd::msp::decode_status(p.reply(), s));
  EXPECT_TRUE(s.armed);
  EXPECT_EQ(s.arming_disable_flags, sd::msp::kArmingDisabledMsp);

  // No extra flight mode bytes, disarmed, other flags.
  feed(p, reply_frame(sd::msp::kStatus, status_payload(false, 0x5, 0)));
  ASSERT_TRUE(sd::msp::decode_status(p.reply(), s));
  EXPECT_FALSE(s.armed);
  EXPECT_EQ(s.arming_disable_flags, 0x5U);

  // Too short for the arming flags its own header promises.
  Bytes cut = status_payload(true, 0, 3);
  cut.resize(18);
  feed(p, reply_frame(sd::msp::kStatus, cut));
  EXPECT_FALSE(sd::msp::decode_status(p.reply(), s));
}

TEST(MspDecode, AnalogTakesTheCentivoltField) {
  sd::msp::Parser p;
  // legacy 0.1 V = 111, mAh, rssi, amps, then 1110 (11.10 V) little-endian.
  feed(p, reply_frame(sd::msp::kAnalog, {111, 0, 0, 0, 0, 0, 0, 0x56, 0x04}));
  sd::msp::Analog a{};
  ASSERT_TRUE(sd::msp::decode_analog(p.reply(), a));
  EXPECT_EQ(a.vbat, 1110);
  feed(p, reply_frame(sd::msp::kAnalog, Bytes(7, 0))); // old 7-byte reply
  EXPECT_FALSE(sd::msp::decode_analog(p.reply(), a));
  feed(p, reply_frame(sd::msp::kAttitude, Bytes(9, 0)));
  EXPECT_FALSE(sd::msp::decode_analog(p.reply(), a));
}

// #160 review: the one write command can only be built typed, with exactly 8
// channels; encode_request() refuses it with any payload.
TEST(Msp, SetRawRcOnlyThroughTheTypedEncoder) {
  std::uint8_t out[sd::msp::kMaxFrame];
  const std::uint8_t payload[sd::msp::kMaxPayload] = {};
  for (std::size_t len = 0; len <= sd::msp::kMaxPayload; ++len) {
    EXPECT_EQ(sd::msp::encode_request(sd::msp::kSetRawRc, payload, len, out,
                                      sizeof(out)),
              0U)
        << len;
  }
  const std::uint16_t ch[sd::msp::kRcChannels] = {1500, 1500, 1000, 1500,
                                                  1500, 1500, 1500, 1500};
  EXPECT_EQ(sd::msp::encode_set_raw_rc(ch, out, sizeof(out)), 22U);
  EXPECT_EQ(sd::msp::encode_set_raw_rc(ch, out, 21), 0U);
  EXPECT_EQ(sd::msp::encode_set_raw_rc(ch, nullptr, sizeof(out)), 0U);
}
