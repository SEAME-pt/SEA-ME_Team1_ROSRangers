---
title: Open Questions
type: tracking
status: active
date: 2026-10-12
updated: 2026-10-26
project: SEA autonomous drone
tags:
  - questions
---

# Open Questions

> [!note] The remaining **Open** items in the communication drafts are tracked there: [[PC_to_ESP]] §12, [[ESP_to_FC]] §12.2, [[ThoneFlow]] §10.2. This page lists questions for people (instructors, team) and things to check physically.

---

## 1. Answered (2026-10-12)

| # | Question | Answer → effect |
|---|---|---|
| 1 | Exposed UART pads? | T1, R1, R2, R3, T4, R4 (+CAM, SCL, SDA, power) → [[ADR-011 FC UART Allocation\|ADR-011]] |
| 2 | Baro present? | **Yes, DPS310 detected.** `status` 2026-10-13/14: `I2C:1`, `BARO=DPS310`, 0 I2C errors. ALTHOLD is possible if it fits the 2026.6.2 build → [[ADR-014 Betaflight Version\|ADR-014]] |
| 3 | ESP board? | ESP → [[ADR-012 ESP UART Pins\|ADR-012]] |
| 4 | Upgrade Betaflight? | **Yes** → [[ADR-014 Betaflight Version\|ADR-014]] |
| 5 | Gestures OK for Module 2? | Yes → [[00 Gesture Pipeline\|Gesture Pipeline]] |
| 6 | GitFlow mandatory? | No → the team's [[Dev_Strategy]] model ([[ADR-017 Branching Model\|ADR-017]]) |
| 7 | HIL runners? | Pis/Arduinos available, concept new → [[HIL Runner]] |
| 8 | Ground OS? | Linux → [[ADR-018 Ground OS and ROS 2\|ADR-018]] |
| 9 | Where is the gesturing person? | Coding room, not the cage → [[ADR-015 Gesture Input Source\|ADR-015]] |
| 10 | When did SEA:ME start? | 7 Oct 2026 → dates corrected in [[Study Plan v2]] and [[Roadmap to April 2027]] |
| 11 | Reflash Betaflight and the ESP? | **Yes** (2026-10-14) → 2026.6.2 upgrade ([[ADR-014 Betaflight Version\|ADR-014]]) and custom ESP firmware ([[ADR-007 Portable Protocol Library and Custom ESP Firmware\|ADR-007]]) can go ahead |
| 12 | Lab self-hosted runners? | **None** (2026-10-14). We build our own → [[ADR-020 HIL Runner\|ADR-020]], [[HIL Runner]] |
| 13 | Mandated ROS 2 distro? | **No** (2026-10-14) → Jazzy stays ([[ADR-018 Ground OS and ROS 2\|ADR-018]]) |
| 14 | ToF rangefinder or extra MCU? | **Neither** (team, 2026-10-14). No rangefinder in the lab → height from the DPS310 baro. No MCU (no companion computer on the drone) → [[ADR-016 Flow Sensor Path\|ADR-016]] |

### 1.1 Answered (2026-10-16, GitHub #16)

