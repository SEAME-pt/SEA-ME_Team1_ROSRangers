---
title: Betaflight Setup
type: procedure
status: draft
date: 2026-10-12
project: SEA autonomous drone
tags:
  - betaflight
  - procedure
---

# Betaflight Setup

> [!warning] Status: DRAFT PROCEDURE
> Every configuration change needs team approval and a saved `diff all` before it is made ([[ESP_to_FC]] §10.5). Check each CLI line with `help` / `get` on your version.

---

## 1. Current state (4.5.1)

| Item | Value | Source |
|---|---|---|
| Firmware | Betaflight 4.5.1 (17 Oct 2024), MSP API 1.46 | [[2.5Drone]] |
| Target | `ODDITYRCF7AIO` (STM32F722) | [[2.5Drone]] |
| UART2 | Serial RX (ELRS, CRSF) | [[ESP_to_FC]] §2 |
| UART3 | IRC Tramp VTX | [[ESP_to_FC]] §2 |
| UART4 | MSP 115200 for the ESP (set 2026-10-13, `diff_all_2026-10-13_v3`, PR #9). Nothing wired yet | [[ADR-011 FC UART Allocation\|ADR-011]] |
| UART1 / UART6 | unassigned | [[ESP_to_FC]] §2 |
| Baro | DPS310, detected (`status` 2026-10-13/14) | [[2.5Drone]] |

## 2. Baseline on 4.5.1 (Module 1 week 1)

1. `version`, `status`, `diff all`. Commit them to the software repo under `drone/config/betaflight/4.5.1/`.
2. Hover test + blackbox log (16 MB onboard flash).

## 3. Upgrade to 2026.6.2 (week 2, [[ADR-014 Betaflight Version|ADR-014]])

1. Get instructor permission. Save `diff all` and screenshots of every tab.
2. Configurator → Firmware Flasher → board `ODDITYRCF7AIO`, 2026.6.2, cloud build. Include CRSF and Tramp. Try ALTHOLD only if the baro is detected. It may not fit 512 KB.
3. Full chip erase, then re-enter the settings **by hand** using the old diff as a checklist.
4. Calibrate the accelerometer, re-check motor order/direction (props off), failsafe, then hover.
5. `diff all` → `drone/config/betaflight/2026.6.2/`.

## 4. CLI for the ESP link (after approval)

```text
# UART4 = MSP for the D1 mini (serial id 3), function MSP (1), 115200
serial 3 1 115200 57600 0 115200
# MSP Override, stage 1 = yaw only (raw AETR bit 3)
set msp_override_channels_mask = 8
set msp_override_failsafe = OFF
save
```

UART2 (ELRS) and UART3 (Tramp) are **left untouched** (SAF-08). Set the modes in the **Modes tab**: ARM, ANGLE, **MSP OVERRIDE** (on its own 2-position switch), and ALTHOLD if built. Masks: 8 = yaw, 11 = roll+pitch+yaw, 15 = all four ([[ADR-010 MSP Override|ADR-010]]).

## 5. Failsafe defaults (verified in 4.5.1 source)

| Setting | Default | Meaning |
|---|---|---|
| `failsafe_delay` | 15 | 1.5 s Stage 1 before Stage 2 |
| `failsafe_procedure` | DROP | Disarm at Stage 2 |
| `failsafe_recovery_delay` | 5 | 0.5 s of valid data to recover |
| RX loss detection | ~100 ms, then the last values are held for 300 ms | then Stage 1 values (sticks centred, throttle low) |

Start with **DROP** in the cage (low altitude). These values are Betaflight's own RX failsafe (radio loss). The program's link-loss requirement is a different failsafe: on **ESP link loss** the drone holds position with neutral sticks and the pilot takes over with the radio ([[ADR-004 ESP Failsafe|ADR-004]], 2026-10-20). Re-evaluate the DROP choice after the upgrade.

## 6. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **API** | Application Programming Interface: a defined way for software components to communicate. |
| **Commit** | A recorded snapshot of changes in a version-control repository. |
| **CRSF** | Crossfire Serial Protocol, a serial protocol used by radio-control equipment. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **VTX** | Video transmitter: hardware that sends the aircraft video feed. |
| **FC** | Flight Controller: the onboard controller responsible for stabilization and control. |
| **ELRS** | ExpressLRS: the open-source radio-control link between the RadioMaster Pocket and the BETAFPV Nano receiver on the drone. |
| **CLI** | Command-line interface: here the Betaflight CLI in the Configurator, used for `diff all` and `status`. |
| **SAF-xx** | Safety requirement IDs SAF-01 to SAF-08 defined in ESP_to_FC section 8 (independent ELRS path, pilot takeover, no arming from code). |
