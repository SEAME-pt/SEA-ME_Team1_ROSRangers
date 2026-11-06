#include <gtest/gtest.h>

#include <cstring>
#include <string>
#include <vector>

#include "sd/hmac.hpp"
#include "sd/receiver.hpp"
#include "sd/setpoint.hpp"

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

// The UDP Receiver: the reference frames above are SETPOINTs.
// An ESP-side Receiver with kSession open (the reference frames are SETPOINTs).
struct OpenRx : sd::Receiver {
  explicit OpenRx(sd::Transport t = sd::Transport::kUdp,
                  sd::Role role = sd::Role::kEsp)
      : sd::Receiver(role, t) {
    EXPECT_TRUE(start_session(kSession, test_key()));
  }
};

// A neutral SETPOINT (mode 0, all zero) with any session and seq.
std::vector<std::uint8_t> encode(std::uint32_t session, std::uint32_t seq) {
  const sd::Header h{
      static_cast<std::uint8_t>(sd::MsgType::kSetpoint), 0, 0, session, seq, 0};
  const std::uint8_t payload[sd::kSetpointSize] = {};
  std::vector<std::uint8_t> out(sd::kMaxFrame);
  out.resize(sd::encode_frame(h, payload, sizeof(payload), test_key(),
                              out.data(), out.size()));
  return out;
}

// A frame with any type and payload, in kSession, with a valid tag.
std::vector<std::uint8_t> frame_of(std::uint8_t type,
                                   const std::vector<std::uint8_t> &payload,
                                   std::uint32_t seq) {
  const sd::Header h{type, 0, 0, kSession, seq, 0};
  std::vector<std::uint8_t> out(sd::kMaxFrame);
  out.resize(sd::encode_frame(h, payload.data(), payload.size(), test_key(),
                              out.data(), out.size()));
  return out;
}

} // namespace

TEST(Receiver, NothingIsAcceptedBeforeASession) {
  sd::Receiver rx(sd::Role::kEsp, sd::Transport::kUdp);
  EXPECT_FALSE(rx.in_session());
  EXPECT_EQ(feed(rx, kSeq1), sd::RxResult::kNoSession);
  EXPECT_EQ(rx.counters().no_session, 1U);
  EXPECT_EQ(rx.counters().rejected, 1U);
}

TEST(Receiver, SessionIdZeroIsReservedForHello) {
  sd::Receiver rx(sd::Role::kEsp, sd::Transport::kUdp);
  EXPECT_FALSE(rx.start_session(0, test_key()));
  EXPECT_FALSE(rx.in_session());
}

TEST(Receiver, AcceptsAValidFrameAndReturnsItsFields) {
  OpenRx rx;
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
  OpenRx rx;
  EXPECT_EQ(feed(rx, kOtherSession), sd::RxResult::kWrongSession);
  EXPECT_EQ(rx.counters().wrong_session, 1U);
  EXPECT_EQ(rx.last_seq(), 0U); // a rejected frame never moves the window
}

TEST(Receiver, SessionIdZeroOutsideHelloRejected) {
  OpenRx rx;
  const auto bytes = encode(0, 1);
  sd::Frame f{};
  EXPECT_EQ(rx.receive(bytes.data(), bytes.size(), f),
            sd::RxResult::kWrongSession);
}

// Vector group 5 (sequence): the seq_duplicate example from the README.
TEST(Receiver, SeqDuplicateExample) {
  OpenRx rx;
  EXPECT_EQ(feed(rx, kSeq1), sd::RxResult::kAccepted);
  EXPECT_EQ(feed(rx, kSeq2), sd::RxResult::kAccepted);
  EXPECT_EQ(feed(rx, kSeq2), sd::RxResult::kDuplicateSeq);
  EXPECT_EQ(feed(rx, kSeq3), sd::RxResult::kAccepted);
  EXPECT_EQ(rx.last_seq(), 3U);
  EXPECT_EQ(rx.counters().rejected, 1U);
  EXPECT_EQ(rx.counters().duplicate_seq, 1U);
}

TEST(Receiver, OlderAndTooHighRejected) {
  OpenRx rx;
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
  OpenRx rx;
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
  sd::Receiver rx(sd::Role::kEsp, sd::Transport::kUdp);
  sd::Key other = test_key();
  other.bytes[31] ^= 0xff;
  ASSERT_TRUE(rx.start_session(kSession, other));
  EXPECT_EQ(feed(rx, kSeq1), sd::RxResult::kBadTag);
}

