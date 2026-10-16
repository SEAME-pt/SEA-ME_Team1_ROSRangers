---
title: ADR-012 — ESP UART Pins (D1 mini)
type: adr
status: accepted-with-conditions
date: 2026-10-12
project: SEA autonomous drone
aliases:
  - D12
  - D12 ESP UART Pins
tags:
  - adr
  - esp8266
  - uart
---

# ADR-012 — ESP UART Pins (D1 mini)

> [!success] Status: ACCEPTED WITH CONDITIONS (team, 2026-10-12)
> Accepts **P4** and resolves **FC-06** in [[ESP_to_FC]].

## 1. Context

- UART0 (GPIO1 TX / GPIO3 RX) is the only full-duplex UART. `Serial1` (UART1) is **TX-only** on GPIO2 (ESP8266 Arduino core docs).
- On the D1 mini the USB-serial chip shares UART0 → risk of electrical contention ([[ESP_to_FC]] §4.3).
- The ESP8266 boot ROM prints at **74880 baud** on TX at power-up.

## 2. Decision

- Use **UART0 on GPIO1/GPIO3** (pins labelled TX/RX).
- **Procedure:** USB and FC are never connected to UART0 at the same time. Unplug the FC wires (or use OTA) to flash.
- Debug output goes over Wi-Fi (diagnostics category) or to `Serial1` (GPIO2/D4) with a USB-TTL adapter. There is no `Serial.print` on UART0.

## 3. Rejected alternative

`Serial.swap()` → GPIO15 (TX) / GPIO13 (RX) frees USB. But **GPIO15 must be low at boot**, and a pull-up on the FC RX line could stop the D1 mini booting. Revisit only after measuring that line.

## 4. Consequences

- The FC MSP parser ignores the boot bytes (no `$M`/`$X` header). Verify in Phase 2 of [[ESP_to_FC]] §11.

## 5. Related pages

- [[Wiring and Pinout]] · [[Decision Register]]

## 6. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **ESP8266** | A WiFi-enabled microcontroller used as the drone communication module. |
| **FC** | Flight Controller: the onboard controller responsible for stabilization and control. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
