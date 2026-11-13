#pragma once
// ESP link state machine (#102): docs/PraticalPlan/03 Architecture/ESP Link
// State Machine.md, ADR-004 (link failsafe), ADR-005 (TCP session, pending
// session of #156) and Protocol Spec v0 §1-§4 and §7.2-§7.3.
//
// Pure logic: no sockets, no Arduino. Every entry point takes the time in ms
// (an injected clock, PC_to_ESP §9.1), so it runs and is tested on the host.
// The firmware (#102 PR 2) feeds it frames and connection events, calls
// fc_output() at 50 Hz and send_state() at telemetry_rate_hz(), and sends what
// it asks for through LinkIo.
//
// Every entry point first applies the timers that expired up to `now_ms`, so
// a frame that arrives after T_loss can never rescue STREAMING: the failsafe
// is entered first, then the frame is handled.

#include <cstddef>
#include <cstdint>

#include "sd/hmac.hpp"
#include "sd/messages.hpp"
#include "sd/receiver.hpp"
#include "sd/setpoint.hpp"

namespace sd {

// Timers (spec §7.2, §7.3 Safety keys; ADR-004; ADR-005 entry 2026-11-06).
constexpr std::uint32_t kTLossMs = 250;  // no accepted control -> LINK_FAILSAFE
constexpr std::uint32_t kTFreshMs = 150; // older setpoint -> neutral sticks
constexpr std::uint32_t kResumeNeutralMs = 500; // neutral stream before RESUME
constexpr std::uint32_t kPendingMaxMs = 2000;   // pending session lifetime
constexpr std::uint32_t kHelloMinGapMs = 500;   // HELLO rate limit
constexpr std::uint32_t kFailsafeNeutralMs = 1000; // 2026.6: neutral, then stop
constexpr std::int32_t kSetpointLimit = 1000;      // spec §7.3 key 0x42

// What the ESP sends to the FC once the link failsafe is active (ADR-004 §2).
enum class FailsafeOutput : std::uint8_t {
  kNeutralForever = 0,  // Betaflight 4.5.1: overrides are held forever
  kNeutralThenStop = 1, // 2026.6.x: neutral 1 s, then stop, so the FC's
                        // freshness guard falls back to the ELRS sticks
};

// TCP connection handle chosen by the firmware (for example its socket slot).
using ConnId = std::uint8_t;
constexpr ConnId kNoConn = 0xFF;

// Fills `out` with `n` random bytes. On the ESP: the hardware RNG (spec §1).
using RandomFill = void (*)(std::uint8_t *out, std::size_t n);

struct LinkConfig {
  Key psk;
  // msp_override_channels_mask set in Betaflight (ADR-010). An
  // ARM_CONTROL_REQUEST with any other mask is refused (spec §3.1 code 2).
  std::uint8_t override_mask;
  FailsafeOutput failsafe_output;
  char fw_version[kFwVersionSize]; // raw bytes, not NUL-terminated
  RandomFill random;
};

// Where the link's frames go. The firmware owns the sockets.
// The callbacks must not call back into Link (on_tcp_closed() included): a
// write error found inside send_tcp() is reported with on_tcp_closed() from
// the firmware's next loop, after the Link call that sent it has returned.
class LinkIo {
public:
  virtual void send_tcp(ConnId conn, const std::uint8_t *buf,
                        std::size_t len) = 0;
  // To the address of the last UDP frame on_udp() accepted for the current
  // session.
  virtual void send_udp(const std::uint8_t *buf, std::size_t len) = 0;
  // Close this TCP connection. The firmware may still call on_tcp_closed()
  // for it afterwards; that does nothing.
  virtual void close_tcp(ConnId conn) = 0;

protected:
  ~LinkIo() = default;
};

// FC values copied into STATE. The link adds esp_state, last_ctrl_seq,
// last_ctrl_t_send_us and rx_rejected.
struct FcView {
  std::uint8_t override_active;
  std::int16_t roll;     // 0.1 deg
  std::int16_t pitch;    // 0.1 deg
  std::uint16_t heading; // deg
  std::uint16_t vbat;    // 0.01 V
  std::int16_t flow_x;
  std::int16_t flow_y;
  std::uint8_t flow_quality;
  std::uint8_t flow_status;
  std::uint16_t msp_errors;
};

enum class TcpVerdict : std::uint8_t {
  kKeep = 0,
  kClose = 1, // framing can no longer be trusted, or a non-HELLO frame on a
              // connection that carries no session (spec §4 rule 5)
};

// Counters of the link itself. The Receivers count their own rejects.
struct LinkCounters {
  std::uint32_t hello_accepted;
  std::uint32_t hello_rejected;     // failed receive_handshake() or bad proto
  std::uint32_t hello_rate_limited; // valid, but within 500 ms of the last one
  std::uint32_t pending_expired;
  std::uint32_t sessions_switched; // pending confirmed while a session ran
  std::uint32_t unrouted;          // no session to check the frame against
  std::uint32_t failsafe_entered;
};

class Link {
public:
  Link(const LinkConfig &config, LinkIo &io);
  ~Link(); // wipes the keys
  Link(const Link &) = delete;
  Link &operator=(const Link &) = delete;

