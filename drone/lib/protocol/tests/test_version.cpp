#include <gtest/gtest.h>

#include "sd/version.hpp"

TEST(Version, MatchesProtocolSpecV0HeaderVersion) {
  EXPECT_EQ(sd::protocol_version(), 1);
}
