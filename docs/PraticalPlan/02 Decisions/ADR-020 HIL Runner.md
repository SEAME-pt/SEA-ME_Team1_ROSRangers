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

### Update 2026-10-22: one spare ESP, bench built

The team has **one spare ESP** (same board as the flight ESP) and permission to use it for HIL (team, 2026-10-22). This replaces the "no spare ESP" part of the 2026-10-20 update. There is still **no spare FC**.

- The Raspberry Pi 4 runner is registered (`hil-drone-pi`, labels `self-hosted, linux, ARM64, hil-drone`) and the spare ESP is on its USB (`/dev/hil-esp`). GitHub #48.
- **The spare ESP is not wired to any FC**, so nothing drives its UART0. The ADR-012 reason for "no USB data to the ESP" does not apply, and CI may **flash it over USB**. Auto-reset over DTR/RTS works (checked with `esptool`), so no button press is needed.
- The 2026-10-20 correction (no USB data, OTA only) stays in force for any ESP that is wired to an FC, including the flight unit.
- Without an FC, the first HIL tests cover the ESP alone (flash, boot, Wi-Fi, protocol). FC-side checks come from Betaflight SITL or the flight unit when it is on the bench. That split is GitHub #61.

### Update 2026-10-26: HIL scope decided (GitHub #61)

Team decision, from the #61 spike. The instructors set no extra criteria beyond "make sure everything works correctly".

- **The HIL target is the spare ESP only.** The FC side is tested in simulation: Betaflight 2026.6.2 SITL for MSP-level behaviour and PX4 SITL + Gazebo for flight behaviour ([[ADR-019 Sim Backends|ADR-019]]). The flight unit is not part of the HIL job.
- **Tested by hand, props off, on a booked slot:** the wired connections, the kill switch and the radio failsafe. Real FC reaction to MSP Override ([[ESP_to_FC]] §11, GitHub #104) and the real-time profile also need the flight unit.
- **When it runs:** after each merge to `dev` (push trigger, as in `hil.yaml`), never on pull requests. HIL must be green before `dev` → `main`. This gate is **not set up yet** (no required checks exist, ADR-022).
- **Evidence:** a green run of the job.
- **Unattended:** only this bench (Pi 4 + spare ESP on USB, no FC, no LiPo, no motors) may run unattended ([[Safety Rules]] #17). Every other bench or flight session is attended.
- **Repo stays public.** Only collaborators can open PRs (interaction limit `collaborators_only`, set 2026-10-26, expires 2027-04-26, renew it). Fork-PR workflows need approval for all external contributors.
- **Coverage:** minimum 80 % (REQ-SW-003). Reaching 100 % is allowed.
- Open: the USB Wi-Fi dongle for the Wi-Fi tests.

### Update 2026-10-26: Wi-Fi tests by time-slicing, no dongle

Team decision, from the experiment in GitHub #48. Replaces the open dongle item in the update above.

- The Pi has one Wi-Fi radio and no free Ethernet port at the bench. It **time-slices** the radio: a job step leaves the lab Wi-Fi, joins the spare ESP's SoftAP, runs the test and re-joins the lab Wi-Fi. A watchdog outside the job re-joins the lab Wi-Fi if the job dies.
- **Keep each offline window under 2 minutes.** Measured 2026-10-26: jobs survived 180 s and 300 s offline. GitHub failed a job whose runner vanished after about 11 minutes. GitHub does not document a limit.
- A USB Wi-Fi dongle is no longer needed. This replaces "Ethernet for GitHub" in [[ADR-006 Network|ADR-006]] §3.
- The runner service is enabled on boot. A runner offline for more than 14 days is deleted by GitHub.

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
| **Self-hosted runner** | A machine of ours (the Pi 4 `hil-drone`) that runs GitHub Actions jobs, used for the HIL job. |
| **SITL** | Software In The Loop: the flight code runs on the PC against a simulator. |
| **OTA** | Over-the-air: flashing the ESP firmware over Wi-Fi instead of USB (env `d1_mini_ota`). |