  // BOOT -> NO_SESSION: the SoftAP is up and the FC answered
  // MSP_API_VERSION (REQ-SAF-016). Nothing is sent to the FC before this.
  void boot_done(std::uint32_t now_ms);

  // One UDP datagram. The result tells the firmware whether to remember the
  // sender as the STATE destination (only when is_accepted()).
  RxResult on_udp(const std::uint8_t *buf, std::size_t len,
                  std::uint32_t now_ms);

  // One complete TCP frame (cut with frame_size()) from connection `conn`.
  // The firmware enforces the connection limit of ADR-005: one session
  // connection plus one HELLO-only connection. A connection that is neither
  // session_conn() nor pending_conn() 2 s after it opened must be closed (a
  // refused HELLO keeps it open here, and no pending timer covers it).
  TcpVerdict on_tcp_frame(ConnId conn, const std::uint8_t *buf, std::size_t len,
                          std::uint32_t now_ms);

  // The connection is gone (closed by the peer, keep-alive, or close_tcp()).
  // A TCP connection by itself never starts or ends a session (ADR-005).
  void on_tcp_closed(ConnId conn, std::uint32_t now_ms);

  // Call at 50 Hz. Writes the MSP_SET_RAW_RC channels (AETR order) and
  // returns true when a frame must be sent; false means send nothing.
  bool fc_output(std::uint32_t now_ms, std::uint16_t channels[kRcChannels]);

  // Sends one STATE over UDP to the current session; nothing without one.
  void send_state(std::uint32_t now_ms, const FcView &fc);

  // The FC link, from sd::FcLink::fc_up() (alive with the #188 hysteresis),
  // every loop (#179). A change is told to the current session: EVENT
  // FC_LINK_LOST / FC_LINK_RESTORED. While the FC is silent, ARM and RESUME
  // are refused (result 4). Starts false: no ARM before the FC has been seen.
  void set_fc_alive(bool alive, std::uint32_t now_ms);
  bool fc_alive() const { return fc_alive_; }

  // From sd::FcLink::rc_accepted(), every loop (#188): the FC has acknowledged
  // RC continuously for 1 s. ARM and RESUME also need it (result 4). Starts
  // false.
  void set_rc_accepted(bool accepted) { rc_accepted_ = accepted; }

  // Sends one FC_HEALTH (spec v1.4) over UDP to the current session.
  void send_fc_health(std::uint32_t now_ms, const FcHealth &health);

  // Applies the timers without any other input (the firmware may call it
  // every loop; the other entry points call it too).
  void tick(std::uint32_t now_ms);

