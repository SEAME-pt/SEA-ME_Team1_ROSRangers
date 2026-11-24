---
title: ADR-016 — ThoneFlow-3901U Integration Path
type: adr
status: accepted
date: 2026-10-12
updated: 2026-11-24
project: SEA autonomous drone
aliases:
  - D16
  - D16 Flow Sensor Path
  - ADR-016 MTF-02P Flow and Height Sensor
tags:
  - adr
  - optical-flow
  - thoneflow-3901u
  - mtf-02p
---

# ADR-016 — ThoneFlow-3901U Integration Path

> [!success] Status: ACCEPTED (2026-10-12, revised 2026-10-14)
> Resolves **TF-01**, **TF-02**, **TF-11** and **TF-12** in [[ThoneFlow]]. 2026-10-14: option C dropped and the height source chosen (§2.3, §2.4). It replaces the earlier "SoftwareSerial on the ESP" decision, following the risk analysis in [[ThoneFlow]] §6.
>
> **2026-11-24: the ThoneFlow is dropped and the MicoAir MTF-02P (flow + ToF height on an FC UART) replaces it** (update below). The title keeps the old name so links still work.

## 1. Context

- **Option A (FC UART) is ruled out:** Betaflight 4.5.1 has **no optical-flow driver at all**. 2026.6.2 only supports MT and UPT1 sensors, not CX-OF. INAV supports CX-OF but has **no target** for `ODDITYRCF7AIO`. All of this was verified in source ([[Verified Facts and Sources]]).
- The ESP8266 has no free hardware RX ([[ThoneFlow]] §6). SoftwareSerial under Wi-Fi load is unproven.
- The datasheet (66 Hz, high byte first, checksum at byte 6) and ArduPilot's CX-OF driver (25 Hz, low byte first, byte 6 "t-motion") **disagree** ([[ThoneFlow]] §3.2). Bench captures decide.
- The sensor gives **no height**.

## 2. Decision

1. **Characterise first:** sensor alone on a USB-serial adapter ([[ThoneFlow]] §9.2). Week 3 of [[Study Plan v2]].
2. **Option B as a time-boxed experiment (1 week):** ThoneFlow TX → ESP **D5 (GPIO14)** with EspSoftwareSerial at 19200. Measure with Wi-Fi and the FC link running. Agree the frame-loss threshold **before** testing (TF-10).
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

### Update 2026-11-06 (proposed): height source

