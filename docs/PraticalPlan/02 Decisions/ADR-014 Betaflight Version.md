---
title: ADR-014 — Upgrade Betaflight to 2026.6.2
type: adr
status: accepted
date: 2026-10-12
updated: 2026-10-23
project: SEA autonomous drone
aliases:
  - D14
  - D14 Betaflight Version
tags:
  - adr
  - betaflight
  - safety
---

# ADR-014 — Upgrade Betaflight to 2026.6.2

> [!success] Status: ACCEPTED: **YES, upgrade** (team, 2026-10-12). Instructors gave permission to reflash (2026-10-14).
> Mitigates **FC-11** in [[ESP_to_FC]].

## 1. Context

- Installed: Betaflight **4.5.1** (17 Oct 2024), MSP API **1.46** ([[2.5Drone]]).
- 4.5.1 MSP Override has **no staleness guard** ([[ADR-010 MSP Override|ADR-010]]).
- The latest release is **2026.6.2**. Its MSP Override has a 300 ms per-channel freshness guard (first in 2026.6.0, absent in 4.5.5 and 2025.12.5).
- 2026.6.x also has ALTHOLD and POS HOLD, but they are **not default** in cloud builds for 512 KB-flash MCUs like our F722.
- 2026.6.2 **SITL builds and answers MSP over TCP** (verified). 4.5.1 SITL does not link out of the box.

> [!note] Barometer (updated 2026-10-14)
> The DPS310 **is detected**: `status` on 2026-10-13 and 2026-10-14 shows `I2C:1`, `BARO=DPS310`, 0 I2C errors, and the Sensors tab altitude responds to height changes ([[2.5Drone]]). The earlier `I2C:0` reading is superseded. Whether ALTHOLD fits the 2026.6.2 cloud build is still open.

## 2. Decision

Upgrade to **2026.6.2** in week 2 of Module 1. Do it after a recorded 4.5.1 baseline flight and **before any MSP Override flight**.

### Update 2026-10-23: timing

The upgrade follows the recorded 4.5.1 failsafe test (done 2026-10-22, GitHub #22) and the baseline hover (GitHub #23). If the hover is late, the upgrade may slip to Study Plan week 3 (from 2026-11-02). The rule is unchanged: **no MSP Override flight before the upgrade**.

## 3. Consequences

| Cost | Mitigation |
|---|---|
| A 4.5 `diff all` cannot be pasted into 2026.6 | Re-enter the configuration by hand, using the old diff as a checklist ([[Betaflight Setup]]) |
| Tune and filters may differ | Hover test + blackbox log before any autonomy |
| ALTHOLD may not fit the cloud build, or the baro may be missing | Fly without it. Throttle stays with the pilot (mask 11). |
| Lab hardware rules | Reflash permission granted (2026-10-14, [[Open Questions]]) |

If permission is refused: stay on 4.5.1, use masks 8/11 only, the ESP streams neutral values forever in failsafe, and override is only switched on when the ESP shows `STREAMING`.

## 4. Related pages

- [[Betaflight Setup]] · [[Verified Facts and Sources]] · [[Decision Register]]

## 5. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **API** | Application Programming Interface: a defined way for software components to communicate. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **FC** | Flight Controller: on this project the OddityRC F7 AIO (STM32F722, Betaflight target `ODDITYRCF7AIO`) of the 2.5" drone. It stabilises the aircraft and takes MSP from the ESP on UART4. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **TCP** | Transmission Control Protocol: reliable, ordered, connection-oriented transport. |
| **SITL** | Software In The Loop: the flight code runs on the PC against a simulator. |
| **OD-xx / FC-xx / TF-xx** | Open-decision IDs in PC_to_ESP, ESP_to_FC and ThoneFlow. The Decision Register shows which ADR resolves each one. |
| **Betaflight** | The open-source FC firmware on the drone: 4.5.1 installed, 2026.6.2 planned (ADR-014). |
| **ALTHOLD** | Betaflight altitude-hold mode that uses the barometer. Only in 2026.6.x builds, and not default on 512 KB F7 targets (ADR-014). |
| **diff all** | Betaflight CLI command that prints every setting changed from default. Baselines are in `drone/config/betaflight/`. |
| **MSP Override** | Betaflight feature that lets MSP supply the values of selected RC channels, while ELRS stays the primary receiver (ADR-010). |
