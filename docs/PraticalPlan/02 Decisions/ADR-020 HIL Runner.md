---
title: ADR-020 — Hardware-in-the-Loop Runner
type: adr
status: accepted
date: 2026-10-12
project: SEA autonomous drone
aliases:
  - D20
  - D20 HIL Runner
tags:
  - adr
  - ci
  - hil
---

# ADR-020 — Hardware-in-the-Loop Runner

> [!success] Status: ACCEPTED (team, 2026-10-12). Instructors confirmed there are no lab runners (2026-10-14): we build our own.

## 1. Context

- Module 1 requires "a hardware-in-the-loop job on a self-hosted runner that flashes and tests the target".
- The program lists lab self-hosted runners as shared equipment, but instructors confirmed none are available (2026-10-14).
- The team has Raspberry Pis and Arduinos. The **Raspberry Pi 5** in [[2.5Drone]] is the ground computer option, so keep it free for that.

## 2. Decision

If the lab provides a runner, use it. Otherwise:

- A **dedicated Raspberry Pi** registered as a GitHub Actions self-hosted runner (label `hil-drone`).
- Attached by USB: a spare ESP and the FC (or a spare FC).
- **No battery, no props.** USB power alone cannot spin the motors.

Details in [[HIL Runner]].

### Update 2026-10-20: no spare FC or ESP

There is **no spare FC and no spare ESP** (GitHub #34, 2026-10-20). The "spare ESP and the FC (or a spare FC)" in §2 therefore cannot be built as written. The HIL bench has to use the **flight unit**, possibly together with simulation (PX4 SITL / Gazebo, Betaflight SITL). The plan is not decided here: it is the spike in GitHub #61. The runner itself is GitHub #48. The rules stay: **no LiPo on the HIL bench, ever** ([[Safety Rules]]). See [[HIL Runner]].

### Correction 2026-10-20: USB to the ESP

§2 says the ESP is "attached by USB". This conflicts with [[ADR-012 ESP UART Pins|ADR-012]]: the ESP's USB is never connected while the FC is wired to UART0. The Pi powers the ESP on its 5V pin and CI flashes it over the air, as [[HIL Runner]] §2 already says. Only the FC is attached by USB. Read §2 with this correction.

## 3. Related pages

- [[HIL Runner]] · [[CI-CD Overview]] · [[Decision Register]]

## 4. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **CI** | Continuous Integration: automated validation of proposed changes. |
| **FC** | Flight Controller: on this project the OddityRC F7 AIO (STM32F722, Betaflight target `ODDITYRCF7AIO`) of the 2.5" drone. It stabilises the aircraft and takes MSP from the ESP on UART4. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **USB** | Universal Serial Bus: used to flash the ESP and to power or configure the FC from a PC. |
