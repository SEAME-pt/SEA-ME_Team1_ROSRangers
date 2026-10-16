---
title: Open Questions
type: tracking
status: active
date: 2026-10-12
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
| 3 | ESP board? | D1 mini → [[ADR-012 ESP UART Pins\|ADR-012]] |
| 4 | Upgrade Betaflight? | **Yes** → [[ADR-014 Betaflight Version\|ADR-014]] |
| 5 | Gestures OK for Module 2? | Yes → [[00 Gesture Pipeline|Gesture Pipeline]] |
| 6 | GitFlow mandatory? | No → the team's [[Dev_Strategy]] model ([[ADR-017 Branching Model\|ADR-017]]) |
| 7 | HIL runners? | Pis/Arduinos available, concept new → [[HIL Runner]] |
| 8 | Ground OS? | Linux → [[ADR-018 Ground OS and ROS 2\|ADR-018]] |
| 9 | Where is the gesturing person? | Coding room, not the cage → [[ADR-015 Gesture Input Source\|ADR-015]] |
| 10 | When did SEA:ME start? | 7 Oct 2026 → dates corrected in [[Study Plan v2]] and [[Roadmap to April 2027]] |
| 11 | Reflash Betaflight and the D1 mini? | **Yes** (2026-10-14) → 2026.6.2 upgrade ([[ADR-014 Betaflight Version\|ADR-014]]) and custom ESP firmware ([[ADR-007 Portable Protocol Library and Custom ESP Firmware\|ADR-007]]) can go ahead |
| 12 | Lab self-hosted runners? | **None** (2026-10-14). We build our own → [[ADR-020 HIL Runner\|ADR-020]], [[HIL Runner]] |
| 13 | Mandated ROS 2 distro? | **No** (2026-10-14) → Jazzy stays ([[ADR-018 Ground OS and ROS 2\|ADR-018]]) |
| 14 | ToF rangefinder or extra MCU? | **Neither** (team, 2026-10-14). No rangefinder in the lab → height from the DPS310 baro. No MCU (no companion computer on the drone) → [[ADR-016 Flow Sensor Path\|ADR-016]] |

## 2. Still open

### For instructors
- [x] Permission to reflash Betaflight (and the D1 mini) on the lab drones. → Yes (#11 above)
- [x] Does the lab already have self-hosted runners with drone hardware? → No, we build our own (#12 above)
- [x] Spare D1 mini for the HIL bench? → Yes, more are available (2026-10-14)
- [ ] Spare FC for the HIL bench? Not confirmed. Without one, the bench FC is a flying unit (GitHub #34).
- [x] Does the class mandate a ROS 2 distro? → No (#13 above)
- [x] Can we add a ToF rangefinder (height) or a small MCU for the flow sensor? → Neither: baro for height, no MCU (#14 above)

### Physical checks
- [x] **Baro:** DPS310 detected in `status`, and the Sensors tab altitude follows the board when raised/lowered (2026-10-14, GitHub #15).
- [ ] ELRS wires really on R2/T2 (multimeter, [[2.5Drone]] #22).
- [ ] T4/R4 continuity to the MCU side, no shorts to neighbours.
- [ ] FC 5V rail: available current for the D1 mini (FC-05).
- [ ] What exactly are the CAM and DV5V pads used for on this board?

### Tests
- [ ] Does a 2026.6.2 cloud build with ALTHOLD fit the 512 KB F722? (week 2)
- [ ] ThoneFlow frame rate (66 vs 25 Hz), byte order and checksum (week 3, [[ThoneFlow]] §9.2).

## 3. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **FC** | Flight Controller: the onboard controller responsible for stabilization and control. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **ROS 2** | The second-generation Robot Operating System middleware used for robotics integration. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