TEST(Receiver, NewSessionResetsTheWindowAndEndSessionClosesIt) {
  OpenRx rx;
  EXPECT_EQ(feed(rx, kSeq3), sd::RxResult::kAcceptedGap);
  ASSERT_TRUE(rx.start_session(kSession, test_key()));
  EXPECT_EQ(rx.last_seq(), 0U);
  EXPECT_EQ(feed(rx, kSeq1), sd::RxResult::kAccepted);

  rx.end_session();
  EXPECT_FALSE(rx.in_session());
  EXPECT_EQ(rx.session_id(), 0U);
  EXPECT_EQ(feed(rx, kSeq2), sd::RxResult::kNoSession);
}

TEST(Receiver, RejectedFrameNeverReachesTheOutput) {
  OpenRx rx;
  ASSERT_EQ(feed(rx, kSeq3), sd::RxResult::kAcceptedGap);
  const auto replay = from_hex(kSeq2); // valid tag, older seq
  sd::Frame f{};
  EXPECT_EQ(rx.receive(replay.data(), replay.size(), f), sd::RxResult::kOldSeq);
  EXPECT_EQ(f.payload, nullptr);
  EXPECT_EQ(f.header.seq, 0U);
}

// REQ-COM-001 and REQ-COM-004: a frame with a good tag but an unknown type, a
// wrong payload size or a bad SETPOINT.mode is rejected, counted, and does not
// use up its seq number.
TEST(Receiver, MalformedPayloadRejectedWithoutUsingTheSeq) {
  OpenRx rx;
  const auto setpoint = static_cast<std::uint8_t>(sd::MsgType::kSetpoint);
  std::vector<std::uint8_t> sp(sd::kSetpointSize, 0);
  sd::Frame f{};

  auto bad = frame_of(0x99, sp, 5);
  EXPECT_EQ(rx.receive(bad.data(), bad.size(), f), sd::RxResult::kBadType);
  bad = frame_of(setpoint, std::vector<std::uint8_t>(9, 0), 5);
  EXPECT_EQ(rx.receive(bad.data(), bad.size(), f),
            sd::RxResult::kBadPayloadSize);
  sp[0] = 2;
  bad = frame_of(setpoint, sp, 5);
  EXPECT_EQ(rx.receive(bad.data(), bad.size(), f), sd::RxResult::kBadMode);
  EXPECT_EQ(f.payload, nullptr);

  const auto &c = rx.counters();
  EXPECT_EQ(c.bad_type, 1U);
  EXPECT_EQ(c.bad_payload_size, 1U);
  EXPECT_EQ(c.bad_mode, 1U);
  EXPECT_EQ(c.rejected, 3U);
  EXPECT_EQ(c.accepted, 0U);
  EXPECT_EQ(rx.last_seq(), 0U);

  // seq 1 is still free.
  sp[0] = 1;
  const auto good = frame_of(setpoint, sp, 1);
  EXPECT_EQ(rx.receive(good.data(), good.size(), f), sd::RxResult::kAccepted);
}

// TCP Receiver: HELLO used seq 1, so the session starts at 1 and seq 2 is in
// order, with no false gap.
TEST(Receiver, TcpSessionStartsAfterHello) {
  OpenRx tcp(sd::Transport::kTcp);
  EXPECT_EQ(tcp.last_seq(), 1U);
  const auto arm = static_cast<std::uint8_t>(sd::MsgType::kArmControlRequest);
  auto f1 = frame_of(arm, {15}, 1);
  auto f2 = frame_of(arm, {15}, 2);
  sd::Frame f{};
  EXPECT_EQ(tcp.receive(f1.data(), f1.size(), f), sd::RxResult::kDuplicateSeq);
  EXPECT_EQ(tcp.receive(f2.data(), f2.size(), f), sd::RxResult::kAccepted);
  EXPECT_EQ(tcp.seq_counters().gaps, 0U);
}

// Spec §2: both transports share the direction key, so each Receiver accepts
// only its own types. A captured TCP frame replayed on UDP, or a SETPOINT sent
// on TCP, is refused and moves neither window.
TEST(Receiver, FrameFromTheOtherTransportRejected) {
  OpenRx udp(sd::Transport::kUdp);
  OpenRx tcp(sd::Transport::kTcp);
  const auto arm = static_cast<std::uint8_t>(sd::MsgType::kArmControlRequest);
  const auto replay = frame_of(arm, {15}, 2);
  const auto setpoint = encode(kSession, 50);
  sd::Frame f{};
  ASSERT_EQ(tcp.receive(replay.data(), replay.size(), f),
            sd::RxResult::kAccepted);
  EXPECT_EQ(udp.receive(replay.data(), replay.size(), f),
            sd::RxResult::kWrongTransport);
  EXPECT_EQ(tcp.receive(setpoint.data(), setpoint.size(), f),
            sd::RxResult::kWrongTransport);
  EXPECT_EQ(udp.last_seq(), 0U);
  EXPECT_EQ(tcp.last_seq(), 2U);
  EXPECT_EQ(udp.counters().wrong_transport, 1U);
  EXPECT_EQ(tcp.counters().wrong_transport, 1U);
  // The real SETPOINT still goes through on UDP.
  EXPECT_EQ(udp.receive(setpoint.data(), setpoint.size(), f),
            sd::RxResult::kAcceptedGap);
}

