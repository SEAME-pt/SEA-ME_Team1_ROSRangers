// ADR-024 OTA gate and upload lock (#102 PR 3), against a fake FC that
// answers like Betaflight: MSP_STATUS, and MSP_SET_ARMING_DISABLED setting or
// clearing ARMING_DISABLED_MSP (bit 16).

#include <gtest/gtest.h>

#include <cstring>
#include <deque>
#include <vector>

#include "sd/fc_link.hpp"
#include "sd/link.hpp"
#include "sd/msp.hpp"
#include "sd/ota_lock.hpp"

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

// MSP_STATUS payload with the armed bit and the arming-disable flags.
Bytes status_payload(bool armed, std::uint32_t flags) {
  Bytes p(16, 0);
  p[6] = armed ? 1 : 0; // flight mode flags, bit 0 = ARM
  p[15] = 0;            // no extra flight mode bytes
  p.push_back(30);      // arming-disable flag count
  for (int i = 0; i < 4; ++i) {
    p.push_back(static_cast<std::uint8_t>(flags >> (8 * i)));
  }
  return p;
}

// A fake FC on the UART. Records every command it receives.
struct FakeFc : sd::FcUart {
  bool armed = false;
  bool silent = false;      // answers nothing
  bool refuse_lock = false; // '!' to MSP_SET_ARMING_DISABLED
  bool ignore_lock = false; // acks 99 but never sets the flag
  std::uint32_t flags = 0;
  std::uint32_t clock = 0;
  std::deque<std::uint8_t> rx; // bytes waiting for the ESP
  // Bytes that arrive `late_at` ms after the first discard (a reply to an
  // earlier poll that was still on its way).
  Bytes late;
  std::uint32_t late_at = 0;
  std::uint32_t late_due = 0;
  bool chatty = false;          // never stops sending
  std::vector<int> commands;    // commands the ESP sent, in order
  std::vector<int> lock_values; // payload of each MSP_SET_ARMING_DISABLED
  int discards = 0;

  void write(const std::uint8_t *buf, std::size_t len) override {
    ASSERT_GE(len, 6U);
    ASSERT_EQ(buf[0], '$');
    ASSERT_EQ(buf[2], '<');
    const std::uint8_t cmd = buf[4];
    commands.push_back(cmd);
    if (silent) {
      return;
    }
    Bytes reply;
    if (cmd == sd::msp::kStatus) {
      reply = reply_frame(cmd, status_payload(armed, flags));
    } else if (cmd == sd::msp::kSetArmingDisabled) {
      lock_values.push_back(buf[5]);
      if (refuse_lock) {
        reply = reply_frame(cmd, {}, '!');
      } else {
        if (!ignore_lock) {
          flags = buf[5] ? flags | sd::msp::kArmingDisabledMsp
                         : flags & ~sd::msp::kArmingDisabledMsp;
        }
        reply = reply_frame(cmd, {});
      }
    }
    rx.insert(rx.end(), reply.begin(), reply.end());
  }
  int read() override {
    ++clock; // time passes while the ESP waits
    if (!late.empty() && late_due != 0 && clock >= late_due) {
      rx.insert(rx.end(), late.begin(), late.end());
      late.clear();
    }
    if (chatty && rx.empty()) {
      rx.push_back(0x00);
    }
    if (rx.empty()) {
      return -1;
    }
    const int b = rx.front();
    rx.pop_front();
    return b;
  }
  std::uint32_t now_ms() override { return clock; }
  void discard_input() override {
    rx.clear();
    ++discards;
    if (!late.empty() && late_due == 0) {
      late_due = clock + late_at;
    }
  }
};

int count(const std::vector<int> &v, int x) {
  int n = 0;
  for (const int e : v) {
    n += e == x;
  }
  return n;
}

// --- Encoder
// -------------------------------------------------------------------

TEST(OtaLock, ArmingDisabledHasItsOwnEncoderOnly) {
  std::uint8_t out[sd::msp::kMaxFrame];
  // Not on the allow-list: the generic encoder refuses it with any payload.
  EXPECT_FALSE(sd::msp::allowed(sd::msp::kSetArmingDisabled));
  const std::uint8_t one = 1;
  EXPECT_EQ(sd::msp::encode_request(sd::msp::kSetArmingDisabled, &one, 1, out,
                                    sizeof out),
            0U);
  // The internal encoder: '$M<', length 1, command 99, value, checksum.
  ASSERT_EQ(sd::msp::encode_set_arming_disabled(true, out, sizeof out), 7U);
  const std::uint8_t lock[7] = {'$', 'M', '<', 1, 99, 1, 1 ^ 99 ^ 1};
  EXPECT_EQ(std::memcmp(out, lock, 7), 0);
  ASSERT_EQ(sd::msp::encode_set_arming_disabled(false, out, sizeof out), 7U);
  EXPECT_EQ(out[5], 0);
  EXPECT_EQ(sd::msp::encode_set_arming_disabled(true, out, 6), 0U);
}

