#include <gtest/gtest.h>

#include "sd/version.hpp"

TEST(Version, MatchesProtocolSpecV0) { EXPECT_EQ(sd::protocol_version(), 0); }
