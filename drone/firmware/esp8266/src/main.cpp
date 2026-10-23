#include <ArduinoOTA.h>
#include <ESP8266WiFi.h>
#include <WiFiUdp.h>

// AP_SSID, AP_PASS, OTA_PASS and AP_CHANNEL come from secrets.py
// (~/.config/seame-drone/), never from the repo.
#if !defined(AP_SSID) || !defined(AP_PASS) || !defined(OTA_PASS) ||            \
    !defined(AP_CHANNEL)
#error "Secrets missing: secrets.py did not run (see platformio.ini)"
#endif

// Bench diagnosis of the Wi-Fi latency (envs esp_diag_*): each part of the
// firmware can be left out. The normal firmware has all three.
#ifndef WITH_OTA
#define WITH_OTA 1
#endif
#ifndef WITH_UDP // PC -> FC: UDP 5760 to the UART
#define WITH_UDP 1
#endif
#ifndef WITH_UART // FC -> PC: UART to UDP
#define WITH_UART 1
#endif
#ifndef WITH_TIMING // env esp_diag_timing: timestamps per request on UDP 5761
#define WITH_TIMING 0
#endif
#ifndef WITH_TCP_ECHO // env esp_diag_tcp (S1-07): TCP echo server on 5762
#define WITH_TCP_ECHO 0
#endif
#ifndef WITH_WDT_TEST // env esp_diag_wdt (S1-10): hang on command, UDP 5763
#define WITH_WDT_TEST 0
#endif

constexpr uint16_t kPort = 5760;
// The FC sends a reply back to back (87 us per byte at 115200): 1 ms of UART
// silence means the reply is complete, so it leaves as one UDP packet.
constexpr uint32_t kFlushIdleUs = 1000;
WiFiUDP udp;
IPAddress peer;
uint16_t peer_port = 0;
uint8_t out[256];
size_t out_len = 0;
uint32_t last_rx_us = 0;

#if WITH_TIMING
// Four micros() stamps per request, to see where the time of a slow reply
// goes: esp_in (UDP packet read), uart_out (written to the FC), uart_in
// (first reply byte from the FC), esp_out (reply handed to the Wi-Fi stack).
// Sent every 100 ms as text lines to the last sender, UDP 5761 (read by
// scripts/esp_timing.py). The bridge bytes are not changed.
constexpr uint16_t kTimingPort = 5761;
constexpr uint32_t kReportMs = 100;
constexpr uint32_t kNoReplyUs = 500000; // no reply after this: unanswered
constexpr size_t kLinesPerPacket = 20;  // about 1 kB, below one Wi-Fi frame
struct Stamp {
  uint32_t seq, esp_in, uart_out, uart_in, esp_out;
  bool sent;
};
Stamp pending[32]; // waiting for the FC, oldest first (ring)
size_t pend_head = 0, pend_n = 0;
Stamp done[64]; // finished, waiting for the next report
size_t done_n = 0;
uint32_t req_seq = 0, report_seq = 0, dropped = 0;
uint32_t loop_max_us = 0, last_loop_us = 0, last_report_ms = 0;
uint32_t flush_first_us = 0;

void finish(const Stamp &s) {
  if (done_n < sizeof done / sizeof done[0]) {
    done[done_n++] = s;
  } else {
    dropped++;
  }
}

Stamp pop_pending() {
  Stamp s = pending[pend_head];
  pend_head = (pend_head + 1) % (sizeof pending / sizeof pending[0]);
  pend_n--;
  return s;
}

void push_pending(const Stamp &s) {
  const size_t size = sizeof pending / sizeof pending[0];
  if (pend_n == size) {
    finish(pop_pending()); // the FC never answered the oldest one
  }
  pending[(pend_head + pend_n) % size] = s;
  pend_n++;
}

// MSP replies start with "$M>" (or "$M!" for an error). Several replies can
// leave in one UDP packet when requests arrive in a burst.
size_t count_replies(const uint8_t *buf, size_t len) {
  size_t n = 0;
  for (size_t i = 0; i + 2 < len; i++) {
    if (buf[i] == '$' && buf[i + 1] == 'M' &&
        (buf[i + 2] == '>' || buf[i + 2] == '!')) {
      n++;
    }
  }
  return n;
}

// R,<report>,<now_us>,<loop_max_us>,<dropped>
// Q,<seq>,<esp_in>,<uart_out>,<uart_in>,<esp_out>,<sent>  (0 = no reply)
void report() {
  while (pend_n > 0 && micros() - pending[pend_head].esp_in >= kNoReplyUs) {
    finish(pop_pending());
  }
  if (peer_port == 0) {
    return;
  }
  size_t first = 0;
  do {
    char line[96];
    udp.beginPacket(peer, kTimingPort);
    int len = snprintf(line, sizeof line, "R,%u,%u,%u,%u\n", report_seq++,
                       static_cast<uint32_t>(micros()), loop_max_us, dropped);
    udp.write(line, len);
    for (size_t i = 0; i < kLinesPerPacket && first < done_n; i++, first++) {
      const Stamp &s = done[first];
      len = snprintf(line, sizeof line, "Q,%u,%u,%u,%u,%u,%u\n", s.seq,
                     s.esp_in, s.uart_out, s.uart_in, s.esp_out, s.sent);
      udp.write(line, len);
    }
    udp.endPacket();
  } while (first < done_n);
  done_n = 0;
  loop_max_us = 0;
}
#endif

#if WITH_TCP_ECHO
// S1-07: one TCP client at a time (ADR-005), everything it sends is echoed
// back (read by scripts/tcp_load.py). Never blocks the loop: it only moves
// what the TCP send buffer can take, so a TCP stall cannot stall UDP here.
constexpr uint16_t kTcpPort = 5762;
WiFiServer tcp_server(kTcpPort);
WiFiClient tcp_client;