// A refused start_session leaves the open session as it was.
TEST(Receiver, RefusedStartKeepsTheOpenSession) {
  OpenRx rx;
  ASSERT_EQ(feed(rx, kSeq1), sd::RxResult::kAccepted);
  EXPECT_FALSE(rx.start_session(0, test_key()));
  EXPECT_TRUE(rx.in_session());
  EXPECT_EQ(rx.session_id(), kSession);
  EXPECT_EQ(rx.last_seq(), 1U);
  EXPECT_EQ(feed(rx, kSeq2), sd::RxResult::kAccepted);
}

// #150: the ESP's Receivers use ground_to_esp, the ground's use esp_to_ground.
// A frame sealed with the other direction's key (a reflection) is refused as a
// bad tag and changes nothing, on both sides.
TEST(Receiver, FrameSealedForTheOtherDirectionRejected) {
  const sd::Key psk = test_key();
  std::uint8_t ground_nonce[sd::kNonceSize];
  std::uint8_t esp_nonce[sd::kNonceSize];
  std::memset(ground_nonce, 0x11, sizeof(ground_nonce));
  std::memset(esp_nonce, 0x22, sizeof(esp_nonce));
  const sd::DirectionKeys keys = sd::derive_keys(psk, ground_nonce, esp_nonce);

  sd::Receiver esp(sd::Role::kEsp, sd::Transport::kUdp);
  sd::Receiver ground(sd::Role::kGround, sd::Transport::kUdp);
  ASSERT_TRUE(esp.start_session(kSession, keys.ground_to_esp));
  ASSERT_TRUE(ground.start_session(kSession, keys.esp_to_ground));

  auto seal = [](std::uint8_t type, std::size_t size, std::uint32_t seq,
                 const sd::Key &key) {
    const sd::Header h{type, 0, 0, kSession, seq, 0};
    const std::vector<std::uint8_t> payload(size, 0);
    std::vector<std::uint8_t> out(sd::kMaxFrame);
    out.resize(sd::encode_frame(h, payload.data(), payload.size(), key,
                                out.data(), out.size()));
    return out;
  };
  const auto setpoint = static_cast<std::uint8_t>(sd::MsgType::kSetpoint);
  const auto state = static_cast<std::uint8_t>(sd::MsgType::kState);
  sd::Frame f{};

  // The real traffic goes through.
  auto sp = seal(setpoint, sd::kSetpointSize, 1, keys.ground_to_esp);
  ASSERT_EQ(esp.receive(sp.data(), sp.size(), f), sd::RxResult::kAccepted);
  auto st = seal(state, sd::kStateSize, 1, keys.esp_to_ground);
  ASSERT_EQ(ground.receive(st.data(), st.size(), f), sd::RxResult::kAccepted);

  // Reflections, with a high seq so only the tag can stop them.
  auto own_state = seal(state, sd::kStateSize, 1000, keys.esp_to_ground);
  EXPECT_EQ(esp.receive(own_state.data(), own_state.size(), f),
            sd::RxResult::kBadTag);
  auto own_setpoint =
      seal(setpoint, sd::kSetpointSize, 1000, keys.ground_to_esp);
  EXPECT_EQ(ground.receive(own_setpoint.data(), own_setpoint.size(), f),
            sd::RxResult::kBadTag);

  EXPECT_EQ(esp.last_seq(), 1U);
  EXPECT_EQ(ground.last_seq(), 1U);
  EXPECT_EQ(esp.counters().bad_tag, 1U);
  EXPECT_EQ(ground.counters().bad_tag, 1U);
}