// --- Lock and release
// -------------------------------------------------------------

TEST(OtaLock, LocksADisarmedFcAndReadsTheLockBack) {
  FakeFc fc;
  EXPECT_EQ(sd::ota_lock(fc), sd::OtaLockResult::kLocked);
  const std::vector<int> expected = {
      sd::msp::kStatus, sd::msp::kSetArmingDisabled, sd::msp::kStatus};
  EXPECT_EQ(fc.commands, expected);
  EXPECT_EQ(fc.lock_values, std::vector<int>{1});
  EXPECT_TRUE(fc.flags & sd::msp::kArmingDisabledMsp);
  EXPECT_GE(fc.discards, 1); // buffered replies thrown away first
}

TEST(OtaLock, NeverSendsTheLockToAnArmedFc) {
  // Betaflight disarms an armed FC on value 1: the lock must not be sent.
  FakeFc fc;
  fc.armed = true;
  EXPECT_EQ(sd::ota_lock(fc), sd::OtaLockResult::kArmed);
  EXPECT_EQ(count(fc.commands, sd::msp::kSetArmingDisabled), 0);
}

TEST(OtaLock, NeverSendsTheLockWithoutAFreshStatus) {
  FakeFc fc;
  // A buffered "disarmed" reply from before the request must not count.
  const Bytes old = reply_frame(sd::msp::kStatus, status_payload(false, 0));
  fc.rx.insert(fc.rx.end(), old.begin(), old.end());
  fc.silent = true;
  EXPECT_EQ(sd::ota_lock(fc), sd::OtaLockResult::kNoReply);
  EXPECT_EQ(count(fc.commands, sd::msp::kSetArmingDisabled), 0);
  // Gives up in time: the quiet wait, then one reply timeout.
  EXPECT_LE(fc.clock, sd::kOtaQuietMs + 2 * sd::kOtaReplyTimeoutMs);
}

TEST(OtaLock, ALateOldReplyIsNeverTakenAsFresh) {
  // An old "disarmed" reply to an earlier poll lands 20 ms after the discard,
  // while the FC is now armed: the lock must wait for quiet and ask again.
  FakeFc fc;
  fc.armed = true;
  fc.late = reply_frame(sd::msp::kStatus, status_payload(false, 0));
  fc.late_at = 20;
  EXPECT_EQ(sd::ota_lock(fc), sd::OtaLockResult::kArmed);
  EXPECT_EQ(count(fc.commands, sd::msp::kSetArmingDisabled), 0);
}

TEST(OtaLock, AnFcThatNeverGoesQuietGetsNoLock) {
  FakeFc fc;
  fc.chatty = true;
  EXPECT_EQ(sd::ota_lock(fc), sd::OtaLockResult::kNoReply);
  EXPECT_TRUE(fc.commands.empty()); // not even the status request
  EXPECT_LE(fc.clock, sd::kOtaQuietMaxMs + 5);
}

TEST(OtaLock, FailsWhenTheFcDoesNotConfirmTheLock) {
  FakeFc refused;
  refused.refuse_lock = true;
  EXPECT_EQ(sd::ota_lock(refused), sd::OtaLockResult::kNotLocked);
  FakeFc ignored;
  ignored.ignore_lock = true; // acks, but the flag never shows
  EXPECT_EQ(sd::ota_lock(ignored), sd::OtaLockResult::kNotLocked);
}

TEST(OtaLock, ReleaseSendsValueZero) {
  FakeFc fc;
  fc.flags = sd::msp::kArmingDisabledMsp;
  EXPECT_TRUE(sd::ota_release(fc));
  EXPECT_EQ(fc.lock_values, std::vector<int>{0});
  EXPECT_FALSE(fc.flags & sd::msp::kArmingDisabledMsp);
  EXPECT_EQ(count(fc.commands, sd::msp::kStatus), 0); // value 0 needs no check
  FakeFc silent;
  silent.silent = true;
  EXPECT_FALSE(sd::ota_release(silent));
}

