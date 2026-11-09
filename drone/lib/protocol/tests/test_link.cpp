// Link state machine (#102): every state and transition of
// docs/PraticalPlan/03 Architecture/ESP Link State Machine.md, with a fake
// ground that does the real handshake and key derivation.

#include <gtest/gtest.h>

#include <cstring>
#include <utility>
#include <vector>

#include "sd/frame.hpp"
#include "sd/hmac.hpp"
#include "sd/link.hpp"
#include "sd/messages.hpp"
#include "sd/receiver.hpp"
#include "sd/setpoint.hpp"

namespace {

using Bytes = std::vector<std::uint8_t>;
using sd::EspState;
using sd::MsgType;

constexpr std::uint8_t kMask = 8; // stage 1: yaw only (ADR-010)

// Deterministic "hardware RNG": a counter, or zeros when g_rng_zero is set.
std::uint8_t g_rng = 1;
bool g_rng_zero = false;
void fake_random(std::uint8_t *out, std::size_t n) {
  for (std::size_t i = 0; i < n; ++i) {
    out[i] = g_rng_zero ? 0 : g_rng++;
  }
}

sd::Key psk() {
  sd::Key k{};
  for (std::size_t i = 0; i < sd::kKeySize; ++i) {
    k.bytes[i] = static_cast<std::uint8_t>(0xA0 + i);
  }
  return k;
}

struct Io : sd::LinkIo {
  std::vector<std::pair<sd::ConnId, Bytes>> tcp;
  std::vector<Bytes> udp;
  std::vector<sd::ConnId> closed;
  void send_tcp(sd::ConnId c, const std::uint8_t *b, std::size_t n) override {
    tcp.emplace_back(c, Bytes(b, b + n));
  }
  void send_udp(const std::uint8_t *b, std::size_t n) override {
    udp.emplace_back(b, b + n);
  }
  void close_tcp(sd::ConnId c) override { closed.push_back(c); }
  bool was_closed(sd::ConnId c) const {
    for (sd::ConnId x : closed) {
      if (x == c) {
        return true;
      }
    }
    return false;
  }
};

// The ground side of one session.
struct Ground {
  std::uint8_t nonce[sd::kNonceSize] = {};
  std::uint32_t session = 0;
  sd::DirectionKeys keys{};
  std::uint32_t seq_tcp = 1; // HELLO used 1
  std::uint32_t seq_udp = 0;

  explicit Ground(std::uint8_t seed = 0x11) {
    for (std::size_t i = 0; i < sd::kNonceSize; ++i) {
      nonce[i] = static_cast<std::uint8_t>(seed + i);
    }
  }

  Bytes hello(std::uint8_t proto = 1) const {
    sd::Hello h{};
    h.proto = proto;
    std::memcpy(h.ground_nonce, nonce, sd::kNonceSize);
    std::uint8_t p[sd::kHelloSize];
    const std::size_t n = sd::encode_hello(h, p, sizeof p);
    const sd::Header hd{
        static_cast<std::uint8_t>(MsgType::kHello), 0, 0, 0, 1, 0};
    Bytes out(sd::kMaxFrame);
    out.resize(sd::encode_frame(hd, p, n, psk(), out.data(), out.size()));
    return out;
  }

  // Takes the HELLO_ACK the ESP sent; derives the session keys.
  bool take_ack(const Bytes &frame) {
    sd::Frame f{};
    if (sd::receive_handshake(sd::Role::kGround, frame.data(), frame.size(),
                              psk(), f) != sd::HandshakeResult::kAccepted) {
      return false;
    }
    sd::HelloAck ack{};
    if (!sd::decode_hello_ack(f.payload, f.header.payload_len, ack)) {
      return false;
    }
    session = ack.session_id;
    keys = sd::derive_keys(psk(), nonce, ack.esp_nonce);
    seq_tcp = 1;
    seq_udp = 0;
    return true;
  }

  Bytes tcp(MsgType type, const Bytes &payload = {}) {
    return seal(type, payload, ++seq_tcp);
  }

  Bytes setpoint(std::uint8_t mode, std::int16_t roll = 0,
                 std::int16_t throttle = 0) {
    sd::Setpoint sp{mode, roll, 0, 0, throttle, 0};
    Bytes p(sd::kSetpointSize);
    sd::encode_setpoint(sp, p.data(), p.size());
    return seal(MsgType::kSetpoint, p, ++seq_udp);
  }

  Bytes seal(MsgType type, const Bytes &payload, std::uint32_t seq) const {
    const sd::Header hd{static_cast<std::uint8_t>(type), 0, 0, session, seq, 0};
    Bytes out(sd::kMaxFrame);
    out.resize(sd::encode_frame(hd, payload.data(), payload.size(),
                                keys.ground_to_esp, out.data(), out.size()));
    return out;
  }

  // Decodes a frame the ESP sent in this session.
  bool open(const Bytes &frame, sd::Frame &f) const {
    return sd::decode_frame(frame.data(), frame.size(), keys.esp_to_ground,
                            f) == sd::RxError::kOk &&
           f.header.session_id == session;
  }
};

Bytes u8(std::uint8_t v) { return Bytes{v}; }

Bytes config_kv(std::uint8_t key, std::int32_t value) {
  Bytes p(sd::kConfigKvSize);
  sd::encode_config_kv(sd::ConfigKv{key, value}, p.data(), p.size());
  return p;
}

class LinkTest : public ::testing::Test {
protected:
  LinkTest() : link(make_config(sd::FailsafeOutput::kNeutralThenStop), io) {}

  static sd::LinkConfig make_config(sd::FailsafeOutput fo) {
    g_rng = 1;
    g_rng_zero = false;
    sd::LinkConfig c{};
    c.psk = psk();
    c.override_mask = kMask;
    c.failsafe_output = fo;
    std::memcpy(c.fw_version, "gw-test-0001", sd::kFwVersionSize);
    c.random = fake_random;
    return c;
  }

  // HELLO on `conn`; the ground takes the HELLO_ACK. Returns false if none.
  bool handshake(Ground &g, sd::ConnId conn, std::uint32_t now) {
    const std::size_t before = io.tcp.size();
    const Bytes hello = g.hello();
    EXPECT_EQ(link.on_tcp_frame(conn, hello.data(), hello.size(), now),
              sd::TcpVerdict::kKeep);
    for (std::size_t i = before; i < io.tcp.size(); ++i) {
      if (io.tcp[i].first == conn && g.take_ack(io.tcp[i].second)) {
        return true;
      }
    }
    return false;
  }

  // Mode-1 setpoints every 20 ms in [from, to), so STREAMING stays alive.
  void feed(Ground &g, std::uint32_t from, std::uint32_t to) {
    for (std::uint32_t t = from; t < to; t += 20) {
      ASSERT_EQ(udp(g.setpoint(1, 1000), t), sd::RxResult::kAccepted);
    }
  }