// #152: even if both ends got the keys wrong (same key everywhere), each side
// accepts only what the other side sends. A node's own frame sent back to it is
// wrong_direction and moves nothing. BYE is sent by both, so both accept it.
TEST(Receiver, OwnFramesRejectedEvenWithTheSameKey) {
  OpenRx esp(sd::Transport::kUdp, sd::Role::kEsp);
  OpenRx ground(sd::Transport::kUdp, sd::Role::kGround);
  OpenRx esp_tcp(sd::Transport::kTcp, sd::Role::kEsp);
  OpenRx ground_tcp(sd::Transport::kTcp, sd::Role::kGround);
  const auto type = [](sd::MsgType t) { return static_cast<std::uint8_t>(t); };
  sd::Frame f{};

  auto state = frame_of(type(sd::MsgType::kState),
                        std::vector<std::uint8_t>(sd::kStateSize, 0), 100);
  EXPECT_EQ(esp.receive(state.data(), state.size(), f),
            sd::RxResult::kWrongDirection);
  auto setpoint =
      frame_of(type(sd::MsgType::kSetpoint),
               std::vector<std::uint8_t>(sd::kSetpointSize, 0), 100);
  EXPECT_EQ(ground.receive(setpoint.data(), setpoint.size(), f),
            sd::RxResult::kWrongDirection);
  auto ack = frame_of(type(sd::MsgType::kArmControlAck), {0}, 100);
  EXPECT_EQ(esp_tcp.receive(ack.data(), ack.size(), f),
            sd::RxResult::kWrongDirection);
  auto request = frame_of(type(sd::MsgType::kArmControlRequest), {15}, 100);
  EXPECT_EQ(ground_tcp.receive(request.data(), request.size(), f),
            sd::RxResult::kWrongDirection);
  EXPECT_EQ(esp.last_seq(), 0U);
  EXPECT_EQ(ground.last_seq(), 0U);
  EXPECT_EQ(esp_tcp.last_seq(), 1U);
  EXPECT_EQ(ground_tcp.last_seq(), 1U);
  EXPECT_EQ(esp.counters().wrong_direction, 1U);
  EXPECT_EQ(ground_tcp.counters().wrong_direction, 1U);

  auto bye = frame_of(type(sd::MsgType::kBye), {0}, 2);
  EXPECT_EQ(esp_tcp.receive(bye.data(), bye.size(), f),
            sd::RxResult::kAccepted);
  EXPECT_EQ(ground_tcp.receive(bye.data(), bye.size(), f),
            sd::RxResult::kAccepted);
  // The real traffic still goes through.
  EXPECT_EQ(esp.receive(setpoint.data(), setpoint.size(), f),
            sd::RxResult::kAcceptedGap);
  EXPECT_EQ(ground.receive(state.data(), state.size(), f),
            sd::RxResult::kAcceptedGap);
}

TEST(Receiver, KeepsItsRoleAndTransport) {
  const sd::Receiver rx(sd::Role::kGround, sd::Transport::kTcp);
  EXPECT_EQ(rx.role(), sd::Role::kGround);
  EXPECT_EQ(rx.transport(), sd::Transport::kTcp);
}

namespace {

// A handshake frame tagged with the PSK (test_key()), any header values.
std::vector<std::uint8_t> handshake(sd::MsgType type, std::uint32_t session,
                                    std::uint32_t seq,
                                    std::vector<std::uint8_t> payload) {
  const sd::Header h{static_cast<std::uint8_t>(type), 0, 0, session, seq, 0};
  std::vector<std::uint8_t> out(sd::kMaxFrame);
  out.resize(sd::encode_frame(h, payload.data(), payload.size(), test_key(),
                              out.data(), out.size()));
  return out;
}

std::vector<std::uint8_t> hello_ack_payload(std::uint32_t session) {
  sd::HelloAck m{session, {}, {}};
  std::vector<std::uint8_t> out(sd::kHelloAckSize);
  sd::encode_hello_ack(m, out.data(), out.size());
  return out;
}

} // namespace

