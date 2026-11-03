#include <gtest/gtest.h>

#include "sd/seq_window.hpp"

using sd::SeqResult;

TEST(SeqWindow, InOrder) {
  sd::SeqWindow w;
  EXPECT_EQ(w.accept(1), SeqResult::kAccepted);
  EXPECT_EQ(w.accept(2), SeqResult::kAccepted);
  EXPECT_EQ(w.accept(3), SeqResult::kAccepted);
  EXPECT_EQ(w.last(), 3U);
  EXPECT_EQ(w.counters().gaps, 0U);
}

TEST(SeqWindow, DuplicateRejectedAndCounted) {
  sd::SeqWindow w;
  w.accept(1);
  w.accept(2);
  EXPECT_EQ(w.accept(2), SeqResult::kDuplicate);
  EXPECT_EQ(w.accept(3), SeqResult::kAccepted);
  EXPECT_EQ(w.last(), 3U);
  EXPECT_EQ(w.counters().duplicates, 1U);
}

TEST(SeqWindow, OlderRejectedAndCounted) {
  sd::SeqWindow w;
  w.accept(1);
  w.accept(5);
  EXPECT_EQ(w.accept(3), SeqResult::kOld);
  EXPECT_EQ(w.last(), 5U);
  EXPECT_EQ(w.counters().old, 1U);
}

TEST(SeqWindow, GapAcceptedAndCounted) {
  sd::SeqWindow w;
  w.accept(1);
  w.accept(2);
  EXPECT_EQ(w.accept(10), SeqResult::kAcceptedGap);
  EXPECT_EQ(w.last(), 10U);
  EXPECT_EQ(w.counters().gaps, 1U);
  EXPECT_EQ(w.counters().missing, 7U);
}

TEST(SeqWindow, SeqZeroIsNeverAccepted) {
  sd::SeqWindow w;
  EXPECT_EQ(w.accept(0), SeqResult::kOld);
  EXPECT_EQ(w.last(), 0U);
}

TEST(SeqWindow, TooHighNeedsNewSession) {
  sd::SeqWindow w;
  w.accept(1);
  EXPECT_EQ(w.accept(0x7fffffffU), SeqResult::kAcceptedGap);
  EXPECT_EQ(w.accept(0x80000000U), SeqResult::kNeedNewSession);
  EXPECT_EQ(w.accept(0xffffffffU), SeqResult::kNeedNewSession);
  EXPECT_EQ(w.last(), 0x7fffffffU);
  EXPECT_EQ(w.counters().too_high, 2U);
}

TEST(SeqWindow, ResetStartsANewSession) {
  sd::SeqWindow w;
  w.accept(50);
  w.accept(50);
  w.reset();
  EXPECT_EQ(w.last(), 0U);
  EXPECT_EQ(w.counters().duplicates, 0U);
  EXPECT_EQ(w.accept(1), SeqResult::kAccepted);
}
