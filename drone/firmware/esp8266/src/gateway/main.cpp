// Gateway firmware (#101, #102), env esp_gateway. Replaces the transparent
// bridge step by step; the bridge (src/main.cpp, env esp) stays as the stable
// bench tool until the gateway has passed its bench tests (#106).
//
// What it does:
// - FC link (ADR-009): one scheduler (sd::FcLink) owns UART0 = FC UART4. It
//   polls MSP_ATTITUDE (25 Hz), MSP_STATUS / MSP_ANALOG (5 Hz) and
//   MSP_API_VERSION (1 Hz), and sends MSP_SET_RAW_RC ahead of the polls. Every
//   MSP frame goes through the allow-list in sd::msp; nothing from the network
//   is ever written to the UART.
// - Ground link (Protocol Spec v0): the ESP Link State Machine (sd::Link) on
//   TCP and UDP port 5759. Every 20 ms sd::Link::fc_output() decides what goes
//   to the FC: nothing, neutral sticks, or the ground's setpoint (ADR-004
//   failsafe, ADR-005 sessions). STATE goes to the ground at
//   telemetry_rate_hz.
//
// - OTA (ADR-024): ArduinoOTA.handle() runs only while sd::ota_gate_open()
//   (no session, FC disarmed per an MSP_STATUS under 500 ms old). The start
//   callback takes the upload lock (MSP_SET_ARMING_DISABLED 1 after a fresh
//   "disarmed"), or restarts the ESP. Value 0 is sent at every boot and after
//   a failed upload, from loop() only, retried until the FC confirms. The OTA
//   port (UDP 8266) is open only while the gate is.
//
// Not yet: STATE.override_active (#176, needs the MSP box IDs) and reading the
// override mask from the FC (fixed kOverrideMask below).

#include <ArduinoOTA.h>
#include <ESP8266WiFi.h>
#include <WiFiUdp.h>

#include "sd/fc_link.hpp"
#include "sd/frame.hpp"
#include "sd/link.hpp"
#include "sd/ota_lock.hpp"

// AP_SSID, AP_PASS, OTA_PASS, AP_CHANNEL and SD_PSK_HEX come from secrets.py
// (~/.config/seame-drone/ or SEAME_* variables), never from the repo.
#if !defined(AP_SSID) || !defined(AP_PASS) || !defined(OTA_PASS) ||            \
    !defined(AP_CHANNEL)
#error "Secrets missing: secrets.py did not run (see platformio.ini)"
#endif
#ifndef SD_PSK_HEX
#error "PSK missing: set SEAME_PSK or \"psk\" in secrets.json (64 hex chars)"
#endif

