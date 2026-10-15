---
title: ESP Bench Test Plan
type: test-plan
status: draft
date: 2026-10-12
project: SEA autonomous drone
aliases:
  - ESP8266 Bench Tests
  - D1 mini tests
tags:
  - testing
  - esp8266
  - bench
  - safety
---

# ESP Bench Test Plan

> [!warning] Status: PLAN. No test below has been run yet.
> **Rule: nothing is soldered to the flight controller until Stage 1 and Stage 2 have passed** and the [[ESP_to_FC]] §10 checklist is complete. Fill in the result tables as you go and commit them.

> [!info] Why test before soldering
> Desoldering a D1 mini from a 2.5" AIO risks lifting the FC pads. Testing first also separates hardware problems from software problems.

---

## 1. Stage 1 — D1 mini alone (USB, no drone)

**Setup:** D1 mini on the laptop's USB. No FC connected. Tools: `esptool.py`, PlatformIO, a USB power meter.

| ID | Test | What it proves | Pass criterion | Links |
|---|---|---|---|---|
| ESP-S1-01 | Back up the AT firmware: `esptool.py flash_id`, then `read_flash 0 0x400000 d1mini_at_1.7.4.0_backup.bin` | We can always go back to the original firmware | File is exactly the flash size reported by `flash_id` | [[ADR-007 Portable Protocol Library and Custom ESP Firmware\|ADR-007]] |
| ESP-S1-02 | Flash a blink sketch, then restore the backup once, then flash blink again | Board, USB chip and flash are OK; restore works | Blink runs; AT firmware answers `AT` after the restore | ADR-007 |
| ESP-S1-03 | Capture boot output at **74880 baud** | What the FC will see on its RX at ESP power-up | Bytes recorded here | [[ADR-012 ESP UART Pins\|ADR-012]], F5 in [[ESP_to_FC]] |
| ESP-S1-04 | SoftAP up with WPA2-PSK; laptop connects, 20 reboots | The network design works on this board | 20/20 connects; time-to-connect recorded | [[ADR-006 Network\|ADR-006]] |
| ESP-S1-05 | UDP echo at 50 Hz for 10 min, `WIFI_NONE_SLEEP` | Real latency and loss | Median, p99, max RTT and loss % recorded; threshold agreed beforehand | OD-05/06/07 in [[PC_to_ESP]] |
| ESP-S1-06 | Same as S1-05 with sleep enabled | Why ADR-006 disables sleep | Difference recorded | ADR-006 |
| ESP-S1-07 | TCP session open + UDP stream at the same time | TCP does not stall control | No UDP gap above the S1-05 max | [[ADR-005 TCP Session\|ADR-005]] |
| ESP-S1-08 | Current draw with a USB power meter during Wi-Fi transmit | Whether the FC 5V rail can feed it | Peak and average mA recorded | FC-05, [[ADR-013 Wiring and Power\|ADR-013]] |
| ESP-S1-09 | Range: laptop in the coding room, D1 mini in the cage | The link reaches through the building | S1-05 repeated at the real distance; loss and RTT recorded | ADR-006 |
| ESP-S1-10 | Watchdog: hang the loop on purpose | The ESP recovers on its own, in a safe state | Reboot + reset reason logged; no control output after reboot | REQ-FS-04 in [[PC_to_ESP]] |

> [!tip] Reuse
> S1-05 to S1-07 are the first version of the protocol code. Write them against the portable library so they become unit/HIL tests later ([[Testing Strategy]]).

## 2. Stage 2 — D1 mini + FC on the bench, temporary wiring

**Setup**

- FC powered **by USB only**. **No battery, no props**: the motors cannot spin.
- D1 mini connected to the FC with **temporary** wires (pogo pins, or wires held by tape and a clip). UART4 (T4/R4), or UART1 (T1/R1) if easier to reach ([[ADR-011 FC UART Allocation|ADR-011]]).
- **The D1 mini's USB is never connected while the FC wires are attached** ([[ADR-012 ESP UART Pins|ADR-012]]). Power it from a USB power bank or the 5V pin. Logs go over Wi-Fi.
- MSP enabled on the chosen UART at 115200, with the pre-change `diff all` saved ([[Betaflight Setup]]).