§2.4 chose the barometer and kept a ToF rangefinder as a future option "if the baro proves too noisy" (§3). The 2026-10-29 flights point that way: the DPS310 sits uncovered under the props, its reading jumps when the props blow on it, and ALTHOLD engaged during a climb kept climbing twice (GitHub #134). The goal is a drone that holds height and position without depending on the pilot's skill.

- **The ThoneFlow alone does not solve it.** It measures no height, so it cannot hold height, and its image motion cannot be turned into a speed without a height (§2.4). Betaflight also needs a rangefinder for optical flow, so a flow-only position hold would have to be written by the team on the ESP or the ground.
- **Proposal: a ToF height + optical-flow module on a free FC UART**, which Betaflight 2026.6.2 supports by name (`rangefinder_hardware`, `opticalflow_hardware = MT`). ALTHOLD can then prefer the rangefinder (`altitude_source = RANGEFINDER_PREFER`) and Position Hold can use the flow (`poshold_position_source = OPTICALFLOW_ONLY`), with no new code. The comparison of four sensors and the sources are in [[Height Sensor Study]]; the best match there is the MicoAir MTF-02P.
- **Not available in the lab.** Until the team decides and the hardware is there, §2.4 stands. A cheap check meanwhile: cover the baro with foam and compare the blackbox height against the uncovered flights.
- If accepted, it needs dated entries in [[ADR-011 FC UART Allocation|ADR-011]] (UART) and in §2.4 here. The free UART is UART1, which is the ESP's only spare / fallback port in ADR-011: using it for the sensor removes that fallback.

### Update 2026-11-24 (accepted): the MTF-02P replaces the ThoneFlow

The facilitators approved and supplied a **MicoAir MTF-02P** (SKU `MA_OF_MTF02P`): optical flow and a ToF rangefinder in one module, the best match in [[Height Sensor Study]] §3.4. This accepts the 2026-11-06 proposal above and makes the 2026-10-20 SoftwareSerial decide-by question moot.

- **The ThoneFlow-3901U is dropped.** Option B (SoftwareSerial on the ESP, §2.2), the §2.3 fallbacks and the open TF-xx items in [[ThoneFlow]] are no longer pursued. That note stays as history.
- **Path:** the MTF-02P goes on a free FC UART with the MSP protocol, and Betaflight 2026.6.2 reads it by name (`rangefinder_hardware = MTF02P`, `opticalflow_hardware = MT`). No ESP reader and no extra MCU.
- **Height (§2.4):** the rangefinder replaces the barometer as the height source once the sensor is fitted and checked on the bench, props off ([[Safety Rules]] #1). Until then §2.4 stands.
- **Not fitted yet (2026-11-24):** the sensor is not soldered because the drone has not arrived. After that, the steps in [[Height Sensor Study]] §5 apply: bench check in the Configurator Sensors tab, then ALTHOLD with `altitude_source = RANGEFINDER_PREFER`, then Position Hold.
- **UART (team, 2026-11-24): UART1** (T1/R1), the only free FC UART. The ESP loses its spare / fallback port ([[ADR-011 FC UART Allocation|ADR-011]] update 2026-11-24).
- **Position hold (team, 2026-11-24): Betaflight** Position Hold with the flow (`poshold_position_source = OPTICALFLOW_ONLY`), not the ground controller. The ground sends no position loop for [[Requirements|REQ-NAV-002]]; Betaflight's own estimator combines the accelerometer, baro, rangefinder and flow ([[Height Sensor Study]] §3.4).
- **Code still written for the ThoneFlow:** the Python parser `sd_protocol/thoneflow.py` and the `flow_status` field of the STATE message (ThoneFlow §8.1 values). Removing or reusing them is a follow-up, not part of this entry; ThiagoTEP updates the ThoneFlow code and issues.

## 3. Consequences

- The parser stays a portable library ([[ADR-007 Portable Protocol Library and Custom ESP Firmware|ADR-007]]), whichever reader wins.
- The flow data reaches the ground in the STATE message ([[Protocol Spec v0]]).
- Velocity from flow scales with the baro height error. For **position hold** (drive flow to zero) that only changes the effective gain; for commanded speeds and distances the baro accuracy matters more.
- A ToF rangefinder stays a future option if the baro proves too noisy (needs hardware the lab doesn't have). *2026-11-24: now chosen, the MTF-02P (update above).*

## 4. Related pages

- [[Height Sensor Study]] · [[ThoneFlow]] · [[Wiring and Pinout]] · [[Decision Register]]

## 5. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **ESP8266** | The Wi-Fi microcontroller of the drone's ESP module (an ESP-12F). It bridges the ground PC to the FC over UART4. |
| **FC** | Flight Controller: on this project the OddityRC F7 AIO (STM32F722, Betaflight target `ODDITYRCF7AIO`) of the 2.5" drone. It stabilises the aircraft and takes MSP from the ESP on UART4. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **ToF** | Time-of-Flight: a rangefinder that measures height with light. The MTF-02P has one (update 2026-11-24). |
| **MTF-02P** | MicoAir optical-flow + ToF rangefinder module (UART, MSP), supplied by the facilitators on 2026-11-24. Read by Betaflight on FC UART1. |
| **OD-xx / FC-xx / TF-xx** | Open-decision IDs in PC_to_ESP, ESP_to_FC and ThoneFlow. The Decision Register shows which ADR resolves each one. |