| # | Question | Answer → effect |
|---|---|---|
| 15 | ELRS wires on R2/T2? | **Yes** (team, multimeter, 2026-10-16) → [[2.5Drone]] #22 is now High certainty, UART2 stays ELRS ([[ADR-011 FC UART Allocation\|ADR-011]]) |
| 16 | T4/R4 reach the MCU, no shorts? | **Yes** (team, multimeter, 2026-10-16): continuity to the MCU side, no short to the neighbouring pads → T4/R4 are cleared for the ESP ([[ADR-011 FC UART Allocation\|ADR-011]], [[Wiring and Pinout]]) |
| 17 | What are the CAM and DV5V pads? | From the OddityRC wiring diagram ([vendor diagram](https://www.rotorama.com/cms/assets/docs/6d96909d797b5e734f781ca47138a567/35018-0/6x.jpg)): **CAM** = analog camera video input (to the AT7456E OSD). **DV5V** = the 5 V pad the diagram uses to power the receiver (TBS, ELRS or SBUS). Neither is a control or UART pad. The Betaflight target has no camera-control pin (`CAMERA_CONTROL_PIN` is commented out). → [[Wiring and Pinout]] §1 |

## 2. Still open

### For instructors
- [x] Permission to reflash Betaflight (and the ESP) on the lab drones. → Yes (#11 above)
- [x] Does the lab already have self-hosted runners with drone hardware? → No, we build our own (#12 above)
- [x] Spare ESP for the HIL bench? → Yes: the team has one spare ESP and may use it for HIL (2026-10-22, [[ADR-020 HIL Runner|ADR-020]]). It is on the HIL Pi (GitHub #48).
- [x] Spare FC for the HIL bench? → No (2026-10-20, GitHub #34). FC-side HIL uses Betaflight SITL or the flight unit on the bench (GitHub #61).
- [x] Does the class mandate a ROS 2 distro? → No (#13 above)
- [x] Can we add a ToF rangefinder (height) or a small MCU for the flow sensor? → Neither: baro for height, no MCU (#14 above)

### Physical checks
- [x] **Baro:** DPS310 detected in `status`, and the Sensors tab altitude follows the board when raised/lowered (2026-10-14, GitHub #15).
- [x] ELRS wires really on R2/T2 (multimeter, [[2.5Drone]] #22). → Yes (#15 above)
- [x] T4/R4 continuity to the MCU side, no shorts to neighbours. → Yes (#16 above)
- [ ] FC 5V rail: available current for the ESP (FC-05).
- [ ] Motor pole count: the FC has `motor_poles = 12`, but 1405 motors are often 14-pole. Count the magnets in one motor bell; if 14, set `motor_poles = 14` (with team approval and a saved `diff all`), because RPM readings and the RPM filter depend on it ([[Betaflight Setup]] §1.2).
- [x] What exactly are the CAM and DV5V pads used for on this board? → camera video in, receiver 5 V (#17 above)
- [x] Is the ELRS red (5 V) wire on **DV5V** or on a **5V** pad? → **5V** (team, 2026-10-16). Moving it to DV5V: GitHub #41.

### Safety
- [ ] **Network isolation for the transparent bridge:** is an isolated network needed, or is the shared SEAME Wi-Fi acceptable? Under evaluation (team, 2026-10-20). Not a rule yet ([[Safety Rules]] §2).
- [ ] **Bridge battery vs HIL "no LiPo":** [[Safety Rules]] #13 allows a battery for bridge tests, #10 forbids one on the HIL bench. Conflict when the flight unit is the HIL target (GitHub #61). Team to decide (2026-10-20).
- [x] ~~Name the second trained safety pilot.~~ Resolved 2026-10-23: one trained safety pilot (BrunoBMFT) is enough; the second person at a session rotates ([[Team Tracks]] §2).

### Process
- [ ] **Second track label per issue:** allow one extra `track:` label when a task really belongs to two tracks (e.g. A+B bench tests, B+E ThoneFlow spike), instead of exactly one. Raised 2026-10-26; the team leans towards allowing it. Sprint 2 issues #104 and #108 already use two. Decide at the next weekly; then update the rule in `CLAUDE.md` (GitHub #110).

### Tests
- [ ] Does a 2026.6.2 cloud build with ALTHOLD fit the 512 KB F722? (week 2)
- [ ] ThoneFlow frame rate (66 vs 25 Hz), byte order and checksum (week 3, [[ThoneFlow]] §9.2).

## 3. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **FC** | Flight Controller: on this project the OddityRC F7 AIO (STM32F722, Betaflight target `ODDITYRCF7AIO`) of the 2.5" drone. It stabilises the aircraft and takes MSP from the ESP on UART4. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **OSD** | On-Screen Display: the chip that draws flight data over the analog camera video. |
| **ROS 2** | The second-generation Robot Operating System middleware used for robotics integration. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **ELRS** | ExpressLRS: the open-source radio-control link between the RadioMaster Pocket and the BETAFPV Nano receiver on the drone. |
| **MCU** | Microcontroller unit: the processor chip (STM32F722 on the FC, ESP8266 on the ESP module). |
| **ToF** | Time-of-Flight: a rangefinder that measures height with light. Not available in the lab (ADR-016). |
| **OD-xx / FC-xx / TF-xx** | Open-decision IDs in PC_to_ESP, ESP_to_FC and ThoneFlow. The Decision Register shows which ADR resolves each one. |
| **RPM** | Motor revolutions per minute, reported by each ESC over bidirectional DShot. |
| **Betaflight** | The open-source FC firmware on the drone: 2026.6.2 installed since 2026-10-28, 4.5.1 before (ADR-014). |
| **Props off** | Propellers physically removed. Required for every bench test ([[Safety Rules]] #1). |
| **Kill switch** | SA on the RadioMaster, which is also the ARM switch: releasing it disarms and stops the motors ([[Radio Link Tests]] RL-12). |
| **Takeover switch** | The MSP Override switch on the radio: turning it off ends ground control, and from then on the drone takes commands only from the radio (ADR-010). Its channel is not mapped yet. |
| **E-stop** | Emergency stop: when something fails, the drone is brought to a safe stop, in the air or on the ground. The faults it must cover are in [[Testing Strategy]] §2. |