namespace {

constexpr uint16_t kPort = 5759;        // TCP session + UDP control (§7.1)
constexpr uint32_t kRcPeriodMs = 20;    // 50 Hz to the FC
constexpr uint32_t kHelloOnlyMs = 2000; // ADR-005: HELLO-only connection
constexpr uint8_t kOverrideMask = 8;    // stage 1, yaw only (ADR-010)
constexpr uint8_t kFlowNoData = 4;      // ThoneFlow §8.1 order: NO_DATA
constexpr int kKeepAliveIdleS = 5;      // ADR-005 entry 2026-11-06
constexpr int kKeepAliveIntervalS = 1;
constexpr int kKeepAliveCount = 3;
constexpr int kMaxUdpPerLoop = 8;     // never starve the UART and timers
constexpr uint8_t kStateRetries = 3;  // Wi-Fi send queue full (#30)
constexpr uint32_t kStateRetryMs = 3; // let the queue drain between tries
// The loop must never wait on the network (it would stop RC and the timers):
// the core's WiFiClient waits up to 5 s for a full send queue and stop()
// up to 300 ms for ACKs by default (firmware review of #102 PR 2).
constexpr unsigned long kTcpWriteTimeoutMs = 1;
constexpr unsigned int kTcpStopWaitMs = 1;
constexpr char kFwVersion[sd::kFwVersionSize + 1] = "sd-gw 0.2.0 ";

sd::FcLink fc;

// --- TCP: one session connection plus one HELLO-only connection (ADR-005).
constexpr sd::ConnId kSlots = 2;
struct Slot {
  WiFiClient client;
  bool used = false;
  bool report_closed = false; // tell sd::Link next loop, never from LinkIo
  uint32_t opened_ms = 0;
  uint8_t buf[sd::kMaxFrame];
  size_t len = 0;
};
Slot slots[kSlots];

// --- UDP: SETPOINT in, STATE out to whoever sent the last accepted frame.
WiFiUDP udp;
bool have_peer = false;
IPAddress peer_ip;
uint16_t peer_port = 0;
uint8_t retry_buf[sd::kMaxFrame];
size_t retry_len = 0;
uint8_t retry_left = 0;
uint32_t retry_at_ms = 0;
uint32_t state_dropped = 0;

bool udp_send(const uint8_t *buf, size_t len) {
  if (!udp.beginPacket(peer_ip, peer_port)) {
    return false;
  }
  udp.write(buf, len);
  return udp.endPacket() == 1; // 0 when the Wi-Fi send queue is full
}

void close_slot(sd::ConnId i) {
  Slot &s = slots[i];
  if (s.used) {
    s.client.stop(kTcpStopWaitMs);
    s.used = false;
    s.len = 0;
    s.report_closed = true;
  }
}

// sd::Link's way out. These must not call back into sd::Link (link.hpp).
class Io : public sd::LinkIo {
public:
  void send_tcp(sd::ConnId conn, const uint8_t *buf, size_t len) override {
    if (conn >= kSlots || !slots[conn].used) {
      return;
    }
    WiFiClient &c = slots[conn].client;
    // Never block the loop: a connection that cannot take the frame now is
    // closed (its session keeps running on UDP, ADR-005).
    if (static_cast<size_t>(c.availableForWrite()) < len ||
        c.write(buf, len) != len) {
      close_slot(conn);
    }
  }
  void send_udp(const uint8_t *buf, size_t len) override {
    if (!have_peer || len > sizeof retry_buf) {
      return;
    }
    if (!udp_send(buf, len)) { // keep the newest STATE and retry it
      if (retry_len > 0) {
        ++state_dropped;
      }
      memcpy(retry_buf, buf, len);
      retry_len = len;
      retry_left = kStateRetries;
      retry_at_ms = millis() + kStateRetryMs;
    } else {
      retry_len = 0;
    }
  }
  void close_tcp(sd::ConnId conn) override {
    if (conn < kSlots) {
      close_slot(conn);
    }
  }
};

Io io;

void hardware_random(uint8_t *out, size_t n) {
  // ESP8266 hardware RNG (spec §1: esp_nonce and session_id).
  while (n > 0) {
    const uint32_t r = ESP.random();
    for (int i = 0; i < 4 && n > 0; ++i, --n) {
      *out++ = static_cast<uint8_t>(r >> (8 * i));
    }
  }
}

// secrets.py checks the PSK (64 lowercase hex). These catch a value that
// reached the build another way (for example build_flags).
static_assert(sizeof(SD_PSK_HEX) == 2 * sd::kKeySize + 1,
              "SD_PSK_HEX must be 64 hex characters");
constexpr bool is_hex(const char *p, size_t n) {
  return n == 0 || ((('0' <= *p && *p <= '9') || ('a' <= *p && *p <= 'f')) &&
                    is_hex(p + 1, n - 1));
}
static_assert(is_hex(SD_PSK_HEX, 2 * sd::kKeySize),
              "SD_PSK_HEX must be lowercase hex");

uint8_t hex_nibble(char c) {
  return static_cast<uint8_t>(c <= '9' ? c - '0' : c - 'a' + 10);
}

sd::LinkConfig make_config() {
  sd::LinkConfig c{};
  const char *hex = SD_PSK_HEX; // checked by secrets.py: 64 lowercase hex
  for (size_t i = 0; i < sd::kKeySize; ++i) {
    c.psk.bytes[i] = static_cast<uint8_t>(hex_nibble(hex[2 * i]) << 4 |
                                          hex_nibble(hex[2 * i + 1]));
  }
  c.override_mask = kOverrideMask;
  c.failsafe_output = sd::FailsafeOutput::kNeutralThenStop; // 2026.6.2
  memcpy(c.fw_version, kFwVersion, sd::kFwVersionSize);
  c.random = hardware_random;
  return c;
}

sd::Link *link_ptr = nullptr; // set in setup(); sd::Link has no default ctor

void accept_tcp(WiFiServer &server, uint32_t now) {
  WiFiClient incoming = server.accept();
  if (!incoming) {
    return;
  }
  for (sd::ConnId i = 0; i < kSlots; ++i) {
    Slot &s = slots[i];
    if (!s.used && !s.report_closed) {
      s.client = incoming;
      s.client.setNoDelay(true);
      s.client.setTimeout(kTcpWriteTimeoutMs); // never block in write()
      s.client.keepAlive(kKeepAliveIdleS, kKeepAliveIntervalS, kKeepAliveCount);
      s.used = true;
      s.opened_ms = now;
      s.len = 0;
      return;
    }
  }
  incoming.stop(kTcpStopWaitMs); // both slots taken
}

void service_tcp(sd::Link &link, uint32_t now) {
  for (sd::ConnId i = 0; i < kSlots; ++i) {
    Slot &s = slots[i];
    if (s.report_closed) { // closed earlier, from inside sd::Link
      s.report_closed = false;
      link.on_tcp_closed(i, now);
      continue;
    }
    if (!s.used) {
      continue;
    }
    if (!s.client.connected()) {
      close_slot(i);
      s.report_closed = false;
      link.on_tcp_closed(i, now);
      continue;
    }
    // A connection that carries no session after 2 s was HELLO-only.
    if (i != link.session_conn() && i != link.pending_conn() &&
        now - s.opened_ms >= kHelloOnlyMs) {
      close_slot(i);
      s.report_closed = false;
      link.on_tcp_closed(i, now);
      continue;
    }
    // TCP framing (OD-03): the header first, then the size it announces.
    while (s.used && s.client.available() > 0) {
      const size_t want = s.len < sd::kHeaderSize
                              ? sd::kHeaderSize
                              : sd::frame_size(s.buf, s.len);
      if (want == 0) { // bad length: the framing is lost (spec §4 rule 5)
        close_slot(i);
        s.report_closed = false;
        link.on_tcp_closed(i, now);
        break;
      }
      const int got = s.client.read(s.buf + s.len, want - s.len);
      if (got <= 0) {
        break;
      }
      s.len += static_cast<size_t>(got);
      if (s.len < want || s.len < sd::kHeaderSize) {
        continue; // wait for the rest
      }
      const size_t size = sd::frame_size(s.buf, s.len);
      if (size == 0) {
        close_slot(i);
        s.report_closed = false;
        link.on_tcp_closed(i, now);
        break;
      }
      if (s.len == size) { // one whole frame
        const sd::TcpVerdict v = link.on_tcp_frame(i, s.buf, s.len, now);
        s.len = 0;
        if (v == sd::TcpVerdict::kClose && s.used) {
          close_slot(i); // reported to sd::Link next loop
        }
      }
    }
  }
}

void service_udp(sd::Link &link, uint32_t now) {
  uint8_t buf[sd::kMaxFrame + 1]; // one byte more: an oversized frame is seen
  for (int k = 0; k < kMaxUdpPerLoop; ++k) {
    const int size = udp.parsePacket();
    if (size <= 0) {
      return;
    }
    // A datagram longer than a frame fills buf: the Receiver sees one byte
    // too many and refuses it (bad length).
    const int n = udp.read(buf, sizeof buf);
    if (n <= 0) {
      continue;
    }
    const sd::RxResult r = link.on_udp(buf, static_cast<size_t>(n), now);
    if (sd::is_accepted(r)) { // STATE goes back to this sender
      peer_ip = udp.remoteIP();
      peer_port = udp.remotePort();
      have_peer = true;
    }
  }
}

sd::FcView fc_view() {
  const sd::FcTelemetry &t = fc.telemetry();
  const sd::FcCounters &c = fc.counters();
  sd::FcView v{};
  v.override_active = 0; // not read yet: needs the MSP box IDs (#102)
  if (t.has_attitude) {
    v.roll = t.attitude.roll;
    v.pitch = t.attitude.pitch;
    v.heading = t.attitude.heading;
  }
  if (t.has_analog) {
    v.vbat = t.analog.vbat;
  }
  v.flow_status = kFlowNoData; // no flow sensor yet: never zero motion
  const uint32_t errors =
      c.timeouts + c.bad_checksum + c.too_long + c.error_replies + c.unexpected;
  v.msp_errors = errors > 0xFFFF ? 0xFFFF : static_cast<uint16_t>(errors);
  return v;
}

// --- OTA (ADR-024). Blocking MSP exchanges on the FC UART, only around an
// upload (the loop is stopped then) and at boot / after a failed upload.
class Uart : public sd::FcUart {
public:
  void write(const uint8_t *buf, size_t len) override {
    Serial.write(buf, len);
  }
  int read() override {
    if (Serial.available() > 0) {
      return Serial.read();
    }
    yield(); // let Wi-Fi run while the FC answers
    return -1;
  }
  uint32_t now_ms() override { return millis(); }
  void discard_input() override {
    while (Serial.available() > 0) {
      Serial.read();
    }
  }
};
Uart fc_uart;
bool ota_locked = false; // the upload lock is set (start callback)
// Set by the error callback (also from the UDP receive callback), read by
// loop().
volatile bool release_due = false;
// Value 0 still to confirm: at every boot, and after a failed upload.
bool release_wanted = true;
uint32_t next_release_ms = 0;
constexpr uint32_t kReleaseRetryMs = 500;
bool ota_open = false; // the OTA port is open (only while the gate is)

void on_ota_start() {
  // ADR-024 upload lock, before any byte is written. Anything but "locked"
  // aborts: the image is not written, and the boot release runs again.
  if (sd::ota_lock(fc_uart) != sd::OtaLockResult::kLocked) {
    ESP.restart();
    return; // restart() does not return on core 3.1.2; never mark a lock
  }
  ota_locked = true;
}

void on_ota_error(ota_error_t) {
  // Also runs for a wrong password (before any lock) and can run twice for
  // one failure: only mark the release as due (ADR-024).
  release_due = true;
}

WiFiServer server(kPort);
uint32_t last_rc_ms = 0;
uint32_t last_state_ms = 0;

} // namespace