// --- Gate (D2)
// ------------------------------------------------------------------

struct NullIo : sd::LinkIo {
  void send_tcp(sd::ConnId, const std::uint8_t *, std::size_t) override {}
  void send_udp(const std::uint8_t *, std::size_t) override {}
  void close_tcp(sd::ConnId) override {}
};

std::uint8_t g_rng = 1;
void rng(std::uint8_t *out, std::size_t n) {
  for (std::size_t i = 0; i < n; ++i) {
    out[i] = g_rng++;
  }
}

sd::Key psk() {
  sd::Key k{};
  for (std::size_t i = 0; i < sd::kKeySize; ++i) {
    k.bytes[i] = static_cast<std::uint8_t>(i);
  }
  return k;
}

sd::LinkConfig config() {
  sd::LinkConfig c{};
  c.psk = psk();
  c.override_mask = 8;
  c.failsafe_output = sd::FailsafeOutput::kNeutralThenStop;
  c.random = rng;
  return c;
}

// The FcLink's status is fed by answering its polls.
void feed_status(sd::FcLink &fc, bool armed, std::uint32_t now) {
  for (std::uint32_t t = now; t < now + 400; ++t) {
    std::uint8_t out[sd::msp::kMaxFrame];
    const std::size_t n = fc.poll(t, out, sizeof out);
    if (n == 0) {
      continue;
    }
    const std::uint8_t cmd = out[4];
    const Bytes r = cmd == sd::msp::kStatus
                        ? reply_frame(cmd, status_payload(armed, 0))
                        : reply_frame(cmd, Bytes(9, 0));
    for (const std::uint8_t b : r) {
      fc.on_byte(b, t);
    }
    if (cmd == sd::msp::kStatus) {
      return;
    }
  }
}

Bytes hello_frame() {
  sd::Hello h{};
  h.proto = 1;
  std::uint8_t p[sd::kHelloSize];
  const std::size_t n = sd::encode_hello(h, p, sizeof p);
  const sd::Header hd{
      static_cast<std::uint8_t>(sd::MsgType::kHello), 0, 0, 0, 1, 0};
  Bytes out(sd::kMaxFrame);
  out.resize(sd::encode_frame(hd, p, n, psk(), out.data(), out.size()));
  return out;
}

TEST(OtaGate, OpenOnlyWithNoSessionAndAFreshDisarmedStatus) {
  NullIo io;
  sd::Link link(config(), io);
  sd::FcLink fc;
  feed_status(fc, false, 0);
  const std::uint32_t t = fc.telemetry().status_ms;
  EXPECT_FALSE(sd::ota_gate_open(link, fc, t)); // BOOT: closed
  link.boot_done(t);
  EXPECT_TRUE(sd::ota_gate_open(link, fc, t)); // NO_SESSION, disarmed, fresh
  EXPECT_TRUE(sd::ota_gate_open(link, fc, t + 499));
  EXPECT_FALSE(sd::ota_gate_open(link, fc, t + 500)); // status too old
}

TEST(OtaGate, ClosedWhenArmedOrUnknown) {
  NullIo io;
  sd::Link link(config(), io);
  link.boot_done(0);
  sd::FcLink never_answered;
  EXPECT_FALSE(sd::ota_gate_open(link, never_answered, 0)); // unknown
  sd::FcLink armed;
  feed_status(armed, true, 0);
  EXPECT_FALSE(sd::ota_gate_open(link, armed, armed.telemetry().status_ms));
}

TEST(OtaGate, ClosedWhileASessionIsOpen) {
  NullIo io;
  sd::Link link(config(), io);
  link.boot_done(0);
  const Bytes hello = hello_frame();
  // A session (SESSION_IDLE).
  link.on_tcp_frame(0, hello.data(), hello.size(), 0);
  ASSERT_EQ(link.state(), sd::EspState::kSessionIdle);
  sd::FcLink fc;
  feed_status(fc, false, 0);
  const std::uint32_t t = fc.telemetry().status_ms;
  EXPECT_FALSE(sd::ota_gate_open(link, fc, t));
  // Ended by TCP close and no UDP: NO_SESSION, open again.
  link.on_tcp_closed(0, t);
  link.tick(t + 300);
  feed_status(fc, false, t + 300);
  const std::uint32_t t2 = fc.telemetry().status_ms;
  ASSERT_EQ(link.state(), sd::EspState::kNoSession);
  EXPECT_TRUE(sd::ota_gate_open(link, fc, t2));
}

} // namespace
