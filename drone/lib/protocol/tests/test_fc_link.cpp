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
    } else if (cmd == sd::msp::kApiVersion) {
      answer(fc, reply_frame(sd::msp::kApiVersion, {0, 1, 47}), t);
    }
  }
}

// The command of any request written by poll() (RC included), or -1.
int poll_cmd_any(sd::FcLink &fc, std::uint32_t now) {
  std::uint8_t out[sd::msp::kMaxFrame];
  const std::size_t n = fc.poll(now, out, sizeof(out));
  return n == 0 ? -1 : out[4];
}

// One request written by poll(): its command and its whole frame.
int poll_frame(sd::FcLink &fc, std::uint32_t now, Bytes &frame) {
  std::uint8_t out[sd::msp::kMaxFrame];
  const std::size_t n = fc.poll(now, out, sizeof(out));
  frame.assign(out, out + n);
  return n == 0 ? -1 : out[4];
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
                c == sd::msp::kAnalog || c == sd::msp::kApiVersion)
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
  // Timeout at 50 ms, then a drain window: nothing is sent, and a late reply
  // to the timed-out request is dropped, not taken as an answer.
  EXPECT_EQ(poll_cmd(fc, sd::kReplyTimeoutMs), -1);
  EXPECT_EQ(fc.counters().timeouts, 1U);
  answer(fc, reply_frame(sd::msp::kAttitude, Bytes(6, 0)), 60);
  EXPECT_FALSE(fc.telemetry().has_attitude);
  EXPECT_EQ(fc.counters().unexpected, 1U);
  for (std::uint32_t t = sd::kReplyTimeoutMs; t < 2 * sd::kReplyTimeoutMs;
       ++t) {
    EXPECT_EQ(poll_cmd(fc, t), -1) << t;
  }
  EXPECT_NE(poll_cmd(fc, 2 * sd::kReplyTimeoutMs), -1);
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
  // The poll at 100 ms finds the bad-checksum request timed out and starts the
  // drain window; the next request goes at 150 ms.
  EXPECT_EQ(poll_cmd(fc, 100), -1);
  EXPECT_EQ(poll_cmd(fc, 149), -1);
  // A short (undecodable) reply to the next request in flight.
  ASSERT_NE(poll_cmd(fc, 150), -1);
  answer(fc, reply_frame(sd::msp::kAttitude, Bytes(2, 0)), 150);
  answer(fc, reply_frame(sd::msp::kStatus, Bytes(2, 0)), 150);
  answer(fc, reply_frame(sd::msp::kAnalog, Bytes(2, 0)), 150);
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

// An old status is forgotten after kMaxValueAgeMs, so it never looks fresh
// again, even across the millis() wrap (ADR-024 gate).
TEST(FcLink, OldValuesAreForgotten) {
  sd::FcLink fc;
  std::uint32_t t = 0xFFFFF000U; // the wrap is 4 s ahead
  for (int i = 0; i < 300; ++i, ++t) {
    const int cmd = poll_cmd(fc, t);
    if (cmd == sd::msp::kStatus) {
      answer(fc, reply_frame(sd::msp::kStatus, status_payload(false)), t);
    } else if (cmd > 0) {
      answer(fc, reply_frame(static_cast<std::uint8_t>(cmd), Bytes(9, 0)), t);
    }
  }
  ASSERT_TRUE(fc.telemetry().has_status);
  const std::uint32_t got = fc.telemetry().status_ms;
  // The FC goes silent. 10 s later every value is gone.
  std::uint8_t out[sd::msp::kMaxFrame];
  for (std::uint32_t dt = 0; dt <= sd::kMaxValueAgeMs + 10; dt += 10) {
    fc.poll(got + dt, out, sizeof(out));
  }
  EXPECT_FALSE(fc.telemetry().has_status);
  EXPECT_FALSE(fc.telemetry().has_attitude);
  EXPECT_FALSE(fc.telemetry().has_analog);
  // 2^32 ms later the old status does not come back as fresh.
  EXPECT_FALSE(fc.status_fresh(got + 0x100000000ULL - 1, 500));
  EXPECT_FALSE(fc.status_fresh(got, 500));
}