void setup() {
  Serial.begin(115200);   // UART0 = GPIO1/3 = FC UART4
  WiFi.persistent(false); // do not rewrite the Wi-Fi settings in flash at boot
  WiFi.mode(WIFI_AP);
  WiFi.setSleepMode(WIFI_NONE_SLEEP);
  WiFi.softAP(AP_SSID, AP_PASS, AP_CHANNEL, false, 1); // max 1 client
  // OTA (ADR-024 D3): password from secrets.py. The OTA port is opened only
  // while the gate is open (loop()), so no request can wait while it is
  // closed. No mDNS: espota uses the IP.
  ArduinoOTA.setPassword(OTA_PASS);
  ArduinoOTA.onStart(on_ota_start);
  ArduinoOTA.onError(on_ota_error);
  static sd::Link link(make_config(), io);
  link_ptr = &link;
  server.begin();
  server.setNoDelay(true);
  udp.begin(kPort);
}

void loop() {
  sd::Link &link = *link_ptr;
  const uint32_t now = millis();

  while (Serial.available() > 0) { // FC -> ESP: replies
    fc.on_byte(static_cast<uint8_t>(Serial.read()), now);
  }
  // Value 0 at every boot (ADR-024: an image that rebooted mid-upload, or
  // after an abort, does not know a lock was taken) and after a failed upload
  // (only if the lock was taken). Retried every 500 ms until the FC confirms:
  // value 0 is harmless, and a missed one would leave arming locked. Only in
  // BOOT or NO_SESSION: each try blocks the loop up to 100 ms and drops
  // FcLink's pending replies, so never during a session (Dot99, review of
  // #177). In BOOT nothing goes to the FC yet, so the boot release still goes
  // out before the link leaves BOOT. A lock left on is the safe side: the
  // FC refuses to arm, and the radio shows it.
  if (release_due) {
    release_due = false;
    if (ota_locked) {
      ota_locked = false;
      release_wanted = true;
    }
  }
  const bool no_session = link.state() == sd::EspState::kBoot ||
                          link.state() == sd::EspState::kNoSession;
  if (release_wanted && no_session && fc.fc_ready() &&
      static_cast<int32_t>(now - next_release_ms) >= 0) {
    release_wanted = !sd::ota_release(fc_uart);
    next_release_ms = now + kReleaseRetryMs;
  }
  // BOOT -> NO_SESSION once the FC answers (REQ-SAF-016).
  if (link.state() == sd::EspState::kBoot && fc.fc_ready()) {
    link.boot_done(now);
  }

  accept_tcp(server, now);
  service_tcp(link, now);
  service_udp(link, now);
  link.tick(now);
  // ADR-024 D2: the OTA port is open only while the gate is. end() also
  // drops a request in progress, so none waits for the gate to reopen.
  const bool gate = sd::ota_gate_open(link, fc, now);
  if (gate && !ota_open) {
    ArduinoOTA.begin(false); // false: no mDNS
    ota_open = true;
  } else if (!gate && ota_open) {
    ArduinoOTA.end();
    ota_open = false;
  }
  if (ota_open) {
    ArduinoOTA.handle();
  }

  // 50 Hz: what goes to the FC. Nothing in BOOT or once the ADR-004 output
  // has stopped.
  if (now - last_rc_ms >= kRcPeriodMs) {
    // Fixed 20 ms grid; after a long stall (OTA) start again from now.
    last_rc_ms =
        now - last_rc_ms >= 2 * kRcPeriodMs ? now : last_rc_ms + kRcPeriodMs;
    uint16_t channels[sd::kRcChannels];
    if (link.fc_output(now, channels)) {
      fc.set_rc(channels);
    } else {
      fc.stop_rc();
    }
  }
  uint8_t request[sd::msp::kMaxFrame];
  const size_t n = fc.poll(now, request, sizeof request);
  if (n > 0) { // ESP -> FC: only allow-listed frames
    Serial.write(request, n);
  }

  // STATE at telemetry_rate_hz (CONFIG key 0x80, 1-50 Hz).
  if (now - last_state_ms >= 1000U / link.telemetry_rate_hz()) {
    last_state_ms = now;
    link.send_state(now, fc_view());
  } else if (retry_len > 0 && have_peer &&
             static_cast<int32_t>(now - retry_at_ms) >= 0) {
    retry_at_ms = now + kStateRetryMs;
    if (udp_send(retry_buf, retry_len)) {
      retry_len = 0;
    } else if (--retry_left == 0) {
      retry_len = 0;
      ++state_dropped;
    }
  }
}
