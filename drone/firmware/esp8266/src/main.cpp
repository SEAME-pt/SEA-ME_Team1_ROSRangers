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
}

void loop() {
#if WITH_OTA
  ArduinoOTA.handle();
#endif

#if WITH_UDP
  uint8_t buf[256];
  int n = udp.parsePacket();
  if (n > 0) { // PC -> FC
    peer = udp.remoteIP();
    peer_port = udp.remotePort();
    n = udp.read(buf, sizeof buf);
    Serial.write(buf, n);
  }
#endif

#if WITH_UART
  while (Serial.available() && out_len < sizeof out) { // FC -> PC
    out[out_len++] = Serial.read();
    last_rx_us = micros();
  }
  if (out_len > 0 &&
      (out_len == sizeof out || micros() - last_rx_us >= kFlushIdleUs)) {
    if (peer_port != 0) {
      udp.beginPacket(peer, peer_port);
      udp.write(out, out_len);
      udp.endPacket();
    }
    out_len = 0;
  }
#endif
}
