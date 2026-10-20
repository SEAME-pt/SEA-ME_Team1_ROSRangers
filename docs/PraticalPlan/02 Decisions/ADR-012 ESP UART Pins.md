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

> [!info] Update 2026-10-20: the board and the install (GitHub #55)
> - The board is an **ESP-12F** module (ESP8266, Wemos D1 mini class). The team treats "D1 mini" and "ESP8266" as the same board in these notes (2026-10-20). The backup notes record its USB-serial chip as an FTDI FT232R (`drone/firmware/esp8266/backup/README.md`). The UART0 analysis below applies to it: its USB-serial chip shares UART0.
> - Since 2026-10-19 the ESP is **soldered to FC UART4** with wires that are easy to remove. The install is not permanent ([[ADR-021 Removable ESP Connector|ADR-021]] replaces it with a connector).
> - Since then the ESP's USB is never connected, as this ADR requires (its USB chip and FC T4 would both drive the ESP RX). It stays on the production SoftAP until the custom firmware ([[ADR-007 Portable Protocol Library and Custom ESP Firmware|ADR-007]]); the first flash of that firmware needs a written procedure.
> - The decision does not change.

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
| **ESP8266** | The Wi-Fi microcontroller of the drone's ESP module (an ESP-12F, Wemos D1 mini class). It bridges the ground PC to the FC over UART4. |
| **FC** | Flight Controller: on this project the OddityRC F7 AIO (STM32F722, Betaflight target `ODDITYRCF7AIO`) of the 2.5" drone. It stabilises the aircraft and takes MSP from the ESP on UART4. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **OTA** | Over-the-air update: flashing firmware through Wi-Fi instead of USB. |
| **USB** | Universal Serial Bus: used to flash the ESP and to power or configure the FC from a PC. |
| **OD-xx / FC-xx / TF-xx** | Open-decision IDs in PC_to_ESP, ESP_to_FC and ThoneFlow. The Decision Register shows which ADR resolves each one. |