// BOOT -> NO_SESSION waits for this (#102): the FC answered MSP_API_VERSION.
TEST(FcLink, ReadyOnlyAfterTheFcAnswersApiVersion) {
  sd::FcLink fc;
  std::vector<int> sent;
  EXPECT_FALSE(fc.fc_ready());
  run_ideal_fc(fc, 999, &sent);
  int api = 0;
  for (const int c : sent) {
    api += c == sd::msp::kApiVersion;
  }
  EXPECT_EQ(api, 1); // 1 Hz
  ASSERT_TRUE(fc.fc_ready());
  EXPECT_EQ(fc.telemetry().api_major, 1);
  EXPECT_EQ(fc.telemetry().api_minor, 47);
  // The FC goes silent: after kMaxValueAgeMs it is no longer ready.
  const std::uint32_t got = fc.telemetry().api_version_ms;
  std::uint8_t out[sd::msp::kMaxFrame];
  for (std::uint32_t dt = 0; dt <= sd::kMaxValueAgeMs + 10; dt += 10) {
    fc.poll(got + dt, out, sizeof(out));
  }
  EXPECT_FALSE(fc.fc_ready());
}

TEST(FcLink, ErrorReplyToApiVersionIsNotReady) {
  sd::FcLink fc;
  std::uint32_t t = 0;
  for (; t < 2000; ++t) {
    const int cmd = poll_cmd(fc, t);
    if (cmd == sd::msp::kApiVersion) {
      answer(fc, reply_frame(sd::msp::kApiVersion, {}, '!'), t);
    } else if (cmd > 0) {
      answer(fc, reply_frame(static_cast<std::uint8_t>(cmd), Bytes(9, 0)), t);
    }
  }
  EXPECT_FALSE(fc.fc_ready());
  EXPECT_GE(fc.counters().error_replies, 1U);
}

TEST(FcLink, RcGoesFirstAndIsAcknowledged) {
  sd::FcLink fc;
  const std::uint16_t ch[sd::msp::kRcChannels] = {1100, 1200, 1300, 1400,
                                                  1500, 1600, 1700, 1800};
  fc.set_rc(ch);
  Bytes frame;
  ASSERT_EQ(poll_frame(fc, 0, frame), sd::msp::kSetRawRc);
  ASSERT_EQ(frame.size(), 6U + 16U);
  EXPECT_EQ(frame[3], 16); // 8 channels, u16 little-endian
  EXPECT_EQ(frame[5] | frame[6] << 8, 1100);
  EXPECT_EQ(frame[19] | frame[20] << 8, 1800);
  EXPECT_EQ(poll_cmd(fc, 1), -1); // one request at a time
  answer(fc, reply_frame(sd::msp::kSetRawRc, {}), 2);
  EXPECT_EQ(fc.counters().replies, 1U);
  EXPECT_EQ(fc.counters().rc_sent, 1U);
  EXPECT_EQ(fc.counters().unexpected, 0U);
  // Sent once: the next request is a poll.
  EXPECT_EQ(poll_cmd(fc, 3), sd::msp::kAttitude);
}

TEST(FcLink, OnlyTheLatestRcIsSent) {
  sd::FcLink fc;
  ASSERT_EQ(poll_cmd(fc, 0), sd::msp::kAttitude); // in flight, no reply yet
  std::uint16_t ch[sd::msp::kRcChannels] = {1500, 1500, 1000, 1500,
                                            1500, 1500, 1500, 1500};
  fc.set_rc(ch);
  ch[0] = 1900;
  fc.set_rc(ch); // replaces the one not sent yet
  EXPECT_EQ(fc.counters().rc_replaced, 1U);
  answer(fc, reply_frame(sd::msp::kAttitude, Bytes(6, 0)), 5);
  Bytes frame;
  ASSERT_EQ(poll_frame(fc, 6, frame), sd::msp::kSetRawRc);
  EXPECT_EQ(frame[5] | frame[6] << 8, 1900);
}

TEST(FcLink, StopRcSendsNothing) {
  sd::FcLink fc;
  const std::uint16_t ch[sd::msp::kRcChannels] = {1500, 1500, 1000, 1500,
                                                  1500, 1500, 1500, 1500};
  fc.set_rc(ch);
  fc.stop_rc();
  EXPECT_EQ(poll_cmd(fc, 0), sd::msp::kAttitude);
  EXPECT_EQ(fc.counters().rc_sent, 0U);
}

