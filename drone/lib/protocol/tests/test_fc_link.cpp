#include <gtest/gtest.h>

#include <vector>

#include "sd/fc_link.hpp"

namespace {

using Bytes = std::vector<std::uint8_t>;

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

Bytes status_payload(bool armed) {
  Bytes p = {0x7D, 0,    0,  0, 0x23, 0, static_cast<std::uint8_t>(armed),
             0,    0,    0,  0, 0x10, 0, 0,
             0,    0,    30, 0, 0,    0, 0,
             0,    0x28, 0,  4, 3,    0};
  return p;
}

// The command of the request written by poll(), or -1 for none.
int poll_cmd(sd::FcLink &fc, std::uint32_t now) {
  std::uint8_t out[sd::msp::kMaxFrame];
  const std::size_t n = fc.poll(now, out, sizeof(out));
  if (n == 0) {
    return -1;
  }
  EXPECT_EQ(n, 6U);
  EXPECT_EQ(out[2], '<');
  return out[4];
}

void answer(sd::FcLink &fc, const Bytes &frame, std::uint32_t now) {
  for (const std::uint8_t b : frame) {
    fc.on_byte(b, now);
  }
}

// Answers every request at once with a valid reply, from t = 0 to `until`.
void run_ideal_fc(sd::FcLink &fc, std::uint32_t until,
                  std::vector<int> *sent = nullptr) {
  for (std::uint32_t t = 0; t <= until; ++t) {
    const int cmd = poll_cmd(fc, t);
    if (cmd < 0) {
      continue;
    }
    if (sent != nullptr) {
      sent->push_back(cmd);
    }
    if (cmd == sd::msp::kAttitude) {
      answer(fc, reply_frame(sd::msp::kAttitude, Bytes(6, 0)), t);
    } else if (cmd == sd::msp::kStatus) {
      answer(fc, reply_frame(sd::msp::kStatus, status_payload(false)), t);
    } else if (cmd == sd::msp::kAnalog) {
      answer(fc, reply_frame(sd::msp::kAnalog, Bytes(9, 0)), t);
    }
  }
}

} // namespace

TEST(FcLink, PollsAtTheAdr009Rates) {
  sd::FcLink fc;
  std::vector<int> sent;
  run_ideal_fc(fc, 999, &sent); // one second
  int attitude = 0;
  int status = 0;
  int analog = 0;
  for (const int c : sent) {
    attitude += c == sd::msp::kAttitude;
    status += c == sd::msp::kStatus;
    analog += c == sd::msp::kAnalog;
  }
  EXPECT_EQ(attitude, 25);
  EXPECT_EQ(status, 5);
  EXPECT_EQ(analog, 5);
  EXPECT_EQ(fc.counters().replies, fc.counters().requests);
  EXPECT_EQ(fc.counters().timeouts, 0U);
  EXPECT_TRUE(fc.telemetry().has_attitude);
  EXPECT_TRUE(fc.telemetry().has_status);
  EXPECT_TRUE(fc.telemetry().has_analog);
}

TEST(FcLink, OnlyPollRequestsEverLeave) {
  // #101: the gateway alone never sends RC or anything outside the polls.
  sd::FcLink fc;
  std::vector<int> sent;
  run_ideal_fc(fc, 5000, &sent);
  for (const int c : sent) {
    EXPECT_TRUE(c == sd::msp::kAttitude || c == sd::msp::kStatus ||
                c == sd::msp::kAnalog)
        << c;
  }
}

TEST(FcLink, OneRequestAtATimeAndTimeouts) {
  sd::FcLink fc;
  ASSERT_EQ(poll_cmd(fc, 0), sd::msp::kAttitude);
  // No reply: nothing else is sent until the timeout.
  for (std::uint32_t t = 1; t < sd::kReplyTimeoutMs; ++t) {
    EXPECT_EQ(poll_cmd(fc, t), -1) << t;
  }
  EXPECT_NE(poll_cmd(fc, sd::kReplyTimeoutMs), -1);
  EXPECT_EQ(fc.counters().timeouts, 1U);
  // A late reply to the timed-out request is not taken as the answer.
  answer(fc, reply_frame(sd::msp::kAttitude, Bytes(6, 0)), 60);
  EXPECT_FALSE(fc.telemetry().has_attitude);
  EXPECT_EQ(fc.counters().unexpected, 1U);
}

TEST(FcLink, ArmedStateAndFreshness) {
  sd::FcLink fc;
  EXPECT_FALSE(fc.status_fresh(0, 500));
  std::uint32_t t = 0;
  for (; t < 1000; ++t) {
    const int cmd = poll_cmd(fc, t);
    if (cmd == sd::msp::kStatus) {
      answer(fc, reply_frame(sd::msp::kStatus, status_payload(true)), t);
      break;
    }
    if (cmd > 0) {
      answer(fc, reply_frame(static_cast<std::uint8_t>(cmd), Bytes(9, 0)), t);
    }
  }
  ASSERT_TRUE(fc.telemetry().has_status);
  EXPECT_TRUE(fc.telemetry().status.armed);
  EXPECT_TRUE(fc.status_fresh(t + 499, 500));
  EXPECT_FALSE(fc.status_fresh(t + 500, 500));
}

TEST(FcLink, ErrorAndBadRepliesCounted) {
  sd::FcLink fc;
  ASSERT_EQ(poll_cmd(fc, 0), sd::msp::kAttitude);
  answer(fc, reply_frame(sd::msp::kAttitude, {}, '!'), 1);
  EXPECT_EQ(fc.counters().error_replies, 1U);
  ASSERT_NE(poll_cmd(fc, 2), -1);
  Bytes bad = reply_frame(sd::msp::kStatus, status_payload(false));
  bad.back() ^= 1;
  answer(fc, bad, 3);
  EXPECT_EQ(fc.counters().bad_checksum, 1U);
  // A short (undecodable) reply to the request in flight.
  ASSERT_NE(poll_cmd(fc, 100), -1);
  answer(fc, reply_frame(sd::msp::kAttitude, Bytes(2, 0)), 100);
  answer(fc, reply_frame(sd::msp::kStatus, Bytes(2, 0)), 100);
  answer(fc, reply_frame(sd::msp::kAnalog, Bytes(2, 0)), 100);
  EXPECT_GE(fc.counters().unexpected, 1U);
  EXPECT_FALSE(fc.telemetry().has_attitude);
}

TEST(FcLink, SurvivesTheMillisWrap) {
  // millis() wraps after 49.7 days; the schedule must keep running across it.
  sd::FcLink fc;
  std::uint32_t t = 0xFFFFFF00U;
  int sent = 0;
  for (int i = 0; i < 1000; ++i, ++t) {
    const int cmd = poll_cmd(fc, t);
    if (cmd > 0) {
      ++sent;
      answer(fc, reply_frame(static_cast<std::uint8_t>(cmd), Bytes(9, 0)), t);
    }
  }
  EXPECT_GE(sent, 30);
  EXPECT_EQ(fc.counters().timeouts, 0U);
}

TEST(FcLink, PollRefusesASmallBuffer) {
  sd::FcLink fc;
  std::uint8_t out[3];
  EXPECT_EQ(fc.poll(0, out, sizeof(out)), 0U);
  EXPECT_EQ(fc.counters().requests, 0U);
}
