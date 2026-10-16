---
title: ADR-007 — Portable Protocol Library and Custom ESP Firmware
type: adr
status: accepted
date: 2026-10-12
project: SEA autonomous drone
aliases:
  - D07
tags:
  - adr
  - esp8266
  - testing
---

# ADR-007 — Portable Protocol Library and Custom ESP Firmware

> [!success] Status: ACCEPTED (team, 2026-10-12)
> Accepts **P7** and resolves **OD-01** and **OD-19** in [[PC_to_ESP]], and **FC-07** in [[ESP_to_FC]].

## 1. Context

- The ESP8266 currently runs **AT firmware 1.7.4.0 (SDK 3.0.5)** ([[ESP_to_FC]] §2). AT firmware is a host-driven command interpreter. It cannot run on-device validation, loss detection or MSP ([[PC_to_ESP]] §10.2).
- Module 1 needs unit tests with **>80% coverage**. That is only realistic if the logic runs on a laptop.
- micro-ROS supports ESP32 but **not ESP8266** ([[Verified Facts and Sources]]), so a custom bridge is the program-sanctioned path ("micro-ROS or custom").

## 2. Decision

- **Custom ESP8266 firmware**: Arduino core 3.1.2 via PlatformIO, board `d1_mini`.
- All logic with branches (framing, MAC, sequence window, failsafe timers, MSP encode/decode, ThoneFlow parser) lives in a **portable C++17 library**, built for the host (GoogleTest) and for the ESP.
- A **Python twin** implements the same spec on the ground. Both must pass shared **golden test vectors**.
- The spec lives in the docs repo ([[Protocol Spec v0]]). The code lives in the software repo ([[Repository Layout]]).

> [!tip] Before the first flash
> Back up the original AT firmware so it can be restored at any time:
> `esptool.py --port /dev/ttyUSB0 read_flash 0 0x400000 d1mini_at_1.7.4.0_backup.bin` (4 MB flash on the D1 mini; check with `esptool.py flash_id`).

## 3. Consequences

- More firmware work for the team, contained by testing the logic off-target.
- Firmware-only code (`esp_firmware/src`) stays thin glue.

## 4. Related pages

- [[Testing Strategy]] · [[GitHub Actions Starter]] · [[Decision Register]]

## 5. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **ESP8266** | A WiFi-enabled microcontroller used as the drone communication module. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **FC** | Flight Controller: the onboard controller responsible for stabilization and control. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