  EspState state() const { return state_; }
  bool has_session() const;
  bool has_pending() const;
  std::uint32_t session_id() const;
  ConnId session_conn() const;
  ConnId pending_conn() const;
  // True while the ADR-004 output replaces the normal output (LINK_FAILSAFE,
  // and after a session switch or BYE from control until the next ARM OK).
  bool failsafe_active() const { return fs_active_; }
  std::uint8_t telemetry_rate_hz() const { return telemetry_rate_hz_; }
  std::uint8_t event_level() const { return event_level_; }
  const LinkCounters &counters() const { return counters_; }
  // Every rejected frame since boot: all Receivers, handshake rejects and
  // unrouted frames. STATE.rx_rejected saturates it at 0xFFFF (#149).
  std::uint32_t rx_rejected() const;

private:
  struct Session {
    Session();
    bool open;
    std::uint32_t id;
    Key tx_key; // esp_to_ground
    Receiver udp;
    Receiver tcp;
    ConnId conn;
    std::uint32_t tx_seq_tcp; // next seq to send; HELLO_ACK used 1
    std::uint32_t tx_seq_udp;
    std::uint32_t opened_ms;
  };

  void advance(std::uint32_t now_ms);
  void on_hello(ConnId conn, const Frame &f, std::uint32_t now_ms);
  void open_session(Session &s, std::uint32_t id, const DirectionKeys &keys,
                    ConnId conn, std::uint32_t now_ms);
  void close_session(Session &s);
  void drop_pending(ConnId keep_conn);
  void confirm_pending(std::uint32_t now_ms);
  void end_current(std::uint32_t now_ms);
  void enter_failsafe(std::uint32_t now_ms);
  void start_failsafe_output(std::uint32_t now_ms);
  void tell_failsafe_output(std::uint32_t now_ms);
  void handle(const Frame &f, std::uint32_t now_ms);
  void handle_setpoint(const Frame &f, std::uint32_t now_ms);
  void handle_arm(const Frame &f, std::uint32_t now_ms);
  void handle_resume(std::uint32_t now_ms);
  void handle_config(const Frame &f, std::uint32_t now_ms);
  void send_tcp_in(Session &s, MsgType type, const std::uint8_t *payload,
                   std::size_t len, std::uint32_t now_ms);
  void send_event(EventCode code, std::uint32_t now_ms);
  void send_udp_in(MsgType type, const std::uint8_t *payload, std::size_t len,
                   std::uint32_t now_ms);
  bool new_session_id(std::uint32_t &out) const;
  Session &cur() { return sessions_[cur_]; }
  const Session &cur() const { return sessions_[cur_]; }
  Session &pend() { return sessions_[cur_ ^ 1U]; }
  const Session &pend() const { return sessions_[cur_ ^ 1U]; }

  LinkConfig config_;
  LinkIo &io_;
  EspState state_ = EspState::kBoot;
  Session sessions_[2];
  std::uint8_t cur_ = 0; // index of the current session; the other is pending

  // HELLO rate limit.
  bool hello_seen_ = false;
  std::uint32_t last_hello_ms_ = 0;

  // Control (UDP) of the current session.
  bool have_sp_ = false;
  Setpoint last_sp_ = {};
  std::uint32_t last_sp_ms_ = 0;
  std::uint32_t ctrl_ms_ = 0;     // last accepted control, or ARM / RESUME OK
  std::uint32_t last_udp_ms_ = 0; // last accepted UDP frame of the session
  std::uint32_t last_ctrl_seq_ = 0;
  std::uint32_t last_ctrl_t_send_us_ = 0;
  bool neutral_run_ = false;
  std::uint32_t neutral_since_ms_ = 0;
  bool stale_reported_ = false;

  bool fc_alive_ = false;    // see set_fc_alive()
  bool rc_accepted_ = false; // see set_rc_accepted()

  // ADR-004 output.
  bool fs_active_ = false;
  bool fs_stopped_ = false; // 2026.6: the neutral second is over, stays over
  std::uint32_t fs_since_ms_ = 0;

  // Tunable configuration (spec §7.3). Back to defaults at every boot.
  std::uint8_t telemetry_rate_hz_ = 25;
  std::uint8_t event_level_ = 1;

  LinkCounters counters_ = {};
};

} // namespace sd
