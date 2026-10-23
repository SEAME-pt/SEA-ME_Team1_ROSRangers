---
title: ADR-010 — MSP Override for Setpoints
type: adr
status: accepted-with-conditions
date: 2026-10-12
updated: 2026-10-23
project: SEA autonomous drone
aliases:
  - D10
  - D10 MSP Override
tags:
  - adr
  - safety
  - msp
  - betaflight
---

# ADR-010 — MSP Override for Setpoints

> [!success] Status: ACCEPTED WITH CONDITIONS (team, 2026-10-12)
> Accepts **P2** and resolves **FC-09** in [[ESP_to_FC]]. The **FC-15** go/no-go rule stays in force: override is acceptable for autonomous use only after props-off evidence for F1–F9.

## 1. Context

| Option | Verdict |
|---|---|
| RX_MSP (the ESP is the only receiver) | ❌ Removes the ELRS kill path. The program and REQ-SAF-001 (was SAF-01), REQ-SAF-002 (was SAF-02) require the radio to always win. |
| CRSF emulation from the ESP | ❌ Same problem, more work |
| **MSP Override** | ✅ ELRS stays the primary receiver. A pilot switch hands control over and back. |

**Verified in Betaflight source** ([[Verified Facts and Sources]]):

- Present in every 4.5.1 build. `msp_override_channels_mask` (default 0) selects **raw receiver channels**. With the default `AETR1234` map: bit 0 roll, 1 pitch, 2 throttle, 3 yaw, 4+ AUX. `MSP_SET_RAW_RC` uses the same order.
- `msp_override_failsafe` defaults to **OFF** → ELRS loss still triggers the normal failsafe with override active (relevant to **FC-10**). Keep it OFF.
- **4.5.1 hazards:**
  - Switch on before the first frame → masked channels = 885 µs (full low stick).
  - The sender stops → the last values are held forever.
  - A short frame → missing masked channels = 885 µs.
- **2026.6.x** adds a 300 ms per-channel freshness guard that falls back to ELRS values ([[ADR-014 Betaflight Version|ADR-014]]).

## 2. Decision

MSP Override (mode ID 50), with these **mandatory conditions**:

1. AUX channels (ARM, modes, the override switch itself) are **never** in the mask (REQ-SAF-003 (was SAF-03), REQ-SAF-004 (was SAF-04)).
2. Every frame carries **all 8 channels**.
3. The ESP streams neutral values **before** the pilot flips the override switch. The pilot only flips it when telemetry shows `STREAMING`.
4. Staged masks: **8** (yaw) → **11** (roll+pitch+yaw, pilot on throttle) → **15** (all four, only with altitude hold or a validated altitude loop).
5. Bump-free handover: the ground initialises setpoints from the pilot's current values (`MSP_RC`).
6. On 4.5.1: **props-off bench tests only** ([[ESP_to_FC]] §11) until the upgrade.

### Update 2026-10-23: kill switch and takeover switch (team decision)

The radio has two separate safety switches. The conditions above are unchanged.

- **Kill switch:** SA, which is also the ARM switch. Releasing SA disarms and stops the motors. Tested on the bench on 2026-10-22 ([[Radio Link Tests]] RL-12, GitHub #22).
- **Takeover switch:** the MSP Override switch (the "override switch" in condition 3). Turning it off ends ground control: from that moment until it is turned on again, the drone takes commands only from the radio. Its AUX channel is not mapped yet ([[Betaflight Setup]] §1). It stays out of the mask (condition 1).

## 3. Consequences

- The bench plan in [[ESP_to_FC]] §11 is the gate before any override flight.
- REQ-SAF-007 (was SAF-07) (pilot awareness): telemetry `override_active` plus a switch-position convention. OSD element still open (FC-14).

## 4. Related pages

- [[ESP_to_FC]] §5, §8, §9 · [[Betaflight Setup]] · [[Decision Register]]

> 2026-10-20: requirement IDs renumbered to the `REQ-<cat>-NNN` scheme (see [[Requirements]], issue #57). Old IDs are kept in brackets. The decision is unchanged.

## 5. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **CRSF** | Crossfire serial protocol: the serial wire format between the ELRS receiver and the FC (UART2), and between the radio and its ELRS module. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **FC** | Flight Controller: on this project the OddityRC F7 AIO (STM32F722, Betaflight target `ODDITYRCF7AIO`) of the 2.5" drone. It stabilises the aircraft and takes MSP from the ESP on UART4. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **Telemetry** | Periodic data sent from a system to report state, measurements, or health. |
| **ELRS** | ExpressLRS: the open-source radio-control link between the RadioMaster Pocket and the BETAFPV Nano receiver on the drone. |
| **REQ-SAF-001 to 008** | Safety requirements (old IDs SAF-01 to SAF-08) defined in ESP_to_FC §8 and registered in [[Requirements]]: independent ELRS path, pilot takeover, no arming from code. |
| **OD-xx / FC-xx / TF-xx** | Open-decision IDs in PC_to_ESP, ESP_to_FC and ThoneFlow. The Decision Register shows which ADR resolves each one. |
| **Kill switch** | SA on the RadioMaster, which is also the ARM switch: releasing it disarms and stops the motors ([[Radio Link Tests]] RL-12). |
| **Takeover switch** | The MSP Override switch on the radio: turning it off ends ground control, and from then on the drone takes commands only from the radio (ADR-010). Its channel is not mapped yet. |
| **Mask** | `msp_override_channels_mask`: which stick channels the ground controls (8 = yaw, 11 = roll+pitch+yaw, 15 = all four). |