TEST(FcLink, RcAt50HzLeavesTheAdr009Polls) {
  // An FC that answers at once: 50 RC frames a second and every poll on time.
  sd::FcLink fc;
  const std::uint16_t ch[sd::msp::kRcChannels] = {1500, 1500, 1000, 1500,
                                                  1500, 1500, 1500, 1500};
  int attitude = 0;
  for (std::uint32_t t = 0; t < 1000; ++t) {
    if (t % 20 == 0) {
      fc.set_rc(ch);
    }
    for (int i = 0; i < 2; ++i) { // RC and a poll may both be due this ms
      Bytes frame;
      const int cmd = poll_frame(fc, t, frame);
      if (cmd < 0) {
        break;
      }
      attitude += cmd == sd::msp::kAttitude;
      const Bytes reply = cmd == sd::msp::kSetRawRc ? Bytes{}
                          : cmd == sd::msp::kStatus ? status_payload(false)
                                                    : Bytes(9, 0);
      answer(fc, reply_frame(static_cast<std::uint8_t>(cmd), reply), t);
    }
  }
  EXPECT_EQ(fc.counters().rc_sent, 50U);
  EXPECT_EQ(fc.counters().rc_replaced, 0U);
  EXPECT_EQ(attitude, 25);
  EXPECT_EQ(fc.counters().timeouts, 0U);
}

TEST(FcLink, ASilentFcDoesNotStarveThePolls) {
  // The FC stops answering: RC keeps timing out, but polls still go out, so
  // the FC is seen again as soon as it answers (safety review of #102 PR 2).
  sd::FcLink fc;
  const std::uint16_t ch[sd::msp::kRcChannels] = {1500, 1500, 1000, 1500,
                                                  1500, 1500, 1500, 1500};
  int polls = 0;
  int rc = 0;
  for (std::uint32_t t = 0; t < 3000; ++t) {
    if (t % 20 == 0) {
      fc.set_rc(ch);
    }
    const int cmd = poll_cmd_any(fc, t);
    polls += cmd > 0 && cmd != sd::msp::kSetRawRc;
    rc += cmd == sd::msp::kSetRawRc;
  }
  EXPECT_GE(polls, 10); // at least every other cycle of 100 ms
  EXPECT_GE(rc, 10);    // RC still goes out between them
  // The FC answers again: its values come back.
  for (std::uint32_t t = 3000; t < 3500; ++t) {
    if (t % 20 == 0) {
      fc.set_rc(ch);
    }
    const int cmd = poll_cmd_any(fc, t);
    if (cmd == sd::msp::kApiVersion) {
      answer(fc, reply_frame(sd::msp::kApiVersion, {0, 1, 47}), t);
    } else if (cmd == sd::msp::kStatus) {
      answer(fc, reply_frame(sd::msp::kStatus, status_payload(false)), t);
    } else if (cmd > 0) {
      answer(fc,
             reply_frame(static_cast<std::uint8_t>(cmd),
                         cmd == sd::msp::kSetRawRc ? Bytes{} : Bytes(9, 0)),
             t);
    }
  }
  EXPECT_TRUE(fc.telemetry().has_status);
  EXPECT_TRUE(fc.telemetry().has_attitude);
}

// #179 D1: the FC is alive while it answers our requests, lost after 250 ms.
TEST(FcLink, AliveWhileTheFcAnswersLostAfter250Ms) {
  sd::FcLink fc;
  EXPECT_FALSE(fc.fc_alive(0)); // nothing answered yet
  ASSERT_EQ(poll_cmd(fc, 0), sd::msp::kAttitude);
  answer(fc, reply_frame(sd::msp::kAttitude, Bytes(6, 0)), 3);
  EXPECT_TRUE(fc.fc_alive(3));
  EXPECT_TRUE(fc.fc_alive(3 + sd::kFcLostMs - 1));
  EXPECT_FALSE(fc.fc_alive(3 + sd::kFcLostMs));
}

TEST(FcLink, AnErrorReplyStillMeansAlive) {
  sd::FcLink fc;
  ASSERT_EQ(poll_cmd(fc, 0), sd::msp::kAttitude);
  answer(fc, reply_frame(sd::msp::kAttitude, {}, '!'), 2);
  EXPECT_TRUE(fc.fc_alive(2)); // the FC refused, but it is there
}

TEST(FcLink, AReplyNobodyAskedForIsNotAlive) {
  sd::FcLink fc;
  answer(fc, reply_frame(sd::msp::kAttitude, Bytes(6, 0)), 1); // no request
  EXPECT_FALSE(fc.fc_alive(1));
  EXPECT_EQ(fc.counters().unexpected, 1U);
}

