#include <gtest/gtest.h>

#include <array>

#include "sd/setpoint.hpp"

namespace {

std::array<std::uint16_t, sd::kRcChannels> rc(std::int16_t roll, std::int16_t pitch,
                                               std::int16_t yaw, std::int16_t throttle) {
  const sd::Setpoint sp{1, roll, pitch, yaw, throttle, 0};
  std::array<std::uint16_t, sd::kRcChannels> ch{};
  sd::setpoint_to_rc(sp, ch.data());
  return ch;
}

} // namespace

TEST(Setpoint, RoundTrip) {
  const sd::Setpoint in{1, -1000, 1000, -1, 1000, 0};
  std::uint8_t buf[sd::kSetpointSize];
  ASSERT_EQ(sd::encode_setpoint(in, buf, sizeof(buf)), sd::kSetpointSize);
  sd::Setpoint out{};
  ASSERT_TRUE(sd::decode_setpoint(buf, sizeof(buf), out));
  EXPECT_EQ(out.mode, in.mode);
  EXPECT_EQ(out.roll, in.roll);
  EXPECT_EQ(out.pitch, in.pitch);
  EXPECT_EQ(out.yaw_rate, in.yaw_rate);
  EXPECT_EQ(out.throttle, in.throttle);
  EXPECT_EQ(out.flags, in.flags);
}

TEST(Setpoint, WrongSizesRefused) {
  std::uint8_t buf[sd::kSetpointSize + 1] = {};
  sd::Setpoint out{};
  EXPECT_FALSE(sd::decode_setpoint(buf, sd::kSetpointSize - 1, out));
  EXPECT_FALSE(sd::decode_setpoint(buf, sd::kSetpointSize + 1, out));
  EXPECT_FALSE(sd::decode_setpoint(nullptr, sd::kSetpointSize, out));
  EXPECT_EQ(sd::encode_setpoint(out, buf, sd::kSetpointSize - 1), 0U);
}

// Group 6 (clamps and mapping), spec §4 rule 4 and §5. Order: roll, pitch, throttle, yaw, aux1-4.
TEST(SetpointToRc, MapMin) { EXPECT_EQ(rc(-1000, -1000, -1000, 0)[0], 1000); }

TEST(SetpointToRc, MapCenter) {
  const auto ch = rc(0, 0, 0, 0);
  EXPECT_EQ(ch[0], 1500);
  EXPECT_EQ(ch[1], 1500);
  EXPECT_EQ(ch[2], 1000);
  EXPECT_EQ(ch[3], 1500);
}

TEST(SetpointToRc, MapMax) { EXPECT_EQ(rc(1000, 0, 0, 0)[0], 2000); }

TEST(SetpointToRc, ClampOver) {
  const auto ch = rc(1500, -32768, 32767, 500);
  EXPECT_EQ(ch[0], 2000);
  EXPECT_EQ(ch[1], 1000);
  EXPECT_EQ(ch[3], 2000);
  EXPECT_EQ(ch[2], 1500);
}

TEST(SetpointToRc, ThrottleMap) {
  EXPECT_EQ(rc(0, 0, 0, 0)[2], 1000);
  EXPECT_EQ(rc(0, 0, 0, 1000)[2], 2000);
  EXPECT_EQ(rc(0, 0, 0, -50)[2], 1000);
  EXPECT_EQ(rc(0, 0, 0, 1200)[2], 2000);
}

TEST(SetpointToRc, AuxAlwaysNeutral) {
  const auto ch = rc(1000, -1000, 1000, 1000);
  for (std::size_t i = 4; i < sd::kRcChannels; ++i) {
    EXPECT_EQ(ch[i], 1500) << "aux" << (i - 3);
  }
}

TEST(SetpointToRc, AxisOrderIsAetr) {
  const auto ch = rc(1000, -1000, 0, 1000);
  EXPECT_EQ(ch[0], 2000); // roll
  EXPECT_EQ(ch[1], 1000); // pitch
  EXPECT_EQ(ch[2], 2000); // throttle
  EXPECT_EQ(ch[3], 1500); // yaw
}
