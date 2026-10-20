---
title: ADR-016 — ThoneFlow-3901U Integration Path
type: adr
status: accepted
date: 2026-10-12
project: SEA autonomous drone
aliases:
  - D16
  - D16 Flow Sensor Path
tags:
  - adr
  - optical-flow
  - thoneflow-3901u
---

# ADR-016 — ThoneFlow-3901U Integration Path

> [!success] Status: ACCEPTED (2026-10-12, revised 2026-10-14)
> Resolves **TF-01**, **TF-02**, **TF-11** and **TF-12** in [[ThoneFlow]]. 2026-10-14: option C dropped and the height source chosen (§2.3, §2.4). It replaces the earlier "SoftwareSerial on the ESP" decision, following the risk analysis in [[ThoneFlow]] §6.

## 1. Context

- **Option A (FC UART) is ruled out:** Betaflight 4.5.1 has **no optical-flow driver at all**. 2026.6.2 only supports MT and UPT1 sensors, not CX-OF. INAV supports CX-OF but has **no target** for `ODDITYRCF7AIO`. All of this was verified in source ([[Verified Facts and Sources]]).
- The ESP8266 has no free hardware RX ([[ThoneFlow]] §6). SoftwareSerial under Wi-Fi load is unproven.
- The datasheet (66 Hz, high byte first, checksum at byte 6) and ArduPilot's CX-OF driver (25 Hz, low byte first, byte 6 "t-motion") **disagree** ([[ThoneFlow]] §3.2). Bench captures decide.
- The sensor gives **no height**.

## 2. Decision

1. **Characterise first:** sensor alone on a USB-serial adapter ([[ThoneFlow]] §9.2). Week 3 of [[Study Plan v2]].
2. **Option B as a time-boxed experiment (1 week):** ThoneFlow TX → D1 mini **D5 (GPIO14)** with EspSoftwareSerial at 19200. Measure with Wi-Fi and the FC link running. Agree the frame-loss threshold **before** testing (TF-10).
3. **No extra MCU (Option C rejected, 2026-10-14).** The program rules out a companion computer on the drone (seame-2.0 drone README), so the team adds no second processor. **If B fails, re-open this ADR.** Candidates then: tune the SoftwareSerial reader, accept a higher frame-loss threshold if the estimator copes, or move to a Betaflight version with a CX-OF driver if one appears.
4. **Height (TF-12): the FC's DPS310 barometer** (2026-10-14). The lab has no ToF rangefinder, and the ThoneFlow cannot measure height (image motion ≈ speed ÷ height, so one camera can't separate the two). The baro is detected and responds to height ([[ADR-014 Betaflight Version|ADR-014]]).
   - **Relative height:** zero the baro on the ground before each flight; use the change from that point, not absolute altitude.
   - **Read it** with `MSP_ALTITUDE` (109) through the ESP ([[MSP Cheat Sheet]]).
   - **Smooth it** with the accelerometer (complementary filter) on the ground; avoid flying below ~0.5 m (ground effect and prop wash disturb the pressure).
   - **Measure first:** baro noise and drift at known heights in the cage, together with the week-5 velocity test ([[Study Plan v2]] §7).
5. Estimation runs on the ground (TF-13).

### Update 2026-10-20 (proposed): position-hold acceptance and fallback

Module 1 needs "holds position" in SITL and in the cage. The numbers below are proposals. Confirm them after the week-5 baro and flow measurements ([[Study Plan v2]] §7).

- **Acceptance:** [[Requirements|REQ-NAV-001]] (SITL: 0.25 m, ±0.15 m, 60 s) and [[Requirements|REQ-NAV-002]] (cage: 0.5 m radius for 30 s, 3 of 3 runs; ±0.3 m height only when height is automatic). The cage tolerance is wider than the SITL one because the baro height (§2.4) scales the flow velocity error.
- **Decide-by 2026-11-13** (Sprint 2 demo, end of the week-4 SoftwareSerial experiment): does Option B deliver flow frames within the agreed loss threshold (TF-10)?
- **Fallback if it doesn't:** position hold is shown in SITL against REQ-NAV-001. In the cage, the ground controller streams velocity setpoints with the pilot correcting drift, and the gap is reported in the sim-to-real report. This ADR is re-opened (§2.3) for the next sensor path. The fallback does not change any safety rule: the pilot keeps the radio and the kill switch.

## 3. Consequences

- The parser stays a portable library ([[ADR-007 Portable Protocol Library and Custom ESP Firmware|ADR-007]]), whichever reader wins.
- The flow data reaches the ground in the STATE message ([[Protocol Spec v0]]).
- Velocity from flow scales with the baro height error. For **position hold** (drive flow to zero) that only changes the effective gain; for commanded speeds and distances the baro accuracy matters more.
- A ToF rangefinder stays a future option if the baro proves too noisy (needs hardware the lab doesn't have).

## 4. Related pages

- [[ThoneFlow]] · [[Wiring and Pinout]] · [[Decision Register]]

## 5. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **ESP8266** | A WiFi-enabled microcontroller used as the drone communication module. |
| **FC** | Flight Controller: the onboard controller responsible for stabilization and control. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **ToF** | Time-of-Flight: a rangefinder that measures height with light. Not available in the lab (ADR-016). |
| **OD-xx / FC-xx / TF-xx** | Open-decision IDs in PC_to_ESP, ESP_to_FC and ThoneFlow. The Decision Register shows which ADR resolves each one. |