TEST(FcLink, AliveNeverComesBackAcrossTheMillisWrap) {
  sd::FcLink fc;
  ASSERT_EQ(poll_cmd(fc, 0), sd::msp::kAttitude);
  answer(fc, reply_frame(sd::msp::kAttitude, Bytes(6, 0)), 1);
  std::uint8_t out[sd::msp::kMaxFrame];
  // The FC goes silent; polls keep timing out for longer than the value age.
  for (std::uint32_t t = 2; t <= sd::kMaxValueAgeMs + 200; t += 10) {
    fc.poll(t, out, sizeof out);
  }
  EXPECT_FALSE(fc.fc_alive(sd::kMaxValueAgeMs + 200));
  // 2^32 ms after the last reply the clock reads the same value again.
  EXPECT_FALSE(fc.fc_alive(1));
}

// --- #188: RC accepted, and alive with hysteresis
// -----------------------------

namespace {

enum class Answer { kNone, kGood, kError };

// RC at 50 Hz from `from` to `to` (1 ms steps); each request gets what
// `policy(cmd, t)` says.
template <typename Policy>
void run_fc(sd::FcLink &fc, std::uint32_t from, std::uint32_t to, Policy policy,
            bool send_rc = true) {
  const std::uint16_t ch[sd::msp::kRcChannels] = {1500, 1500, 1000, 1500,
                                                  1500, 1500, 1500, 1500};
  for (std::uint32_t t = from; t < to; ++t) {
    if (send_rc && t % 20 == 0) {
      fc.set_rc(ch);
    }
    for (int i = 0; i < 2; ++i) { // RC and a poll may both be due this ms
      Bytes frame;
      const int cmd = poll_frame(fc, t, frame);
      if (cmd < 0) {
        break;
      }
      const Answer a = policy(cmd, t);
      if (a == Answer::kNone) {
        break;
      }
      const Bytes payload = a == Answer::kError || cmd == sd::msp::kSetRawRc
                                ? Bytes{}
                            : cmd == sd::msp::kStatus ? status_payload(false)
                            : cmd == sd::msp::kApiVersion ? Bytes{0, 1, 47}
                                                          : Bytes(9, 0);
      answer(fc,
             reply_frame(static_cast<std::uint8_t>(cmd), payload,
                         a == Answer::kError ? '!' : '>'),
             t);
    }
  }
}

Answer all_good(int, std::uint32_t) { return Answer::kGood; }

} // namespace

TEST(FcLink, RcAcceptedAfterOneSecondOfGoodAcks) {
  sd::FcLink fc;
  run_fc(fc, 0, 900, all_good);
  EXPECT_FALSE(fc.rc_accepted(899)); // first ack at t=0: not 1 s yet
  run_fc(fc, 900, 1100, all_good);
  EXPECT_TRUE(fc.rc_accepted(1099));
  EXPECT_FALSE(fc.rc_accepted(1099 + sd::kRcMaxGapMs)); // no ack for 100 ms
}

TEST(FcLink, PollsAnsweredButRcNotIsNeverAccepted) {
  sd::FcLink fc;
  run_fc(fc, 0, 3000, [](int cmd, std::uint32_t) {
    return cmd == sd::msp::kSetRawRc ? Answer::kNone : Answer::kGood;
  });
  EXPECT_TRUE(fc.fc_alive(2999)); // #183's rule alone would let ARM through
  EXPECT_FALSE(fc.rc_accepted(2999));
}

TEST(FcLink, RcRefusedWithErrorIsNeverAccepted) {
  sd::FcLink fc;
  run_fc(fc, 0, 3000, [](int cmd, std::uint32_t) {
    return cmd == sd::msp::kSetRawRc ? Answer::kError : Answer::kGood;
  });
  EXPECT_TRUE(fc.fc_alive(2999));
  EXPECT_FALSE(fc.rc_accepted(2999));
}

TEST(FcLink, OneMissedRcAckRestartsTheSecond) {
  sd::FcLink fc;
  run_fc(fc, 0, 1500, all_good);
  ASSERT_TRUE(fc.rc_accepted(1499));
  bool dropped = false;
  run_fc(fc, 1500, 1600, [&dropped](int cmd, std::uint32_t) {
    if (cmd == sd::msp::kSetRawRc && !dropped) {
      dropped = true; // one RC request goes unanswered
      return Answer::kNone;
    }
    return Answer::kGood;
  });
  EXPECT_FALSE(fc.rc_accepted(1599));
  run_fc(fc, 1600, 2700, all_good);
  EXPECT_TRUE(fc.rc_accepted(2699)); // 1 s of good acks again
}

