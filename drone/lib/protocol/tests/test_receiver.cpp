#include <gtest/gtest.h>

#include <string>
#include <vector>

#include "sd/receiver.hpp"

namespace {

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
    out.push_back(
        static_cast<std::uint8_t>(std::stoi(s.substr(i, 2), nullptr, 16)));
  }
  return out;
}

constexpr std::uint32_t kSession = 0x12345678;

// From drone/tests/vectors/README.md: neutral setpoints with seq 1, 2, 3, and
// seq 7.
const char *const kSeq1 = "5344016000000a007856341201000000204e0000010000000000"
                          "000000006c870020e780aa97";
const char *const kSeq2 = "5344016000000a007856341202000000409c0000010000000000"
                          "00000000712720248f2ea86c";
const char *const kSeq3 = "5344016000000a00785634120300000060ea0000010000000000"
                          "0000000074f74adcb71e36f6";
const char *const kSeq7 = "5344016000000a007856341207000000e803000001640038ff00"
                          "00f40100fb703d824f5b589e";
// Same as kSeq7 but session 0x12345679, tag valid.
const char *const kOtherSession = "5344016000000a007956341207000000e80300000164"
                                  "0038ff0000f40100b6785492183eae61";

sd::RxResult feed(sd::Receiver &rx, const char *hex) {
  const auto bytes = from_hex(hex);
  sd::Frame f{};
  return rx.receive(bytes.data(), bytes.size(), f);
}

sd::Receiver open_receiver() {
  sd::Receiver rx;
  EXPECT_TRUE(rx.start_session(kSession, test_key()));
  return rx;
}

std::vector<std::uint8_t> encode(std::uint32_t session, std::uint32_t seq) {
  const sd::Header h{static_cast<std::uint8_t>(sd::MsgType::kResumeRequest),
                     0,
                     0,
                     session,
                     seq,
                     0};
  std::vector<std::uint8_t> out(sd::kMaxFrame);
  out.resize(
      sd::encode_frame(h, nullptr, 0, test_key(), out.data(), out.size()));
  return out;
}

} // namespace

TEST(Receiver, NothingIsAcceptedBeforeASession) {
  sd::Receiver rx;
  EXPECT_FALSE(rx.in_session());
  EXPECT_EQ(feed(rx, kSeq1), sd::RxResult::kNoSession);
  EXPECT_EQ(rx.counters().no_session, 1U);
  EXPECT_EQ(rx.counters().rejected, 1U);
}

TEST(Receiver, SessionIdZeroIsReservedForHello) {
  sd::Receiver rx;
  EXPECT_FALSE(rx.start_session(0, test_key()));
  EXPECT_FALSE(rx.in_session());
}

TEST(Receiver, AcceptsAValidFrameAndReturnsItsFields) {
  auto rx = open_receiver();
  const auto bytes = from_hex(kSeq7);
  sd::Frame f{};
  ASSERT_EQ(rx.receive(bytes.data(), bytes.size(), f),
            sd::RxResult::kAcceptedGap);
  EXPECT_EQ(f.header.type, 0x60);
  EXPECT_EQ(f.header.seq, 7U);
  EXPECT_EQ(rx.last_seq(), 7U);
  EXPECT_EQ(rx.counters().accepted, 1U);
}

// Vector group 4 (session).
TEST(Receiver, WrongSessionRejectedEvenWithAValidTag) {
  auto rx = open_receiver();
  EXPECT_EQ(feed(rx, kOtherSession), sd::RxResult::kWrongSession);
  EXPECT_EQ(rx.counters().wrong_session, 1U);
  EXPECT_EQ(rx.last_seq(), 0U); // a rejected frame never moves the window
}

TEST(Receiver, SessionIdZeroOutsideHelloRejected) {
  auto rx = open_receiver();
  const auto bytes = encode(0, 1);
  sd::Frame f{};
  EXPECT_EQ(rx.receive(bytes.data(), bytes.size(), f),
            sd::RxResult::kWrongSession);
}