  sd::TcpVerdict tcp(sd::ConnId conn, const Bytes &frame, std::uint32_t now) {
    return link.on_tcp_frame(conn, frame.data(), frame.size(), now);
  }

  sd::RxResult udp(const Bytes &frame, std::uint32_t now) {
    return link.on_udp(frame.data(), frame.size(), now);
  }

  // First payload byte of the last frame of type `expect` sent to `g`.
  std::uint8_t last_ack(const Ground &g, MsgType expect) {
    for (std::size_t i = io.tcp.size(); i > 0; --i) {
      sd::Frame f{};
      if (g.open(io.tcp[i - 1].second, f) &&
          f.header.type == static_cast<std::uint8_t>(expect)) {
        return f.header.payload_len > 0 ? f.payload[0] : 0;
      }
    }
    ADD_FAILURE() << "no frame of type " << int(expect);
    return 0xEE;
  }

  // Counts EVENT frames with `code` that the ESP sent to `g`.
  int events(const Ground &g, sd::EventCode code) {
    int n = 0;
    for (const auto &m : io.tcp) {
      sd::Frame f{};
      if (!g.open(m.second, f) ||
          f.header.type != static_cast<std::uint8_t>(MsgType::kEvent)) {
        continue;
      }
      sd::Event e{};
      if (sd::decode_event(f.payload, f.header.payload_len, e) &&
          e.code == static_cast<std::uint16_t>(code)) {
        ++n;
      }
    }
    return n;
  }

  // Session open on conn 0 at t=0, ARM OK at t=10, one neutral setpoint.
  void stream(Ground &g) {
    link.boot_done(0);
    ASSERT_TRUE(handshake(g, 0, 0));
    ASSERT_EQ(udp(g.setpoint(0), 5), sd::RxResult::kAccepted);
    tcp(0, g.tcp(MsgType::kArmControlRequest, u8(kMask)), 10);
    ASSERT_EQ(link.state(), EspState::kStreaming);
  }

