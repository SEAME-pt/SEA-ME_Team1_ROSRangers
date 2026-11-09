#include "sd/link.hpp"

#include <cstring>

#include "le.hpp"
#include "sd/frame.hpp"

namespace sd {

namespace {

constexpr std::uint8_t kHelloType = static_cast<std::uint8_t>(MsgType::kHello);

// Spec §4 rule 5: these results mean the TCP framing can no longer be trusted.
bool breaks_framing(RxResult r) {
  return r == RxResult::kTruncated || r == RxResult::kBadMagic ||
         r == RxResult::kBadVersion || r == RxResult::kBadLength;
}

bool breaks_framing(HandshakeResult r) {
  return r == HandshakeResult::kTruncated || r == HandshakeResult::kBadMagic ||
         r == HandshakeResult::kBadVersion || r == HandshakeResult::kBadLength;
}

void neutral(std::uint16_t channels[kRcChannels]) {
  const Setpoint zero = {};
  setpoint_to_rc(zero, channels);
}

// Header session_id, before any check: used only to pick the Receiver, which
// then checks everything (tag included).
std::uint32_t peek_session_id(const std::uint8_t *buf, std::size_t len) {
  return len >= 12 ? get_u32(buf + 8) : 0;
}

std::uint32_t t_send_us(std::uint32_t now_ms) { return now_ms * 1000U; }

} // namespace

Link::Session::Session()
    : open(false), id(0), tx_key{}, udp(Role::kEsp, Transport::kUdp),
      tcp(Role::kEsp, Transport::kTcp), conn(kNoConn), tx_seq_tcp(2),
      tx_seq_udp(1), opened_ms(0) {}

Link::Link(const LinkConfig &config, LinkIo &io) : config_(config), io_(io) {}

Link::~Link() {
  close_session(sessions_[0]);
  close_session(sessions_[1]);
  secure_zero(&config_.psk, sizeof(config_.psk));
}

bool Link::has_session() const { return cur().open; }
bool Link::has_pending() const { return pend().open; }
std::uint32_t Link::session_id() const { return cur().id; }
ConnId Link::session_conn() const { return cur().conn; }
ConnId Link::pending_conn() const { return pend().conn; }

std::uint32_t Link::rx_rejected() const {
  std::uint32_t n = counters_.hello_rejected + counters_.unrouted;
  for (const Session &s : sessions_) {
    n += s.udp.counters().rejected + s.tcp.counters().rejected;
  }
  return n;
}

void Link::boot_done(std::uint32_t now_ms) {
  (void)now_ms;
  if (state_ == EspState::kBoot) {
    state_ = EspState::kNoSession;
  }
}

void Link::tick(std::uint32_t now_ms) { advance(now_ms); }

void Link::advance(std::uint32_t now_ms) {
  if (state_ == EspState::kBoot) {
    return;
  }
  if (pend().open && now_ms - pend().opened_ms >= kPendingMaxMs) {
    ++counters_.pending_expired;
    drop_pending(kNoConn);
  }
  if (state_ == EspState::kStreaming) {
    if (now_ms - ctrl_ms_ >= kTLossMs) {
      enter_failsafe(now_ms);
    } else {
      const std::uint32_t ref = have_sp_ ? last_sp_ms_ : ctrl_ms_;
      if (!stale_reported_ && now_ms - ref >= kTFreshMs) {
        stale_reported_ = true;
        send_event(EventCode::kSetpointStale, now_ms);
      }
    }
  } else if (state_ == EspState::kSessionIdle) {
    // SESSION_IDLE -> NO_SESSION: TCP closed and no UDP. Only SESSION_IDLE:
    // in STREAMING a TCP drop changes nothing while UDP flows (ADR-005).
    if (cur().conn == kNoConn && now_ms - last_udp_ms_ >= kTLossMs) {
      end_current(now_ms);
    }
  }
}

RxResult Link::on_udp(const std::uint8_t *buf, std::size_t len,
                      std::uint32_t now_ms) {
  advance(now_ms);
  if (state_ == EspState::kBoot) {
    ++counters_.unrouted;
    return RxResult::kNoSession;
  }
  const std::uint32_t id = peek_session_id(buf, len);
  Session *s = nullptr;
  if (pend().open && id == pend().id) {
    s = &pend();
  } else if (cur().open) {
    s = &cur();
  }
  if (s == nullptr) {
    ++counters_.unrouted;
    return RxResult::kNoSession;
  }
  Frame f{};
  const RxResult r = s->udp.receive(buf, len, f);
  if (!is_accepted(r)) {
    return r;
  }
  if (s == &pend()) {
    confirm_pending(now_ms);
  }
  last_udp_ms_ = now_ms;
  handle(f, now_ms);
  return r;
}

TcpVerdict Link::on_tcp_frame(ConnId conn, const std::uint8_t *buf,
                              std::size_t len, std::uint32_t now_ms) {
  advance(now_ms);
  if (state_ == EspState::kBoot || conn == kNoConn) {
    ++counters_.unrouted;
    return TcpVerdict::kClose;
  }
  if (len >= 4 && buf[3] == kHelloType) {
    Frame f{};
    const HandshakeResult r =
        receive_handshake(Role::kEsp, buf, len, config_.psk, f);
    if (r != HandshakeResult::kAccepted) {
      ++counters_.hello_rejected;
      return breaks_framing(r) ? TcpVerdict::kClose : TcpVerdict::kKeep;
    }
    on_hello(conn, f, now_ms);
    return TcpVerdict::kKeep;
  }

  // A session frame. It is checked only against a session that lives on this
  // connection; a connection with no session carries HELLO only (ADR-005).
  const std::uint32_t id = peek_session_id(buf, len);
  Session *s = nullptr;
  if (pend().open && conn == pend().conn && id == pend().id) {
    s = &pend();
  } else if (cur().open && conn == cur().conn) {
    s = &cur();
  } else if (pend().open && conn == pend().conn) {
    s = &pend();
  }
  if (s == nullptr) {
    ++counters_.unrouted;
    return TcpVerdict::kClose;
  }
  Frame f{};
  const RxResult r = s->tcp.receive(buf, len, f);
  if (!is_accepted(r)) {
    return breaks_framing(r) ? TcpVerdict::kClose : TcpVerdict::kKeep;
  }
  if (s == &pend()) {
    confirm_pending(now_ms);
  }
  handle(f, now_ms);
  return TcpVerdict::kKeep;
}

void Link::on_tcp_closed(ConnId conn, std::uint32_t now_ms) {
  advance(now_ms);
  if (conn == kNoConn) {
    return;
  }
  // A pending session cannot answer without its connection: drop it.
  if (pend().open && pend().conn == conn) {
    close_session(pend());
  }
  // The current session keeps running on UDP (ADR-005); ARM and CONFIG wait
  // for a new session.
  if (cur().open && cur().conn == conn) {
    cur().conn = kNoConn;
  }
}

bool Link::fc_output(std::uint32_t now_ms,
                     std::uint16_t channels[kRcChannels]) {
  advance(now_ms);
  if (state_ == EspState::kBoot) {
    return false;
  }
  if (fs_active_) {
    // Latched, so a clock that wraps (2^32 ms) never restarts the neutral.
    if (config_.failsafe_output == FailsafeOutput::kNeutralThenStop &&
        (fs_stopped_ || now_ms - fs_since_ms_ >= kFailsafeNeutralMs)) {
      fs_stopped_ = true;
      return false;
    }
    neutral(channels);
    return true;
  }
  if (state_ == EspState::kStreaming && have_sp_ &&
      now_ms - last_sp_ms_ < kTFreshMs && last_sp_.mode == 1) {
    setpoint_to_rc(last_sp_, channels);
    return true;
  }
  neutral(channels);
  return true;
}

void Link::send_state(std::uint32_t now_ms, const FcView &fc) {
  advance(now_ms);
  Session &s = cur();
  if (!s.open) {
    return;
  }
  const std::uint32_t rejected = rx_rejected();
  State st{};
  st.esp_state = static_cast<std::uint8_t>(state_);
  st.override_active = fc.override_active;
  st.last_ctrl_seq = last_ctrl_seq_;
  st.last_ctrl_t_send_us = last_ctrl_t_send_us_;
  st.roll = fc.roll;
  st.pitch = fc.pitch;
  st.heading = fc.heading;
  st.vbat = fc.vbat;
  st.flow_x = fc.flow_x;
  st.flow_y = fc.flow_y;
  st.flow_quality = fc.flow_quality;
  st.flow_status = fc.flow_status;
  st.rx_rejected =
      rejected > 0xFFFFU ? 0xFFFFU : static_cast<std::uint16_t>(rejected);
  st.msp_errors = fc.msp_errors;
  std::uint8_t payload[kStateSize];
  const std::size_t n = encode_state(st, payload, sizeof payload);
  const Header h{static_cast<std::uint8_t>(MsgType::kState),
                 0,
                 0,
                 s.id,
                 s.tx_seq_udp++,
                 t_send_us(now_ms)};
  std::uint8_t out[kMaxFrame];
  const std::size_t len =
      encode_frame(h, payload, n, s.tx_key, out, sizeof out);
  if (len > 0) {
    io_.send_udp(out, len);
  }
}

void Link::on_hello(ConnId conn, const Frame &f, std::uint32_t now_ms) {
  Hello hello{};
  if (!decode_hello(f.payload, f.header.payload_len, hello) ||
      hello.proto != kWireVersion) {
    ++counters_.hello_rejected;
    return;
  }
  // At most one accepted HELLO per 500 ms; a refused one does not restart the
  // window (ADR-005 entry 2026-11-06).
  if (hello_seen_ && now_ms - last_hello_ms_ < kHelloMinGapMs) {
    ++counters_.hello_rate_limited;
    return;
  }
  std::uint32_t id = 0;
  if (!new_session_id(id)) {
    ++counters_.hello_rejected; // the RNG gave only 0 or ids in use
    return;
  }
  hello_seen_ = true;
  last_hello_ms_ = now_ms;

  HelloAck ack{};
  ack.session_id = id;
  config_.random(ack.esp_nonce, kNonceSize);
  std::memcpy(ack.fw_version, config_.fw_version, kFwVersionSize);
  DirectionKeys keys =
      derive_keys(config_.psk, hello.ground_nonce, ack.esp_nonce);

  if (!cur().open) {
    // NO_SESSION: nothing to protect, the session opens at once.
    drop_pending(conn);
    open_session(cur(), id, keys, conn, now_ms);
    state_ = EspState::kSessionIdle;
  } else {
    // A later HELLO replaces a pending one that is not confirmed yet.
    drop_pending(conn);
    open_session(pend(), id, keys, conn, now_ms);
  }
  secure_zero(&keys, sizeof keys);

  std::uint8_t payload[kHelloAckSize];
  const std::size_t n = encode_hello_ack(ack, payload, sizeof payload);
  const Header h{static_cast<std::uint8_t>(MsgType::kHelloAck),
                 0,
                 0,
                 id,
                 1,
                 t_send_us(now_ms)};
  std::uint8_t out[kMaxFrame];
  const std::size_t len =
      encode_frame(h, payload, n, config_.psk, out, sizeof out);
  if (len > 0) {
    io_.send_tcp(conn, out, len);
  }
  ++counters_.hello_accepted;
}

bool Link::new_session_id(std::uint32_t &out) const {
  for (int i = 0; i < 8; ++i) {
    std::uint8_t b[4];
    config_.random(b, sizeof b);
    const std::uint32_t id = get_u32(b);
    if (id != 0 && !(cur().open && id == cur().id) &&
        !(pend().open && id == pend().id)) {
      out = id;
      return true;
    }
  }
  return false;
}

void Link::open_session(Session &s, std::uint32_t id, const DirectionKeys &keys,
                        ConnId conn, std::uint32_t now_ms) {
  // start_session() fails only for id 0, which new_session_id() never gives.
  (void)s.udp.start_session(id, keys.ground_to_esp);
  (void)s.tcp.start_session(id, keys.ground_to_esp);
  s.tx_key = keys.esp_to_ground;
  s.open = true;
  s.id = id;
  s.conn = conn;
  s.tx_seq_tcp = 2; // HELLO_ACK used seq 1 (spec §1)
  s.tx_seq_udp = 1;
  s.opened_ms = now_ms;
  if (&s == &cur()) {
    have_sp_ = false;
    neutral_run_ = false;
    stale_reported_ = false;
    last_udp_ms_ = now_ms;
    last_ctrl_seq_ = 0;
    last_ctrl_t_send_us_ = 0;
  }
}

void Link::close_session(Session &s) {
  s.udp.end_session();
  s.tcp.end_session();
  secure_zero(&s.tx_key, sizeof s.tx_key);
  s.open = false;
  s.id = 0;
  s.conn = kNoConn;
}

void Link::drop_pending(ConnId keep_conn) {
  if (!pend().open) {
    return;
  }
  const ConnId c = pend().conn;
  close_session(pend());
  if (c != kNoConn && c != keep_conn && c != cur().conn) {
    io_.close_tcp(c);
  }
}

// The pending session's first valid frame arrived: it becomes the current
// session (ADR-005 entry 2026-11-06). The caller then handles that frame.
void Link::confirm_pending(std::uint32_t now_ms) {
  Session &old = cur();
  const ConnId new_conn = pend().conn;
  if (old.open) {
    ++counters_.sessions_switched;
    std::uint8_t reason[1];
    const std::size_t n = encode_u8_payload(
        static_cast<std::uint8_t>(ByeReason::kReplaced), reason, sizeof reason);
    send_tcp_in(old, MsgType::kBye, reason, n, now_ms);
    if (old.conn != kNoConn && old.conn != new_conn) {
      io_.close_tcp(old.conn);
    }
    // Leaving control: the ADR-004 output, not neutral frames at once (a hard
    // throttle cut with mask 15), until the new session's ARM OK. From
    // LINK_FAILSAFE the output already runs and keeps its timeline.
    if (state_ == EspState::kStreaming) {
      start_failsafe_output(now_ms);
    }
  }
  close_session(old);
  cur_ ^= 1U;
  state_ = EspState::kSessionIdle;
  have_sp_ = false;
  neutral_run_ = false;
  stale_reported_ = false;
  last_udp_ms_ = now_ms;
  last_ctrl_seq_ = 0;
  last_ctrl_t_send_us_ = 0;
}

// BYE from the ground, or SESSION_IDLE with no TCP and no UDP.
void Link::end_current(std::uint32_t now_ms) {
  if (state_ == EspState::kStreaming) {
    start_failsafe_output(now_ms);
  }
  const ConnId c = cur().conn;
  close_session(cur());
  if (c != kNoConn && c != pend().conn) {
    io_.close_tcp(c);
  }
  state_ = EspState::kNoSession;
  have_sp_ = false;
  neutral_run_ = false;
}

void Link::enter_failsafe(std::uint32_t now_ms) {
  state_ = EspState::kLinkFailsafe;
  start_failsafe_output(now_ms);
  neutral_run_ = false;
  ++counters_.failsafe_entered;
  send_event(EventCode::kLinkFailsafeEntered, now_ms);
}

void Link::start_failsafe_output(std::uint32_t now_ms) {
  if (!fs_active_) {
    fs_active_ = true;
    fs_stopped_ = false;
    fs_since_ms_ = now_ms;
  }
}

void Link::handle(const Frame &f, std::uint32_t now_ms) {
  switch (static_cast<MsgType>(f.header.type)) {
  case MsgType::kSetpoint:
    handle_setpoint(f, now_ms);
    break;
  case MsgType::kArmControlRequest:
    handle_arm(f, now_ms);
    break;
  case MsgType::kResumeRequest:
    handle_resume(now_ms);
    break;
  case MsgType::kBye:
    end_current(now_ms);
    break;
  case MsgType::kConfigGet:
  case MsgType::kConfigSet:
    handle_config(f, now_ms);
    break;
  default:
    break; // the Receiver passes only the types the ESP receives (spec §2)
  }
}

void Link::handle_setpoint(const Frame &f, std::uint32_t now_ms) {
  Setpoint sp{};
  if (!decode_setpoint(f.payload, f.header.payload_len, sp)) {
    return; // cannot happen: the Receiver checked the size
  }
  const bool had = have_sp_;
  const std::uint32_t prev_ms = last_sp_ms_;
  last_sp_ = sp;
  last_sp_ms_ = now_ms;
  have_sp_ = true;
  ctrl_ms_ = now_ms;
  last_ctrl_seq_ = f.header.seq;
  last_ctrl_t_send_us_ = f.header.t_send_us;
  stale_reported_ = false;
  // The neutral stream that RESUME needs: mode 0 with no gap of T_fresh.
  if (sp.mode == 0) {
    if (!neutral_run_ || !had || now_ms - prev_ms >= kTFreshMs) {
      neutral_run_ = true;
      neutral_since_ms_ = now_ms;
    }
  } else {
    neutral_run_ = false;
  }
}

void Link::handle_arm(const Frame &f, std::uint32_t now_ms) {
  std::uint8_t mask = 0;
  (void)decode_u8_payload(f.payload, f.header.payload_len, mask);
  AckResult result = AckResult::kOk;
  if (state_ != EspState::kSessionIdle) {
    result = AckResult::kWrongState;
  } else if (mask != config_.override_mask) {
    result = AckResult::kMaskMismatch;
  } else {
    state_ = EspState::kStreaming;
    ctrl_ms_ = now_ms; // no setpoint within T_loss of ARM -> LINK_FAILSAFE
    fs_active_ = false;
    stale_reported_ = false;
    // Sticks sent while control was off never reach the FC: only setpoints
    // accepted after ARM OK count (safety review of PR 1).
    have_sp_ = false;
  }
  std::uint8_t payload[1];
  const std::size_t n = encode_u8_payload(static_cast<std::uint8_t>(result),
                                          payload, sizeof payload);
  send_tcp_in(cur(), MsgType::kArmControlAck, payload, n, now_ms);
}

void Link::handle_resume(std::uint32_t now_ms) {
  AckResult result = AckResult::kOk;
  if (state_ != EspState::kLinkFailsafe) {
    result = AckResult::kWrongState;
  } else if (!(neutral_run_ && have_sp_ && now_ms - last_sp_ms_ < kTFreshMs &&
               now_ms - neutral_since_ms_ >= kResumeNeutralMs)) {
    result = AckResult::kResumeConditionNotMet;
  }
  std::uint8_t payload[1];
  const std::size_t n = encode_u8_payload(static_cast<std::uint8_t>(result),
                                          payload, sizeof payload);
  send_tcp_in(cur(), MsgType::kResumeAck, payload, n, now_ms);
  if (result == AckResult::kOk) {
    state_ = EspState::kStreaming;
    ctrl_ms_ = now_ms;
    fs_active_ = false;
    stale_reported_ = false;
    send_event(EventCode::kLinkResumed, now_ms);
  }
}

void Link::handle_config(const Frame &f, std::uint32_t now_ms) {
  ConfigKv kv{};
  (void)decode_config_kv(f.payload, f.header.payload_len, kv);
  bool known = true;
  std::int32_t value = 0;
  switch (kv.key) {
  case 0x01: // config_table_version
    value = 1;
    break;
  case 0x02: // proto_version
    value = kWireVersion;
    break;
  case 0x40: // t_loss_ms
    value = static_cast<std::int32_t>(kTLossMs);
    break;
  case 0x41: // t_fresh_ms
    value = static_cast<std::int32_t>(kTFreshMs);
    break;
  case 0x42: // setpoint_limit
    value = kSetpointLimit;
    break;
  case 0x80: // telemetry_rate_hz
    value = telemetry_rate_hz_;
    break;
  case 0x81: // event_level
    value = event_level_;
    break;
  default:
    known = false; // 0xF0-0xFF too: no diagnostic keys on a normal build
    break;
  }

  ConfigResult result = ConfigResult::kOk;
  if (f.header.type == static_cast<std::uint8_t>(MsgType::kConfigGet)) {
    result = known ? ConfigResult::kOk : ConfigResult::kUnknownKey;
  } else if (state_ == EspState::kStreaming) {
    result = ConfigResult::kControlEnabled; // checked first (spec §3.1)
  } else if (!known) {
    result = ConfigResult::kUnknownKey;
  } else if (kv.key <= 0x3F) {
    result = ConfigResult::kReadOnly;
  } else if (kv.key <= 0x7F) {
    result = ConfigResult::kSafetyParameter;
  } else if (kv.key == 0x80) {
    if (kv.value < 1 || kv.value > 50) {
      result = ConfigResult::kOutOfRange; // never clamped (§7.3 rule 4)
    } else {
      telemetry_rate_hz_ = static_cast<std::uint8_t>(kv.value);
      value = kv.value;
    }
  } else { // 0x81
    if (kv.value < 0 || kv.value > 3) {
      result = ConfigResult::kOutOfRange;
    } else {
      event_level_ = static_cast<std::uint8_t>(kv.value);
      value = kv.value;
    }
  }

  ConfigAck ack{};
  ack.key = kv.key;
  ack.result = static_cast<std::uint8_t>(result);
  ack.value = known ? value : 0;
  std::uint8_t payload[kConfigAckSize];
  const std::size_t n = encode_config_ack(ack, payload, sizeof payload);
  send_tcp_in(cur(), MsgType::kConfigAck, payload, n, now_ms);
}

void Link::send_event(EventCode code, std::uint32_t now_ms) {
  if (!cur().open) {
    return;
  }
  Event e{};
  e.code = static_cast<std::uint16_t>(code);
  e.text_len = 0;
  std::uint8_t payload[kEventMaxSize];
  const std::size_t n = encode_event(e, payload, sizeof payload);
  send_tcp_in(cur(), MsgType::kEvent, payload, n, now_ms);
}

// One frame on a session's own TCP connection, with its sending key. Nothing
// when the connection is gone (EVENT and BYE are best effort, spec §3.1).
void Link::send_tcp_in(Session &s, MsgType type, const std::uint8_t *payload,
                       std::size_t len, std::uint32_t now_ms) {
  if (!s.open || s.conn == kNoConn) {
    return;
  }
  const Header h{static_cast<std::uint8_t>(type),
                 0,
                 0,
                 s.id,
                 s.tx_seq_tcp++,
                 t_send_us(now_ms)};
  std::uint8_t out[kMaxFrame];
  const std::size_t n =
      encode_frame(h, payload, len, s.tx_key, out, sizeof out);
  if (n > 0) {
    io_.send_tcp(s.conn, out, n);
  }
}

} // namespace sd