// Vector group 5 (sequence): the seq_duplicate example from the README.
TEST(Receiver, SeqDuplicateExample) {
  auto rx = open_receiver();
  EXPECT_EQ(feed(rx, kSeq1), sd::RxResult::kAccepted);
  EXPECT_EQ(feed(rx, kSeq2), sd::RxResult::kAccepted);
  EXPECT_EQ(feed(rx, kSeq2), sd::RxResult::kDuplicateSeq);
  EXPECT_EQ(feed(rx, kSeq3), sd::RxResult::kAccepted);
  EXPECT_EQ(rx.last_seq(), 3U);
  EXPECT_EQ(rx.counters().rejected, 1U);
  EXPECT_EQ(rx.counters().duplicate_seq, 1U);
}

TEST(Receiver, OlderAndTooHighRejected) {
  auto rx = open_receiver();
  EXPECT_EQ(feed(rx, kSeq7), sd::RxResult::kAcceptedGap);
  EXPECT_EQ(feed(rx, kSeq3), sd::RxResult::kOldSeq);
  const auto high = encode(kSession, 0x80000000U);
  sd::Frame f{};
  EXPECT_EQ(rx.receive(high.data(), high.size(), f), sd::RxResult::kSeqTooHigh);
  EXPECT_EQ(rx.counters().old_seq, 1U);
  EXPECT_EQ(rx.counters().seq_too_high, 1U);
  EXPECT_EQ(rx.seq_counters().gaps, 1U);
}

TEST(Receiver, EveryFrameRejectIsCounted) {
  auto rx = open_receiver();
  auto bytes = from_hex(kSeq1);
  sd::Frame f{};

  EXPECT_EQ(rx.receive(bytes.data(), 5, f), sd::RxResult::kTruncated);
  auto bad = bytes;
  bad[0] = 0;
  EXPECT_EQ(rx.receive(bad.data(), bad.size(), f), sd::RxResult::kBadMagic);
  bad = bytes;
  bad[2] = 9;
  EXPECT_EQ(rx.receive(bad.data(), bad.size(), f), sd::RxResult::kBadVersion);
  bad = bytes;
  bad.push_back(0);
  EXPECT_EQ(rx.receive(bad.data(), bad.size(), f), sd::RxResult::kBadLength);
  bad = bytes;
  bad[25] ^= 1;
  EXPECT_EQ(rx.receive(bad.data(), bad.size(), f), sd::RxResult::kBadTag);

  const auto &c = rx.counters();
  EXPECT_EQ(c.truncated, 1U);
  EXPECT_EQ(c.bad_magic, 1U);
  EXPECT_EQ(c.bad_version, 1U);
  EXPECT_EQ(c.bad_length, 1U);
  EXPECT_EQ(c.bad_tag, 1U);
  EXPECT_EQ(c.rejected, 5U);
  EXPECT_EQ(c.accepted, 0U);

  // The good frame is still accepted afterwards: rejects leave no state behind.
  EXPECT_EQ(rx.receive(bytes.data(), bytes.size(), f), sd::RxResult::kAccepted);
}

TEST(Receiver, WrongKeyRejected) {
  sd::Receiver rx;
  sd::Key other = test_key();
  other.bytes[31] ^= 0xff;
  ASSERT_TRUE(rx.start_session(kSession, other));
  EXPECT_EQ(feed(rx, kSeq1), sd::RxResult::kBadTag);
}

TEST(Receiver, NewSessionResetsTheWindowAndEndSessionClosesIt) {
  auto rx = open_receiver();
  EXPECT_EQ(feed(rx, kSeq3), sd::RxResult::kAcceptedGap);
  ASSERT_TRUE(rx.start_session(kSession, test_key()));
  EXPECT_EQ(rx.last_seq(), 0U);
  EXPECT_EQ(feed(rx, kSeq1), sd::RxResult::kAccepted);

  rx.end_session();
  EXPECT_FALSE(rx.in_session());
  EXPECT_EQ(rx.session_id(), 0U);
  EXPECT_EQ(feed(rx, kSeq2), sd::RxResult::kNoSession);
}

TEST(Receiver, IsAccepted) {
  EXPECT_TRUE(sd::is_accepted(sd::RxResult::kAccepted));
  EXPECT_TRUE(sd::is_accepted(sd::RxResult::kAcceptedGap));
  EXPECT_FALSE(sd::is_accepted(sd::RxResult::kBadTag));
  EXPECT_FALSE(sd::is_accepted(sd::RxResult::kDuplicateSeq));
}