  Io io;
  sd::Link link;
  std::uint16_t ch[sd::kRcChannels] = {};
};

bool is_neutral(const std::uint16_t ch[sd::kRcChannels]) {
  return ch[0] == 1500 && ch[1] == 1500 && ch[2] == 1000 && ch[3] == 1500 &&
         ch[4] == 1500 && ch[5] == 1500 && ch[6] == 1500 && ch[7] == 1500;
}

// --- BOOT and NO_SESSION
// ------------------------------------------------------

TEST_F(LinkTest, BootSendsNothingAndDropsEverything) {
  EXPECT_EQ(link.state(), EspState::kBoot);
  EXPECT_FALSE(link.fc_output(0, ch));
  Ground g;
  EXPECT_EQ(tcp(0, g.hello(), 0), sd::TcpVerdict::kClose);
  EXPECT_TRUE(io.tcp.empty());
  EXPECT_EQ(udp(g.setpoint(0), 0), sd::RxResult::kNoSession);
  EXPECT_FALSE(link.fc_output(5000, ch)); // stays in BOOT without boot_done
}

TEST_F(LinkTest, NoSessionSendsNeutralFrames) {
  link.boot_done(0);
  EXPECT_EQ(link.state(), EspState::kNoSession);
  ASSERT_TRUE(link.fc_output(0, ch));
  EXPECT_TRUE(is_neutral(ch));
  link.send_state(0, sd::FcView{});
  EXPECT_TRUE(io.udp.empty()); // no session, no STATE
}

TEST_F(LinkTest, HelloInNoSessionOpensTheSessionAtOnce) {
  link.boot_done(0);
  Ground g;
  ASSERT_TRUE(handshake(g, 3, 100));
  EXPECT_EQ(link.state(), EspState::kSessionIdle);
  EXPECT_TRUE(link.has_session());
  EXPECT_FALSE(link.has_pending());
  EXPECT_EQ(link.session_conn(), 3);
  EXPECT_EQ(link.session_id(), g.session);
  EXPECT_NE(g.session, 0U);
  EXPECT_EQ(udp(g.setpoint(0), 110), sd::RxResult::kAccepted);
  ASSERT_TRUE(link.fc_output(110, ch));
  EXPECT_TRUE(is_neutral(ch)); // SESSION_IDLE: neutral frames
}

TEST_F(LinkTest, BadHelloIsCountedAndKeepsOrClosesTheConnection) {
  link.boot_done(0);
  Ground g;
  Bytes bad_tag = g.hello();
  bad_tag.back() ^= 1;
  EXPECT_EQ(tcp(0, bad_tag, 0), sd::TcpVerdict::kKeep);
  Bytes bad_magic = g.hello();
  bad_magic[0] = 0;
  bad_magic[3] = 0x01; // still looks like a HELLO
  EXPECT_EQ(tcp(0, bad_magic, 0), sd::TcpVerdict::kClose);
  EXPECT_EQ(tcp(0, g.hello(2), 0), sd::TcpVerdict::kKeep); // unknown proto
  EXPECT_TRUE(io.tcp.empty());
  EXPECT_EQ(link.counters().hello_rejected, 3U);
  EXPECT_EQ(link.rx_rejected(), 3U);
  EXPECT_EQ(link.state(), EspState::kNoSession);
}

TEST_F(LinkTest, ABadHelloDoesNotUseTheRateLimitWindow) {
  link.boot_done(0);
  Ground g;
  Bytes bad_tag = g.hello();
  bad_tag.back() ^= 1;
  tcp(0, bad_tag, 0);
  tcp(0, g.hello(2), 100);           // valid tag, unknown proto
  EXPECT_TRUE(handshake(g, 0, 200)); // within 500 ms of both
  EXPECT_EQ(link.counters().hello_rate_limited, 0U);
}

TEST_F(LinkTest, NonHelloOnAConnectionWithoutSessionClosesIt) {
  link.boot_done(0);
  Ground g;
  ASSERT_TRUE(handshake(g, 0, 0));
  // Valid frame of the session, but sent on another connection.
  EXPECT_EQ(tcp(1, g.tcp(MsgType::kArmControlRequest, u8(kMask)), 10),
            sd::TcpVerdict::kClose);
  EXPECT_EQ(link.state(), EspState::kSessionIdle);
  EXPECT_EQ(link.counters().unrouted, 1U);
}

TEST_F(LinkTest, UdpWithoutASessionIsDroppedAndCounted) {
  link.boot_done(0);
  Ground g;
  g.session = 0x1234;
  EXPECT_EQ(udp(g.setpoint(0), 0), sd::RxResult::kNoSession);
  EXPECT_EQ(link.counters().unrouted, 1U);
  EXPECT_EQ(link.rx_rejected(), 1U);
}

TEST_F(LinkTest, BadFramesOnTheSessionConnection) {
  link.boot_done(0);
  Ground g;
  ASSERT_TRUE(handshake(g, 0, 0));
  Bytes bad_tag = g.tcp(MsgType::kConfigGet, config_kv(0x01, 0));
  bad_tag.back() ^= 1;
  EXPECT_EQ(tcp(0, bad_tag, 10), sd::TcpVerdict::kKeep); // §4 rule 5
  Bytes bad_version = g.tcp(MsgType::kConfigGet, config_kv(0x01, 0));
  bad_version[2] = 9;
  EXPECT_EQ(tcp(0, bad_version, 20), sd::TcpVerdict::kClose);
  EXPECT_EQ(link.state(), EspState::kSessionIdle); // no failsafe on its own
  EXPECT_EQ(link.rx_rejected(), 2U);
}

TEST_F(LinkTest, ForeignFrameOnThePendingConnectionIsRejected) {
  Ground g;
  stream(g);
  feed(g, 20, 600);
  Ground g2(0x33);
  ASSERT_TRUE(handshake(g2, 1, 600));
  // The current session's frame, but on the pending (HELLO-only) connection.
  EXPECT_EQ(tcp(1, g.tcp(MsgType::kConfigGet, config_kv(0x01, 0)), 610),
            sd::TcpVerdict::kKeep);
  EXPECT_TRUE(link.has_pending());
  EXPECT_EQ(link.session_id(), g.session);
  EXPECT_EQ(link.counters().sessions_switched, 0U);
}

TEST_F(LinkTest, SessionIdsComeFromTheRngAndAreNeverZero) {
  link.boot_done(0);
  g_rng_zero = true;
  Ground g;
  EXPECT_EQ(tcp(0, g.hello(), 0), sd::TcpVerdict::kKeep);
  EXPECT_TRUE(io.tcp.empty());
  EXPECT_EQ(link.counters().hello_rejected, 1U);
  EXPECT_FALSE(link.has_session());
  g_rng_zero = false;
  ASSERT_TRUE(handshake(g, 0, 0)); // a refused HELLO does not use the window
}

// --- ARM_CONTROL_REQUEST -> STREAMING ----------------------------------------

TEST_F(LinkTest, ArmWithTheWrongMaskIsRefused) {
  link.boot_done(0);
  Ground g;
  ASSERT_TRUE(handshake(g, 0, 0));
  tcp(0, g.tcp(MsgType::kArmControlRequest, u8(15)), 10);
  EXPECT_EQ(last_ack(g, MsgType::kArmControlAck),
            static_cast<std::uint8_t>(sd::AckResult::kMaskMismatch));
  EXPECT_EQ(link.state(), EspState::kSessionIdle);
}

TEST_F(LinkTest, ArmOkEntersStreamingAndForwardsFreshSetpoints) {
  Ground g;
  stream(g);
  EXPECT_EQ(last_ack(g, MsgType::kArmControlAck),
            static_cast<std::uint8_t>(sd::AckResult::kOk));
  ASSERT_EQ(udp(g.setpoint(1, 1000, 500), 20), sd::RxResult::kAccepted);
  ASSERT_TRUE(link.fc_output(20, ch));
  EXPECT_EQ(ch[0], 2000); // roll
  EXPECT_EQ(ch[2], 1500); // throttle 500 -> 1500
  // A neutral (mode 0) setpoint sends neutral sticks.
  ASSERT_EQ(udp(g.setpoint(0, 1000, 500), 40), sd::RxResult::kAccepted);
  ASSERT_TRUE(link.fc_output(40, ch));
  EXPECT_TRUE(is_neutral(ch));
}

TEST_F(LinkTest, SetpointsSentBeforeArmNeverReachTheFc) {
  link.boot_done(0);
  Ground g;
  ASSERT_TRUE(handshake(g, 0, 0));
  ASSERT_EQ(udp(g.setpoint(1, 800, 700), 100), sd::RxResult::kAccepted);
  tcp(0, g.tcp(MsgType::kArmControlRequest, u8(kMask)), 110);
  ASSERT_EQ(link.state(), EspState::kStreaming);
  ASSERT_TRUE(link.fc_output(111, ch));
  EXPECT_TRUE(is_neutral(ch)); // not roll 1900 / throttle 1700
  ASSERT_EQ(udp(g.setpoint(1, 800, 700), 120), sd::RxResult::kAccepted);
  ASSERT_TRUE(link.fc_output(121, ch));
  EXPECT_EQ(ch[0], 1900);
}

TEST_F(LinkTest, ArmIsRefusedOutsideSessionIdle) {
  Ground g;
  stream(g);
  tcp(0, g.tcp(MsgType::kArmControlRequest, u8(kMask)), 20);
  EXPECT_EQ(last_ack(g, MsgType::kArmControlAck),
            static_cast<std::uint8_t>(sd::AckResult::kWrongState));
  link.tick(300); // LINK_FAILSAFE
  ASSERT_EQ(link.state(), EspState::kLinkFailsafe);
  tcp(0, g.tcp(MsgType::kArmControlRequest, u8(kMask)), 310);
  EXPECT_EQ(last_ack(g, MsgType::kArmControlAck),
            static_cast<std::uint8_t>(sd::AckResult::kWrongState));
  EXPECT_EQ(link.state(), EspState::kLinkFailsafe);
}

TEST_F(LinkTest, ArmWithoutSetpointsFailsAfterTLoss) {
  link.boot_done(0);
  Ground g;
  ASSERT_TRUE(handshake(g, 0, 0));
  tcp(0, g.tcp(MsgType::kArmControlRequest, u8(kMask)), 100);
  ASSERT_EQ(link.state(), EspState::kStreaming);
  link.tick(349);
  EXPECT_EQ(link.state(), EspState::kStreaming);
  link.tick(350);
  EXPECT_EQ(link.state(), EspState::kLinkFailsafe);
}

// --- T_fresh and T_loss
// -------------------------------------------------------

TEST_F(LinkTest, StaleSetpointBecomesNeutralAfterTFresh) {
  Ground g;
  stream(g);
  ASSERT_EQ(udp(g.setpoint(1, 1000), 100), sd::RxResult::kAccepted);
  ASSERT_TRUE(link.fc_output(249, ch));
  EXPECT_EQ(ch[0], 2000);
  ASSERT_TRUE(link.fc_output(250, ch));
  EXPECT_TRUE(is_neutral(ch));
  EXPECT_EQ(link.state(), EspState::kStreaming);
  link.tick(260);
  EXPECT_EQ(events(g, sd::EventCode::kSetpointStale), 1); // once per episode
  // The next valid setpoint is used at once, no handshake.
  ASSERT_EQ(udp(g.setpoint(1, -1000), 300), sd::RxResult::kAccepted);
  ASSERT_TRUE(link.fc_output(300, ch));
  EXPECT_EQ(ch[0], 1000);
}

TEST_F(LinkTest, LossAfterTLossEntersFailsafe) {
  Ground g;
  stream(g);
  ASSERT_EQ(udp(g.setpoint(1, 1000), 100), sd::RxResult::kAccepted);
  link.tick(349);
  EXPECT_EQ(link.state(), EspState::kStreaming);
  link.tick(350);
  EXPECT_EQ(link.state(), EspState::kLinkFailsafe);
  EXPECT_TRUE(link.failsafe_active());
  EXPECT_EQ(events(g, sd::EventCode::kLinkFailsafeEntered), 1);
  EXPECT_EQ(link.counters().failsafe_entered, 1U);
}

TEST_F(LinkTest, ALateSetpointDoesNotRescueStreaming) {
  Ground g;
  stream(g);
  ASSERT_EQ(udp(g.setpoint(1, 1000), 100), sd::RxResult::kAccepted);
  // Arrives after T_loss: the failsafe is entered first.
  EXPECT_EQ(udp(g.setpoint(1, 1000), 400), sd::RxResult::kAccepted);
  EXPECT_EQ(link.state(), EspState::kLinkFailsafe);
  ASSERT_TRUE(link.fc_output(400, ch));
  EXPECT_TRUE(is_neutral(ch));
}

TEST_F(LinkTest, RejectedFramesDoNotResetTheLossTimer) {
  Ground g;
  stream(g);
  const Bytes good = g.setpoint(1, 1000);
  ASSERT_EQ(udp(good, 100), sd::RxResult::kAccepted);
  EXPECT_EQ(udp(good, 200), sd::RxResult::kDuplicateSeq); // replay
  Bytes bad = g.setpoint(1, 1000);
  bad.back() ^= 1;
  EXPECT_EQ(udp(bad, 250), sd::RxResult::kBadTag);
  Ground stranger(0x55); // no session: wrong key and session
  stranger.session = g.session;
  EXPECT_EQ(udp(stranger.setpoint(1), 300), sd::RxResult::kBadTag);
  link.tick(350);
  EXPECT_EQ(link.state(), EspState::kLinkFailsafe);
  EXPECT_EQ(link.rx_rejected(), 3U);
}

// --- LINK_FAILSAFE output (ADR-004)
// -------------------------------------------

TEST_F(LinkTest, FailsafeOn2026SendsNeutralForOneSecondThenStops) {
  Ground g;
  stream(g);
  link.tick(260); // T_loss after ARM at 10: the output starts now
  ASSERT_EQ(link.state(), EspState::kLinkFailsafe);
  ASSERT_TRUE(link.fc_output(1259, ch));
  EXPECT_TRUE(is_neutral(ch));
  EXPECT_FALSE(link.fc_output(1260, ch));
  EXPECT_FALSE(link.fc_output(60000, ch));
}

TEST_F(LinkTest, FailsafeOn451SendsNeutralForever) {
  Io io451;
  sd::Link l(make_config(sd::FailsafeOutput::kNeutralForever), io451);
  l.boot_done(0);
  Ground g;
  ASSERT_EQ(l.on_tcp_frame(0, g.hello().data(), g.hello().size(), 0),
            sd::TcpVerdict::kKeep);
  ASSERT_TRUE(g.take_ack(io451.tcp.back().second));
  const Bytes arm = g.tcp(MsgType::kArmControlRequest, u8(kMask));
  l.on_tcp_frame(0, arm.data(), arm.size(), 10);
  l.tick(300);
  ASSERT_EQ(l.state(), EspState::kLinkFailsafe);
  ASSERT_TRUE(l.fc_output(60000, ch));
  EXPECT_TRUE(is_neutral(ch));
}

TEST_F(LinkTest, PacketsComingBackDoNotResumeControl) {
  Ground g;
  stream(g);
  link.tick(300);
  ASSERT_EQ(link.state(), EspState::kLinkFailsafe);
  for (std::uint32_t t = 400; t < 1400; t += 20) {
    ASSERT_EQ(udp(g.setpoint(1, 1000), t), sd::RxResult::kAccepted);
    EXPECT_EQ(link.state(), EspState::kLinkFailsafe);
    if (t < 1300) { // the ADR-004 output, never the ground's sticks
      ASSERT_TRUE(link.fc_output(t, ch));
      EXPECT_TRUE(is_neutral(ch));
    } else {
      EXPECT_FALSE(link.fc_output(t, ch));
    }
  }
}

// --- RESUME
// --------------------------------------------------------------------

TEST_F(LinkTest, ResumeNeedsFiveHundredMsOfNeutralStream) {
  Ground g;
  stream(g);
  link.tick(300);
  ASSERT_EQ(link.state(), EspState::kLinkFailsafe);
  std::uint32_t t = 400;
  for (; t < 880; t += 20) { // 400..860: 460 ms of neutral
    ASSERT_EQ(udp(g.setpoint(0), t), sd::RxResult::kAccepted);
  }
  tcp(0, g.tcp(MsgType::kResumeRequest), 870);
  EXPECT_EQ(last_ack(g, MsgType::kResumeAck),
            static_cast<std::uint8_t>(sd::AckResult::kResumeConditionNotMet));
  EXPECT_EQ(link.state(), EspState::kLinkFailsafe);
  for (; t <= 900; t += 20) { // 880, 900: neutral since 400 -> 500 ms
    ASSERT_EQ(udp(g.setpoint(0), t), sd::RxResult::kAccepted);
  }
  tcp(0, g.tcp(MsgType::kResumeRequest), 905);
  EXPECT_EQ(last_ack(g, MsgType::kResumeAck),
            static_cast<std::uint8_t>(sd::AckResult::kOk));
  EXPECT_EQ(link.state(), EspState::kStreaming);
  EXPECT_FALSE(link.failsafe_active());
  EXPECT_EQ(events(g, sd::EventCode::kLinkResumed), 1);
  ASSERT_EQ(udp(g.setpoint(1, 1000), 920), sd::RxResult::kAccepted);
  ASSERT_TRUE(link.fc_output(920, ch));
  EXPECT_EQ(ch[0], 2000);
}

TEST_F(LinkTest, AGapOrANonNeutralSetpointRestartsTheNeutralStream) {
  Ground g;
  stream(g);
  link.tick(300);
  std::uint32_t t = 400;
  for (; t <= 700; t += 20) {
    udp(g.setpoint(0), t);
  }
  udp(g.setpoint(1, 100), 720); // not neutral
  for (t = 740; t <= 1100; t += 20) {
    udp(g.setpoint(0), t);
  }
  tcp(0, g.tcp(MsgType::kResumeRequest), 1105); // neutral only since 740
  EXPECT_EQ(last_ack(g, MsgType::kResumeAck),
            static_cast<std::uint8_t>(sd::AckResult::kResumeConditionNotMet));
  // A 150 ms gap restarts it too.
  udp(g.setpoint(0), 1300);
  for (t = 1320; t <= 1780; t += 20) {
    udp(g.setpoint(0), t);
  }
  tcp(0, g.tcp(MsgType::kResumeRequest), 1785); // since 1300: 485 ms
  EXPECT_EQ(last_ack(g, MsgType::kResumeAck),
            static_cast<std::uint8_t>(sd::AckResult::kResumeConditionNotMet));
  udp(g.setpoint(0), 1800);
  tcp(0, g.tcp(MsgType::kResumeRequest), 1805);
  EXPECT_EQ(last_ack(g, MsgType::kResumeAck),
            static_cast<std::uint8_t>(sd::AckResult::kOk));
}

TEST_F(LinkTest, ResumeNeedsAFreshSetpoint) {
  Ground g;
  stream(g);
  link.tick(300);
  for (std::uint32_t t = 400; t <= 1000; t += 20) {
    udp(g.setpoint(0), t);
  }
  tcp(0, g.tcp(MsgType::kResumeRequest), 1150); // last setpoint 150 ms old
  EXPECT_EQ(last_ack(g, MsgType::kResumeAck),
            static_cast<std::uint8_t>(sd::AckResult::kResumeConditionNotMet));
}

TEST_F(LinkTest, ResumeOutsideFailsafeIsWrongState) {
  link.boot_done(0);
  Ground g;
  ASSERT_TRUE(handshake(g, 0, 0));
  tcp(0, g.tcp(MsgType::kResumeRequest), 10);
  EXPECT_EQ(last_ack(g, MsgType::kResumeAck),
            static_cast<std::uint8_t>(sd::AckResult::kWrongState));
  EXPECT_EQ(link.state(), EspState::kSessionIdle);
}

// --- Pending session (#156)
// ----------------------------------------------------

TEST_F(LinkTest, NewHelloDuringStreamingOpensAPendingSessionOnly) {
  Ground g;
  stream(g);
  feed(g, 20, 600);
  const std::uint32_t old_id = g.session;
  Ground g2(0x33);
  ASSERT_TRUE(handshake(g2, 1, 600));
  EXPECT_TRUE(link.has_pending());
  EXPECT_EQ(link.pending_conn(), 1);
  EXPECT_EQ(link.state(), EspState::kStreaming);
  EXPECT_EQ(link.session_id(), old_id);
  // The old session keeps flying.
  ASSERT_EQ(udp(g.setpoint(1, 1000), 610), sd::RxResult::kAccepted);
  ASSERT_TRUE(link.fc_output(610, ch));
  EXPECT_EQ(ch[0], 2000);
}

TEST_F(LinkTest, ReplayedHelloNeverEndsTheLiveSession) {
  Ground g;
  stream(g);
  const Bytes recorded = g.hello(); // as sent at t=0
  std::uint32_t t = 20;
  for (int i = 0; i < 5; ++i) { // replayed every 600 ms on another socket
    const std::uint32_t at = 600 + 600U * static_cast<std::uint32_t>(i);
    for (; t < at; t += 20) {
      ASSERT_EQ(udp(g.setpoint(1, 1000), t), sd::RxResult::kAccepted);
    }
    EXPECT_EQ(tcp(2, recorded, at), sd::TcpVerdict::kKeep);
    EXPECT_EQ(link.state(), EspState::kStreaming);
  }
  // The replayer cannot produce a frame under the new keys: nothing confirms.
  for (; t < 6000; t += 20) {
    ASSERT_EQ(udp(g.setpoint(1, 1000), t), sd::RxResult::kAccepted);
  }
  EXPECT_EQ(link.state(), EspState::kStreaming);
  EXPECT_FALSE(link.has_pending());
  EXPECT_GE(link.counters().pending_expired, 1U);
  EXPECT_EQ(link.counters().sessions_switched, 0U);
  EXPECT_TRUE(io.was_closed(2)); // the HELLO-only connection is closed
  EXPECT_FALSE(io.was_closed(0));
}

TEST_F(LinkTest, PendingSessionExpiresAfterTwoSeconds) {
  Ground g;
  link.boot_done(0);
  ASSERT_TRUE(handshake(g, 0, 0));
  Ground g2(0x33);
  ASSERT_TRUE(handshake(g2, 1, 600));
  link.tick(2599);
  EXPECT_TRUE(link.has_pending());
  link.tick(2600);
  EXPECT_FALSE(link.has_pending());
  EXPECT_TRUE(io.was_closed(1));
  // A frame under the expired keys no longer switches anything.
  EXPECT_EQ(udp(g2.setpoint(0), 2610), sd::RxResult::kBadTag);
  EXPECT_EQ(link.session_id(), g.session);
}

TEST_F(LinkTest, FirstValidFrameUnderTheNewKeysConfirmsTheSwitch) {
  Ground g;
  stream(g);
  feed(g, 20, 600);
  Ground g2(0x33);
  ASSERT_TRUE(handshake(g2, 1, 600));
  ASSERT_EQ(udp(g2.setpoint(0), 620), sd::RxResult::kAccepted);
  EXPECT_EQ(link.session_id(), g2.session);
  EXPECT_EQ(link.session_conn(), 1);
  EXPECT_EQ(link.state(), EspState::kSessionIdle);
  EXPECT_EQ(link.counters().sessions_switched, 1U);
  // BYE reason 3 to the old session on its own connection, which is closed.
  bool bye = false;
  for (const auto &m : io.tcp) {
    sd::Frame f{};
    if (m.first == 0 && g.open(m.second, f) &&
        f.header.type == static_cast<std::uint8_t>(MsgType::kBye)) {
      bye = f.payload[0] == static_cast<std::uint8_t>(sd::ByeReason::kReplaced);
    }
  }
  EXPECT_TRUE(bye);
  EXPECT_TRUE(io.was_closed(0));
  // Old-session traffic is rejected now.
  EXPECT_EQ(udp(g.setpoint(1, 1000), 630), sd::RxResult::kBadTag);
}

TEST_F(LinkTest, SwitchFromStreamingKeepsTheFailsafeOutputUntilArm) {
  Ground g;
  stream(g);
  feed(g, 20, 600);
  Ground g2(0x33);
  ASSERT_TRUE(handshake(g2, 1, 600));
  ASSERT_EQ(udp(g2.setpoint(1, 1000, 800), 620), sd::RxResult::kAccepted);
  ASSERT_EQ(link.state(), EspState::kSessionIdle);
  EXPECT_TRUE(link.failsafe_active());
  ASSERT_TRUE(link.fc_output(1619, ch)); // ADR-004: neutral for 1 s...
  EXPECT_TRUE(is_neutral(ch));
  EXPECT_FALSE(link.fc_output(1620, ch)); // ...then stop
  // The new session's ARM ends it.
  for (std::uint32_t t = 640; t <= 1700; t += 20) {
    udp(g2.setpoint(1, 1000, 800), t);
  }
  tcp(1, g2.tcp(MsgType::kArmControlRequest, u8(kMask)), 1705);
  EXPECT_EQ(link.state(), EspState::kStreaming);
  EXPECT_FALSE(link.failsafe_active());
  ASSERT_EQ(udp(g2.setpoint(1, 1000, 800), 1710), sd::RxResult::kAccepted);
  ASSERT_TRUE(link.fc_output(1710, ch));
  EXPECT_EQ(ch[0], 2000);
}

TEST_F(LinkTest, SwitchFromFailsafeKeepsItsTimeline) {
  Ground g;
  stream(g);
  link.tick(300); // loss detected: failsafe output since 300
  ASSERT_EQ(link.state(), EspState::kLinkFailsafe);
  Ground g2(0x33);
  ASSERT_TRUE(handshake(g2, 1, 900));
  tcp(1, g2.tcp(MsgType::kConfigGet, config_kv(0x01, 0)), 1000); // confirms
  ASSERT_EQ(link.state(), EspState::kSessionIdle);
  EXPECT_EQ(link.session_id(), g2.session);
  EXPECT_TRUE(link.failsafe_active());
  ASSERT_TRUE(link.fc_output(1299, ch));
  EXPECT_FALSE(link.fc_output(1300, ch)); // 1 s after 300, not after 1000
  // RESUME is not possible in the new session: it needs ARM (REQ-COM-020).
  tcp(1, g2.tcp(MsgType::kResumeRequest), 1300);
  EXPECT_EQ(last_ack(g2, MsgType::kResumeAck),
            static_cast<std::uint8_t>(sd::AckResult::kWrongState));
}

TEST_F(LinkTest, SwitchFromSessionIdleSendsNeutralAtOnce) {
  link.boot_done(0);
  Ground g;
  ASSERT_TRUE(handshake(g, 0, 0));
  Ground g2(0x33);
  ASSERT_TRUE(handshake(g2, 1, 600));
  ASSERT_EQ(udp(g2.setpoint(0), 620), sd::RxResult::kAccepted);
  EXPECT_FALSE(link.failsafe_active());
  ASSERT_TRUE(link.fc_output(5000, ch));
  EXPECT_TRUE(is_neutral(ch));
}

TEST_F(LinkTest, RehandshakeOnTheSameConnectionKeepsIt) {
  Ground g;
  stream(g);
  feed(g, 20, 600);
  Ground g2(0x33);
  ASSERT_TRUE(handshake(g2, 0, 600));
  ASSERT_EQ(link.pending_conn(), 0);
  tcp(0, g2.tcp(MsgType::kConfigGet, config_kv(0x02, 0)), 620);
  EXPECT_EQ(link.session_id(), g2.session);
  EXPECT_EQ(link.session_conn(), 0);
  EXPECT_FALSE(io.was_closed(0));
  // BYE 3 went out on connection 0 under the old keys.
  EXPECT_EQ(last_ack(g, MsgType::kBye),
            static_cast<std::uint8_t>(sd::ByeReason::kReplaced));
  EXPECT_EQ(last_ack(g2, MsgType::kConfigAck), 0x02);
}

TEST_F(LinkTest, OldSessionFramesStillWorkOnASharedConnection) {
  Ground g;
  stream(g);
  feed(g, 20, 600);
  Ground g2(0x33);
  ASSERT_TRUE(handshake(g2, 0, 600)); // pending on the same connection
  tcp(0, g.tcp(MsgType::kConfigGet, config_kv(0x01, 0)), 610);
  EXPECT_EQ(last_ack(g, MsgType::kConfigAck), 0x01);
  EXPECT_EQ(link.session_id(), g.session);
  EXPECT_TRUE(link.has_pending());
}

TEST_F(LinkTest, HelloIsRateLimitedAndARefusedOneDoesNotRestartTheWindow) {
  link.boot_done(0);
  Ground g;
  ASSERT_TRUE(handshake(g, 0, 0));
  Ground g2(0x33);
  EXPECT_FALSE(handshake(g2, 1, 300)); // refused: 300 ms after the last one
  EXPECT_EQ(link.counters().hello_rate_limited, 1U);
  EXPECT_FALSE(link.has_pending());
  EXPECT_TRUE(handshake(g2, 1, 500)); // 500 ms after the accepted one
  EXPECT_TRUE(link.has_pending());
}

TEST_F(LinkTest, ALaterHelloReplacesThePendingOne) {
  Ground g;
  stream(g);
  feed(g, 20, 600);
  Ground g2(0x33);
  ASSERT_TRUE(handshake(g2, 1, 600));
  feed(g, 600, 1100);
  Ground g3(0x77);
  ASSERT_TRUE(handshake(g3, 2, 1100));
  EXPECT_EQ(link.pending_conn(), 2);
  EXPECT_TRUE(io.was_closed(1));
  EXPECT_EQ(udp(g2.setpoint(0), 1120), sd::RxResult::kBadTag);
  EXPECT_EQ(link.session_id(), g.session);
  EXPECT_EQ(udp(g3.setpoint(0), 1140), sd::RxResult::kAccepted);
  EXPECT_EQ(link.session_id(), g3.session);
}

TEST_F(LinkTest, PendingSessionIsDroppedWhenItsConnectionCloses) {
  Ground g;
  stream(g);
  feed(g, 20, 600);
  Ground g2(0x33);
  ASSERT_TRUE(handshake(g2, 1, 600));
  link.on_tcp_closed(1, 700);
  EXPECT_FALSE(link.has_pending());
  EXPECT_EQ(udp(g2.setpoint(0), 710), sd::RxResult::kBadTag);
  EXPECT_EQ(link.state(), EspState::kStreaming);
}

// --- TCP drops and BYE (ADR-005)
// -------------------------------------------------

TEST_F(LinkTest, TcpDropWhileStreamingChangesNothingWhileUdpFlows) {
  Ground g;
  stream(g);
  link.on_tcp_closed(0, 100);
  EXPECT_EQ(link.session_conn(), sd::kNoConn);
  for (std::uint32_t t = 20; t <= 2000; t += 20) {
    ASSERT_EQ(udp(g.setpoint(1, 1000), t), sd::RxResult::kAccepted);
  }
  EXPECT_EQ(link.state(), EspState::kStreaming);
  link.send_state(2000, sd::FcView{});
  EXPECT_EQ(io.udp.size(), 1U); // STATE still goes out over UDP
}

TEST_F(LinkTest, ReconnectAfterATcpDropNeverInterruptsControlOutput) {
  Ground g;
  stream(g);
  link.on_tcp_closed(0, 100);
  std::uint32_t t = 20;
  for (; t <= 500; t += 20) {
    ASSERT_EQ(udp(g.setpoint(1, 1000), t), sd::RxResult::kAccepted);
  }
  Ground g2(0x33);
  ASSERT_TRUE(handshake(g2, 1, 510));
  ASSERT_EQ(udp(g.setpoint(1, 1000), 520), sd::RxResult::kAccepted);
  // The ground confirms with ARM as its first frame under the new keys.
  tcp(1, g2.tcp(MsgType::kArmControlRequest, u8(kMask)), 530);
  EXPECT_EQ(link.state(), EspState::kStreaming);
  EXPECT_EQ(link.session_id(), g2.session);
  ASSERT_EQ(udp(g2.setpoint(1, 1000), 540), sd::RxResult::kAccepted);
  ASSERT_TRUE(link.fc_output(540, ch));
  EXPECT_EQ(ch[0], 2000);
}

TEST_F(LinkTest, SessionIdleWithNoTcpAndNoUdpEndsTheSession) {
  link.boot_done(0);
  Ground g;
  ASSERT_TRUE(handshake(g, 0, 0));
  ASSERT_EQ(udp(g.setpoint(0), 100), sd::RxResult::kAccepted);
  link.on_tcp_closed(0, 200);
  EXPECT_EQ(link.state(), EspState::kSessionIdle);
  link.tick(349);
  EXPECT_EQ(link.state(), EspState::kSessionIdle);
  link.tick(350);
  EXPECT_EQ(link.state(), EspState::kNoSession);
  EXPECT_FALSE(link.has_session());
}

TEST_F(LinkTest, SessionIdleWithNoTcpButUdpKeepsTheSession) {
  link.boot_done(0);
  Ground g;
  ASSERT_TRUE(handshake(g, 0, 0));
  link.on_tcp_closed(0, 10);
  for (std::uint32_t t = 20; t <= 2000; t += 20) {
    ASSERT_EQ(udp(g.setpoint(0), t), sd::RxResult::kAccepted);
  }
  EXPECT_EQ(link.state(), EspState::kSessionIdle);
}

TEST_F(LinkTest, ByeInSessionIdleEndsTheSession) {
  link.boot_done(0);
  Ground g;
  ASSERT_TRUE(handshake(g, 0, 0));
  const std::size_t sent = io.tcp.size();
  tcp(0, g.tcp(MsgType::kBye, u8(0)), 10);
  EXPECT_EQ(link.state(), EspState::kNoSession);
  EXPECT_EQ(io.tcp.size(), sent); // BYE gets no reply
  EXPECT_TRUE(io.was_closed(0));
  EXPECT_FALSE(link.failsafe_active());
  ASSERT_TRUE(link.fc_output(20, ch));
  EXPECT_TRUE(is_neutral(ch));
}

TEST_F(LinkTest, ByeInFailsafeEndsTheSessionAndKeepsItsTimeline) {
  Ground g;
  stream(g);
  link.tick(300);
  ASSERT_EQ(link.state(), EspState::kLinkFailsafe);
  tcp(0, g.tcp(MsgType::kBye, u8(0)), 500);
  EXPECT_EQ(link.state(), EspState::kNoSession);
  EXPECT_TRUE(link.failsafe_active());
  ASSERT_TRUE(link.fc_output(1299, ch));
  EXPECT_FALSE(link.fc_output(1300, ch)); // 1 s after 300, not after 500
}

TEST_F(LinkTest, ByeAsTheFirstFrameOfAPendingSession) {
  Ground g;
  stream(g);
  feed(g, 20, 600);
  Ground g2(0x33);
  ASSERT_TRUE(handshake(g2, 1, 600));
  tcp(1, g2.tcp(MsgType::kBye, u8(0)), 610);
  EXPECT_EQ(last_ack(g, MsgType::kBye),
            static_cast<std::uint8_t>(sd::ByeReason::kReplaced));
  EXPECT_EQ(link.state(), EspState::kNoSession);
  EXPECT_FALSE(link.has_session());
  EXPECT_TRUE(link.failsafe_active()); // left STREAMING: ADR-004 output
}

TEST_F(LinkTest, APendingSessionOutlivesAByeOfTheCurrentOne) {
  Ground g;
  stream(g);
  feed(g, 20, 600);
  Ground g2(0x33);
  ASSERT_TRUE(handshake(g2, 1, 600));
  tcp(0, g.tcp(MsgType::kBye, u8(0)), 610);
  EXPECT_EQ(link.state(), EspState::kNoSession);
  EXPECT_TRUE(link.has_pending());
  const std::size_t sent = io.tcp.size();
  ASSERT_EQ(udp(g2.setpoint(0), 620), sd::RxResult::kAccepted);
  EXPECT_EQ(link.state(), EspState::kSessionIdle);
  EXPECT_EQ(link.session_id(), g2.session);
  // No old session left: no BYE reason 3, only EVENT 0x0104 to the new one.
  ASSERT_EQ(io.tcp.size(), sent + 1);
  sd::Frame f{};
  ASSERT_TRUE(g2.open(io.tcp.back().second, f));
  EXPECT_EQ(f.header.type, static_cast<std::uint8_t>(MsgType::kEvent));
  EXPECT_TRUE(link.failsafe_active());
}

TEST_F(LinkTest, FailsafeStopSurvivesAClockWrap) {
  Ground g;
  stream(g);
  link.tick(300);
  EXPECT_FALSE(link.fc_output(1300, ch));
  // 2^32 ms later the clock reads the same values again: still stopped.
  EXPECT_FALSE(link.fc_output(500, ch));
}

TEST_F(LinkTest, ByeOfTheOldSessionKeepsASharedConnectionOpen) {
  Ground g;
  stream(g);
  feed(g, 20, 600);
  Ground g2(0x33);
  ASSERT_TRUE(handshake(g2, 0, 600)); // pending on the session's connection
  tcp(0, g.tcp(MsgType::kBye, u8(0)), 610);
  EXPECT_EQ(link.state(), EspState::kNoSession);
  EXPECT_FALSE(io.was_closed(0)); // the pending session still needs it
  EXPECT_EQ(link.pending_conn(), 0);
  tcp(0, g2.tcp(MsgType::kConfigGet, config_kv(0x01, 0)), 620);
  EXPECT_EQ(link.session_id(), g2.session);
  EXPECT_EQ(link.session_conn(), 0);
  EXPECT_EQ(last_ack(g2, MsgType::kConfigAck), 0x01);
}

TEST_F(LinkTest, ReplacingASharedPendingSessionKeepsTheConnection) {
  Ground g;
  stream(g);
  feed(g, 20, 600);
  Ground g2(0x33);
  ASSERT_TRUE(handshake(g2, 0, 600)); // pending on the session's connection
  feed(g, 600, 1100);
  Ground g3(0x77);
  ASSERT_TRUE(handshake(g3, 1, 1100)); // replaces it from another one
  EXPECT_EQ(link.pending_conn(), 1);
  EXPECT_FALSE(io.was_closed(0)); // still the current session's connection
  EXPECT_EQ(link.session_conn(), 0);
}

TEST_F(LinkTest, NewSessionIsToldTheFailsafeOutputStillRuns) {
  Ground g;
  stream(g);
  tcp(0, g.tcp(MsgType::kBye, u8(0)), 30); // ADR-004 output from now
  Ground g2(0x33);
  ASSERT_TRUE(handshake(g2, 1, 600)); // NO_SESSION: opens at once
  EXPECT_EQ(events(g2, sd::EventCode::kFailsafeOutputActive), 1);
  // A switch from STREAMING tells the new session too.
  Ground g3(0x55);
  tcp(1, g2.tcp(MsgType::kArmControlRequest, u8(kMask)), 610);
  feed(g2, 620, 1200);
  ASSERT_TRUE(handshake(g3, 2, 1200));
  ASSERT_EQ(udp(g3.setpoint(0), 1210), sd::RxResult::kAccepted);
  EXPECT_EQ(events(g3, sd::EventCode::kFailsafeOutputActive), 1);
  // Without the ADR-004 output, a new session is not told anything.
  Io io2;
  sd::Link quiet(make_config(sd::FailsafeOutput::kNeutralThenStop), io2);
  quiet.boot_done(0);
  const Bytes hello = g.hello();
  quiet.on_tcp_frame(0, hello.data(), hello.size(), 0);
  EXPECT_EQ(io2.tcp.size(), 1U); // only the HELLO_ACK
}

TEST_F(LinkTest, ByeWhileStreamingKeepsTheFailsafeOutput) {
  Ground g;
  stream(g);
  ASSERT_EQ(udp(g.setpoint(1, 1000, 800), 20), sd::RxResult::kAccepted);
  tcp(0, g.tcp(MsgType::kBye, u8(0)), 30);
  EXPECT_EQ(link.state(), EspState::kNoSession);
  EXPECT_TRUE(link.failsafe_active());
  ASSERT_TRUE(link.fc_output(1029, ch));
  EXPECT_TRUE(is_neutral(ch));
  EXPECT_FALSE(link.fc_output(1030, ch));
  // A new session does not clear it; only its ARM does.
  Ground g2(0x33);
  ASSERT_TRUE(handshake(g2, 1, 2000));
  EXPECT_TRUE(link.failsafe_active());
  EXPECT_FALSE(link.fc_output(2000, ch));
}

// --- CONFIG (spec §3.1, §7.3)
// ------------------------------------------------------

TEST_F(LinkTest, ConfigGetAnswersKnownAndUnknownKeys) {
  link.boot_done(0);
  Ground g;
  ASSERT_TRUE(handshake(g, 0, 0));
  struct Case {
    std::uint8_t key;
    std::uint8_t result;
    std::int32_t value;
  };
  const Case cases[] = {{0x01, 0, 1},   {0x02, 0, 1},    {0x40, 0, 250},
                        {0x41, 0, 150}, {0x42, 0, 1000}, {0x80, 0, 25},
                        {0x81, 0, 1},   {0x03, 1, 0},    {0xF0, 1, 0},
                        {0x00, 1, 0}};
  std::uint32_t t = 10;
  for (const Case &c : cases) {
    tcp(0, g.tcp(MsgType::kConfigGet, config_kv(c.key, 0)), t += 10);
    sd::Frame f{};
    ASSERT_TRUE(g.open(io.tcp.back().second, f));
    sd::ConfigAck ack{};
    ASSERT_TRUE(sd::decode_config_ack(f.payload, f.header.payload_len, ack));
    EXPECT_EQ(ack.key, c.key);
    EXPECT_EQ(ack.result, c.result) << int(c.key);
    EXPECT_EQ(ack.value, c.value) << int(c.key);
  }
}

TEST_F(LinkTest, ConfigSetChecksStateFirstThenTheKey) {
  Ground g;
  stream(g);
  auto set = [&](std::uint8_t key, std::int32_t v, std::uint32_t t) {
    tcp(0, g.tcp(MsgType::kConfigSet, config_kv(key, v)), t);
    sd::Frame f{};
    EXPECT_TRUE(g.open(io.tcp.back().second, f));
    sd::ConfigAck ack{};
    EXPECT_TRUE(sd::decode_config_ack(f.payload, f.header.payload_len, ack));
    return ack;
  };
  // STREAMING: "refused while control is enabled", whatever the key.
  EXPECT_EQ(set(0x80, 30, 20).result, 5);
  EXPECT_EQ(set(0x03, 30, 30).result, 5);
  EXPECT_EQ(link.telemetry_rate_hz(), 25);
  link.tick(300);                         // LINK_FAILSAFE: writes allowed again
  EXPECT_EQ(set(0x03, 1, 310).result, 1); // unknown
  EXPECT_EQ(set(0x01, 2, 320).result, 2); // read-only
  EXPECT_EQ(set(0x40, 300, 330).result, 3); // safety
  const sd::ConfigAck out = set(0x80, 51, 340);
  EXPECT_EQ(out.result, 4); // out of range, never clamped
  EXPECT_EQ(out.value, 25);
  EXPECT_EQ(set(0x81, -1, 350).result, 4);
  const sd::ConfigAck ok = set(0x80, 50, 360);
  EXPECT_EQ(ok.result, 0);
  EXPECT_EQ(ok.value, 50);
  EXPECT_EQ(link.telemetry_rate_hz(), 50);
  EXPECT_EQ(set(0x81, 3, 370).result, 0);
  EXPECT_EQ(link.event_level(), 3);
}

// --- STATE
// ---------------------------------------------------------------------------

TEST_F(LinkTest, StateCarriesTheLinkFieldsAndTheFcView) {
  Ground g;
  stream(g);
  ASSERT_EQ(udp(g.setpoint(1, 1000), 20), sd::RxResult::kAccepted);
  Bytes bad = g.setpoint(1);
  bad.back() ^= 1;
  udp(bad, 25);
  sd::FcView fc{};
  fc.override_active = 1;
  fc.roll = -45;
  fc.vbat = 1620;
  fc.msp_errors = 2;
  link.send_state(30, fc);
  ASSERT_EQ(io.udp.size(), 1U);
  sd::Frame f{};
  ASSERT_TRUE(g.open(io.udp.back(), f));
  EXPECT_EQ(f.header.type, static_cast<std::uint8_t>(MsgType::kState));
  EXPECT_EQ(f.header.seq, 1U);
  sd::State st{};
  ASSERT_TRUE(sd::decode_state(f.payload, f.header.payload_len, st));
  EXPECT_EQ(st.esp_state, static_cast<std::uint8_t>(EspState::kStreaming));
  EXPECT_EQ(st.override_active, 1);
  EXPECT_EQ(st.last_ctrl_seq, 2U);
  EXPECT_EQ(st.roll, -45);
  EXPECT_EQ(st.vbat, 1620);
  EXPECT_EQ(st.rx_rejected, 1);
  EXPECT_EQ(st.msp_errors, 2);
  link.send_state(40, fc);
  ASSERT_TRUE(g.open(io.udp.back(), f));
  EXPECT_EQ(f.header.seq, 2U);
}

TEST_F(LinkTest, EspFramesAreAcceptedByTheGroundReceiver) {
  Ground g;
  stream(g);
  sd::Receiver rx(sd::Role::kGround, sd::Transport::kTcp);
  ASSERT_TRUE(rx.start_session(g.session, g.keys.esp_to_ground));
  // Every in-session TCP frame the ESP sent (skip the PSK-tagged HELLO_ACK).
  for (std::size_t i = 1; i < io.tcp.size(); ++i) {
    sd::Frame f{};
    EXPECT_TRUE(sd::is_accepted(
        rx.receive(io.tcp[i].second.data(), io.tcp[i].second.size(), f)));
  }
}

} // namespace