| ID | Test | What it proves | Pass criterion | Links |
|---|---|---|---|---|
| ESP-S2-01 | `MSP_API_VERSION` round trip | Wiring, crossover and baud are right | Reply 1.46 (4.5.1), 100/100 requests | [[ADR-009 ESP to FC Link\|ADR-009]] |
| ESP-S2-02 | Power-cycle the ESP 20× while the FC runs | The FC ignores the boot bytes | No FC errors, no change in Betaflight | F5, F7 in [[ESP_to_FC]] |
| ESP-S2-03 | `MSP_ATTITUDE` while tilting the FC by hand | Telemetry path FC → ESP → laptop | Angles follow the tilt | TEL-03 |
| ESP-S2-04 | `MSP_SET_RAW_RC` with distinct values → `MSP_RC` readback | Channel order (AETR) | Each value lands on the expected channel | Phase 3 of [[ESP_to_FC]] §11 |
| ESP-S2-05 | ELRS receiver bound, Wi-Fi streaming next to it | Wi-Fi ↔ ELRS coexistence | ELRS link quality recorded on channels 1, 6, 11 | [[Study Plan v2]] week 2 |
| ESP-S2-06 | Unplug a jumper wire mid-stream | The ESP detects the lost FC | Error reported to the ground within 500 ms | F2 in [[ESP_to_FC]] |

MSP Override tests (Phases 4–5 of [[ESP_to_FC]] §11) need ELRS bound and the mode configured. Run them in this same setup before soldering.

## 3. Stage 3 — mounting and soldering

Only after Stages 1 and 2 pass:

1. Run the [[ESP_to_FC]] §10 checklist: T4/R4 continuity to the MCU side, no shorts to neighbouring pads, T4 idle level measured.
2. Check the FC 5V rail can supply the **peak current from ESP-S1-08**.
3. Mount per [[ADR-021 Removable ESP Connector|ADR-021]] (proposed: a 4-pin connector, so the D1 mini can be unplugged).
4. Repeat ESP-S2-01 and ESP-S2-03 on the installed hardware.

## 4. Results log

| Test ID | Date | Who | Result (pass/fail + numbers) | Notes / issue # |
|---|---|---|---|---|
| ESP-S1-01 | | | | |
| ESP-S1-02 | | | | |
| ESP-S1-03 | | | | |
| ESP-S1-04 | | | | |
| ESP-S1-05 | | | | |
| ESP-S1-06 | | | | |
| ESP-S1-07 | | | | |
| ESP-S1-08 | | | | |
| ESP-S1-09 | | | | |
| ESP-S1-10 | | | | |
| ESP-S2-01 | | | | |
| ESP-S2-02 | | | | |
| ESP-S2-03 | | | | |
| ESP-S2-04 | | | | |
| ESP-S2-05 | | | | |
| ESP-S2-06 | | | | |

## 5. Related pages

- [[ESP_to_FC]] §10–§11 · [[PC_to_ESP]] §9.2 · [[Testing Strategy]] · [[Wiring and Pinout]] · [[Safety Rules]]

## 6. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **Commit** | A recorded snapshot of changes in a version-control repository. |
| **ESP8266** | A WiFi-enabled microcontroller used as the drone communication module. |
| **FC** | Flight Controller: the onboard controller responsible for stabilization and control. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **TCP** | Transmission Control Protocol: reliable, ordered, connection-oriented transport. |
| **Telemetry** | Periodic data sent from a system to report state, measurements, or health. |
| **UDP** | User Datagram Protocol: connectionless transport suited to refreshed, time-sensitive data. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **API** | Application Programming Interface: a defined way for software components to communicate. |