TEST(FcLink, RcStoppedThenRestartedNeedsAnotherSecond) {
  sd::FcLink fc;
  run_fc(fc, 0, 1500, all_good);
  ASSERT_TRUE(fc.rc_accepted(1499));
  run_fc(fc, 1500, 3000, all_good, false); // the ADR-004 output stopped
  EXPECT_FALSE(fc.rc_accepted(2999));
  run_fc(fc, 3000, 3500, all_good);
  EXPECT_FALSE(fc.rc_accepted(3499)); // back, but only 0.5 s of acks
  run_fc(fc, 3500, 4100, all_good);
  EXPECT_TRUE(fc.rc_accepted(4099));
}

TEST(FcLink, FcComesBackOnlyAfter250MsOfReplies) {
  sd::FcLink fc;
  run_fc(fc, 0, 100, all_good);
  EXPECT_FALSE(fc.fc_up(false, 99)); // answering for < 250 ms
  EXPECT_TRUE(fc.fc_up(true, 99));   // already up: stays up while alive
  run_fc(fc, 100, 300, all_good);
  EXPECT_TRUE(fc.fc_up(false, 299));
}

TEST(FcLink, AHalfAnsweringFcNeverComesBack) {
  // Every other request of each kind goes unanswered: alive in short windows
  // (#183 review), but never 250 ms without a timeout, so no RESTORED
  // flapping, and RC is never accepted.
  sd::FcLink fc;
  int n[256] = {};
  bool up = false;
  int changes = 0;
  for (std::uint32_t t = 0; t < 3000; t += 10) {
    run_fc(fc, t, t + 10, [&n](int cmd, std::uint32_t) {
      return ++n[cmd & 0xFF] % 2 ? Answer::kGood : Answer::kNone;
    });
    const bool now_up = fc.fc_up(up, t + 9);
    changes += now_up != up;
    up = now_up;
  }
  EXPECT_EQ(changes, 0);
  EXPECT_FALSE(fc.rc_accepted(2999));
}

TEST(FcLink, ASilenceWithoutATimeoutStillNeeds250MsToComeBack) {
  // Nothing in flight for 400 ms (a stalled loop): no timeout is counted, but
  // the FC was lost, so it comes back only after 250 ms of replies again.
  sd::FcLink fc;
  run_fc(fc, 0, 400, all_good);
  ASSERT_TRUE(fc.fc_up(false, 399));
  EXPECT_FALSE(fc.fc_alive(400 + sd::kFcLostMs)); // nobody polled meanwhile
  run_fc(fc, 800, 850, all_good);
  EXPECT_FALSE(fc.fc_up(false, 849));
  run_fc(fc, 850, 1100, all_good);
  EXPECT_TRUE(fc.fc_up(false, 1099));
}

TEST(FcLink, OneRefusedRcRestartsTheSecond) {
  sd::FcLink fc;
  run_fc(fc, 0, 1500, all_good);
  ASSERT_TRUE(fc.rc_accepted(1499));
  bool refused = false;
  run_fc(fc, 1500, 1600, [&refused](int cmd, std::uint32_t) {
    if (cmd == sd::msp::kSetRawRc && !refused) {
      refused = true; // one '!' reply to RC
      return Answer::kError;
    }
    return Answer::kGood;
  });
  EXPECT_FALSE(fc.rc_accepted(1599));
  run_fc(fc, 1600, 2700, all_good);
  EXPECT_TRUE(fc.rc_accepted(2699));
}

TEST(FcLink, APollTimeoutDoesNotRestartTheRcSecond) {
  sd::FcLink fc;
  run_fc(fc, 0, 1500, all_good);
  ASSERT_TRUE(fc.rc_accepted(1499));
  bool dropped = false;
  run_fc(fc, 1500, 1600, [&dropped](int cmd, std::uint32_t) {
    if (cmd != sd::msp::kSetRawRc && !dropped) {
      dropped = true; // one poll unanswered, every RC acked
      return Answer::kNone;
    }
    return Answer::kGood;
  });
  EXPECT_TRUE(fc.rc_accepted(1599));
}
