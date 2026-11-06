// Gateway firmware (#101), env esp_gateway. Replaces the transparent bridge
// step by step; the bridge (src/main.cpp, env esp) stays as the stable bench
// tool until the gateway has passed its bench tests.
//
// What it does now: the FC link of ADR-009. One scheduler (sd::FcLink) owns
// UART0 = FC UART4 and polls MSP_ATTITUDE at 25 Hz and MSP_STATUS / MSP_ANALOG
// at 5 Hz. Every MSP frame it sends goes through the allow-list in sd::msp, so
// no forbidden command can reach the FC, and nothing from the network is ever
// written to the UART. It never sends RC: sessions, SETPOINT -> MSP_SET_RAW_RC
// and the failsafe are #102.
//
// Bench view (diagnostic, read-only, WITH_STATUS_VIEW=1 in env esp_gateway):
// any datagram to UDP 5770 is answered with one text line of the latest FC
// values and the link counters. Until #102 sends STATE, this is how a bench
// test sees what the gateway reads; it is dropped then.

#include <ArduinoOTA.h>
#include <ESP8266WiFi.h>
#include <WiFiUdp.h>

#include "sd/fc_link.hpp"

// AP_SSID, AP_PASS, OTA_PASS and AP_CHANNEL come from secrets.py
// (~/.config/seame-drone/), never from the repo.
#if !defined(AP_SSID) || !defined(AP_PASS) || !defined(OTA_PASS) ||            \
    !defined(AP_CHANNEL)
#error "Secrets missing: secrets.py did not run (see platformio.ini)"
#endif

#ifndef WITH_STATUS_VIEW
#define WITH_STATUS_VIEW 0
#endif

namespace {

sd::FcLink fc;

#if WITH_STATUS_VIEW
constexpr uint16_t kStatusPort = 5770; // diagnostic builds only (spec §7.1)
WiFiUDP status_udp;

void answer_status() {
  if (status_udp.parsePacket() <= 0) {
    return;
  }
  const uint32_t now = millis();
  const sd::FcTelemetry &t = fc.telemetry();
  const sd::FcCounters &c = fc.counters();
  char line[288];
  const int len = snprintf(
      line, sizeof line,
      "armed=%d arming_flags=0x%08lx status_age_ms=%ld roll=%d pitch=%d "
      "heading=%u vbat_cV=%u att_age_ms=%ld req=%lu rep=%lu timeout=%lu "
      "bad_cksum=%lu too_long=%lu err=%lu unexpected=%lu\n",
      t.has_status ? static_cast<int>(t.status.armed) : -1,
      static_cast<unsigned long>(t.status.arming_disable_flags),
      t.has_status ? static_cast<long>(now - t.status_ms) : -1L,
      t.attitude.roll, t.attitude.pitch, t.attitude.heading, t.analog.vbat,
      t.has_attitude ? static_cast<long>(now - t.attitude_ms) : -1L,
      static_cast<unsigned long>(c.requests),
      static_cast<unsigned long>(c.replies),
      static_cast<unsigned long>(c.timeouts),
      static_cast<unsigned long>(c.bad_checksum),
      static_cast<unsigned long>(c.too_long),
      static_cast<unsigned long>(c.error_replies),
      static_cast<unsigned long>(c.unexpected));
  if (len <= 0) {
    return;
  }
  status_udp.beginPacket(status_udp.remoteIP(), status_udp.remotePort());
  status_udp.write(reinterpret_cast<const uint8_t *>(line),
                   static_cast<size_t>(len) < sizeof line ? len
                                                          : sizeof line - 1);
  status_udp.endPacket();
}
#endif

} // namespace

void setup() {
  Serial.begin(115200);   // UART0 = GPIO1/3 = FC UART4
  WiFi.persistent(false); // do not rewrite the Wi-Fi settings in flash at boot
  WiFi.mode(WIFI_AP);
  WiFi.setSleepMode(WIFI_NONE_SLEEP);
  WiFi.softAP(AP_SSID, AP_PASS, AP_CHANNEL, false, 1); // max 1 client
  // OTA is ungated here, as in the bridge; the ADR-024 gate and lock are #102.
  ArduinoOTA.setPassword(OTA_PASS);
  ArduinoOTA.begin();
#if WITH_STATUS_VIEW
  status_udp.begin(kStatusPort);
#endif
}

void loop() {
  ArduinoOTA.handle();

  const uint32_t now = millis();
  while (Serial.available() > 0) { // FC -> ESP: replies to the polls
    fc.on_byte(static_cast<uint8_t>(Serial.read()), now);
  }
  uint8_t request[sd::msp::kMaxFrame];
  const size_t n = fc.poll(now, request, sizeof request);
  if (n > 0) { // ESP -> FC: only allow-listed polls
    Serial.write(request, n);
  }

#if WITH_STATUS_VIEW
  answer_status(); // the next parsePacket() drops the request's content
#endif
}