void tcp_echo() {
  WiFiClient incoming = tcp_server.accept();
  if (incoming) {
    if (tcp_client.connected()) {
      incoming.stop(); // a second client is refused
    } else {
      tcp_client = incoming;
      tcp_client.setNoDelay(true);
    }
  }
  if (!tcp_client.connected()) {
    return;
  }
  uint8_t buf[512];
  size_t n = static_cast<size_t>(tcp_client.available());
  n = std::min(
      {n, static_cast<size_t>(tcp_client.availableForWrite()), sizeof buf});
  if (n > 0) {
    n = tcp_client.read(buf, n);
    tcp_client.write(buf, n);
  }
}
#endif

#if WITH_WDT_TEST
// S1-10: "HANG" stops the loop without yielding, so the software watchdog
// resets the ESP after about 3 s. "HANG_HW" also turns the software watchdog
// off, so the hardware watchdog does it (about 8 s). "REASON?" answers with
// the reset reason and the uptime (read by scripts/esp_wdt.py). The serial
// port goes to the FC, so nothing is printed there.
constexpr uint16_t kWdtPort = 5763;
WiFiUDP wdt_udp;

void wdt_test() {
  char cmd[16];
  int n = wdt_udp.parsePacket();
  if (n <= 0) {
    return;
  }
  n = wdt_udp.read(cmd, sizeof cmd - 1);
  cmd[n > 0 ? n : 0] = '\0';
  char reply[96];
  int len = 0;
  bool hang = strcmp(cmd, "HANG") == 0, hang_hw = strcmp(cmd, "HANG_HW") == 0;
  if (strcmp(cmd, "REASON?") == 0) {
    len =
        snprintf(reply, sizeof reply, "REASON,%s,%u",
                 ESP.getResetReason().c_str(), static_cast<uint32_t>(millis()));
  } else if (hang || hang_hw) {
    len = snprintf(reply, sizeof reply, "HANGING,%s", cmd);
  } else {
    return;
  }
  wdt_udp.beginPacket(wdt_udp.remoteIP(), wdt_udp.remotePort());
  wdt_udp.write(reply, len);
  wdt_udp.endPacket();
  if (hang || hang_hw) {
    delay(50); // let the reply leave
    if (hang_hw) {
      ESP.wdtDisable();
    }
    while (true) {
      asm volatile(""); // keep the empty loop, never yield
    }
  }
}
#endif

void setup() {
  Serial.begin(115200);   // UART0 = GPIO1/3 = FC UART4
  WiFi.persistent(false); // do not rewrite the Wi-Fi settings in flash at boot
  WiFi.mode(WIFI_AP);
  WiFi.setSleepMode(WIFI_NONE_SLEEP);
  WiFi.softAP(AP_SSID, AP_PASS, AP_CHANNEL, false, 1); // max 1 client
#if WITH_OTA
  ArduinoOTA.setPassword(OTA_PASS);
  ArduinoOTA.begin();
#endif
#if WITH_UDP || WITH_UART
  udp.begin(kPort);
#endif
#if WITH_TCP_ECHO
  tcp_server.begin();
#endif
#if WITH_WDT_TEST
  wdt_udp.begin(kWdtPort);
#endif
}

void loop() {
#if WITH_OTA
  ArduinoOTA.handle();
#endif
#if WITH_TCP_ECHO
  tcp_echo();
#endif
#if WITH_WDT_TEST
  wdt_test();
#endif

#if WITH_TIMING
  uint32_t now_us = micros();
  if (last_loop_us != 0 && now_us - last_loop_us > loop_max_us) {
    loop_max_us = now_us - last_loop_us; // the ESP itself stalled
  }
  last_loop_us = now_us;
  if (millis() - last_report_ms >= kReportMs) {
    last_report_ms = millis();
    report();
  }
#endif

#if WITH_TIMING
  uint32_t now_us = micros();
  if (last_loop_us != 0 && now_us - last_loop_us > loop_max_us) {
    loop_max_us = now_us - last_loop_us; // the ESP itself stalled
  }
  last_loop_us = now_us;
  if (millis() - last_report_ms >= kReportMs) {
    last_report_ms = millis();
    report();
  }
#endif

#if WITH_UDP
  uint8_t buf[256];
  int n = udp.parsePacket();
  if (n > 0) { // PC -> FC
#if WITH_TIMING
    uint32_t esp_in = micros();
#endif
    peer = udp.remoteIP();
    peer_port = udp.remotePort();
    n = udp.read(buf, sizeof buf);
    Serial.write(buf, n);
#if WITH_TIMING
    push_pending({++req_seq, esp_in, micros(), 0, 0, false});
#endif
  }
#endif

#if WITH_UART
  while (Serial.available() && out_len < sizeof out) { // FC -> PC
#if WITH_TIMING
    if (out_len == 0) {
      flush_first_us = micros();
    }
#endif
    out[out_len++] = Serial.read();
    last_rx_us = micros();
  }
  if (out_len > 0 &&
      (out_len == sizeof out || micros() - last_rx_us >= kFlushIdleUs)) {
    if (peer_port != 0) {
      udp.beginPacket(peer, peer_port);
      udp.write(out, out_len);
#if WITH_TIMING
      bool sent = udp.endPacket();
      uint32_t esp_out = micros();
      for (size_t i = count_replies(out, out_len); i > 0 && pend_n > 0; i--) {
        Stamp s = pop_pending();
        s.uart_in = flush_first_us;
        s.esp_out = esp_out;
        s.sent = sent;
        finish(s);
      }
#else
      udp.endPacket();
#endif
    }
    out_len = 0;
  }
#endif
}