// Spec §1, #152: before a session the ESP accepts only HELLO and the ground
// only HELLO_ACK, with the session_id and seq rules, and `out` is written only
// for an accepted frame.
TEST(Handshake, EachSideAcceptsOnlyItsOwnHandshakeFrame) {
  using sd::HandshakeResult;
  using sd::MsgType;
  using sd::Role;
  const std::vector<std::uint8_t> hello_payload(sd::kHelloSize, 0x11);
  const auto hello = handshake(MsgType::kHello, 0, 1, hello_payload);
  const auto ack =
      handshake(MsgType::kHelloAck, kSession, 1, hello_ack_payload(kSession));
  const auto check = [](Role role, const std::vector<std::uint8_t> &buf) {
    sd::Frame f{};
    return sd::receive_handshake(role, buf.data(), buf.size(), test_key(), f);
  };

  EXPECT_EQ(check(Role::kEsp, hello), HandshakeResult::kAccepted);
  EXPECT_EQ(check(Role::kGround, ack), HandshakeResult::kAccepted);
  // Reflected or out of place: each side refuses the other's frame.
  EXPECT_EQ(check(Role::kGround, hello), HandshakeResult::kWrongDirection);
  EXPECT_EQ(check(Role::kEsp, ack), HandshakeResult::kWrongDirection);
  // Any other type tagged with the PSK.
  const auto sp = handshake(MsgType::kSetpoint, 0, 1,
                            std::vector<std::uint8_t>(sd::kSetpointSize, 0));
  EXPECT_EQ(check(Role::kEsp, sp), HandshakeResult::kWrongDirection);

  EXPECT_EQ(check(Role::kEsp, handshake(MsgType::kHello, 5, 1, hello_payload)),
            HandshakeResult::kWrongSession);
  EXPECT_EQ(check(Role::kGround,
                  handshake(MsgType::kHelloAck, 0, 1, hello_ack_payload(0))),
            HandshakeResult::kWrongSession);
  EXPECT_EQ(check(Role::kGround, handshake(MsgType::kHelloAck, kSession, 1,
                                           hello_ack_payload(kSession + 1))),
            HandshakeResult::kWrongSession);
  EXPECT_EQ(check(Role::kEsp, handshake(MsgType::kHello, 0, 2, hello_payload)),
            HandshakeResult::kBadSeq);
  EXPECT_EQ(check(Role::kEsp,
                  handshake(MsgType::kHello, 0, 1,
                            std::vector<std::uint8_t>(sd::kHelloSize - 1, 0))),
            HandshakeResult::kBadPayloadSize);

  // Wrong key, broken frame.
  auto bad = hello;
  bad.back() ^= 1;
  EXPECT_EQ(check(Role::kEsp, bad), HandshakeResult::kBadTag);
  EXPECT_EQ(check(Role::kEsp, {hello.begin(), hello.begin() + 10}),
            HandshakeResult::kTruncated);
  auto magic = hello;
  magic[0] = 'X';
  EXPECT_EQ(check(Role::kEsp, magic), HandshakeResult::kBadMagic);
  auto version = hello;
  version[2] = 9;
  EXPECT_EQ(check(Role::kEsp, version), HandshakeResult::kBadVersion);
  auto longer = hello;
  longer.push_back(0);
  EXPECT_EQ(check(Role::kEsp, longer), HandshakeResult::kBadLength);

  // `out` stays untouched on a reject, and holds the frame on accept.
  sd::Frame f{};
  ASSERT_EQ(sd::receive_handshake(Role::kGround, hello.data(), hello.size(),
                                  test_key(), f),
            HandshakeResult::kWrongDirection);
  EXPECT_EQ(f.payload, nullptr);
  ASSERT_EQ(sd::receive_handshake(Role::kGround, ack.data(), ack.size(),
                                  test_key(), f),
            HandshakeResult::kAccepted);
  EXPECT_EQ(f.header.session_id, kSession);
}

// The numbers equal the Python twin's RxResult (sd_protocol/receiver.py), so a
// number in a log or a message means the same reason on both sides. Values
// are only ever appended.
TEST(Receiver, ResultNumbersMatchThePythonTwin) {
  using R = sd::RxResult;
  const struct {
    R r;
    int n;
  } expected[] = {
      {R::kAccepted, 0},        {R::kAcceptedGap, 1},     {R::kNoSession, 2},
      {R::kTruncated, 3},       {R::kBadMagic, 4},        {R::kBadVersion, 5},
      {R::kBadLength, 6},       {R::kBadTag, 7},          {R::kWrongSession, 8},
      {R::kDuplicateSeq, 9},    {R::kOldSeq, 10},         {R::kSeqTooHigh, 11},
      {R::kBadType, 12},        {R::kBadPayloadSize, 13}, {R::kBadMode, 14},
      {R::kWrongTransport, 15}, {R::kWrongDirection, 16}};
  for (const auto &e : expected) {
    EXPECT_EQ(static_cast<int>(e.r), e.n);
  }
  using H = sd::HandshakeResult;
  EXPECT_EQ(static_cast<int>(H::kAccepted), 0);
  EXPECT_EQ(static_cast<int>(H::kWrongDirection), 6);
  EXPECT_EQ(static_cast<int>(H::kBadSeq), 9);
}

TEST(Receiver, IsAccepted) {
  EXPECT_TRUE(sd::is_accepted(sd::RxResult::kAccepted));
  EXPECT_TRUE(sd::is_accepted(sd::RxResult::kAcceptedGap));
  EXPECT_FALSE(sd::is_accepted(sd::RxResult::kBadTag));
  EXPECT_FALSE(sd::is_accepted(sd::RxResult::kDuplicateSeq));
}
