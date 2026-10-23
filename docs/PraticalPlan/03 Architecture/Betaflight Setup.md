---
title: Betaflight Setup
type: procedure
status: draft
date: 2026-10-12
updated: 2026-10-23
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
| UART4 | MSP 115200 for the ESP (set 2026-10-13, `diff_all_2026-10-13_v3`, PR #9). The ESP is soldered to UART4 since 2026-10-19 | [[ADR-011 FC UART Allocation\|ADR-011]] |
| UART1 / UART6 | unassigned | [[ESP_to_FC]] §2 |
| Baro | DPS310, detected (`status` 2026-10-13/14) | [[2.5Drone]] |

### 1.1 Modes (CLI `aux`, read 2026-10-21)

Identical to the 2026-10-13 baseline (`drone/config/betaflight/4.5.1/diff_all_2026-10-13_v2.txt`). The CLI prints each mode's permanent ID.

| Mode | Channel | Range (µs) | Radio switch |
|---|---|---|---|
| ARM | AUX1 (CH5) | 1700–2100 | **SA** (mapped 2026-10-21, [[Radio Link Tests]]) |
| ANGLE | AUX2 (CH6) | 1700–2100 | not mapped yet |
| HORIZON | AUX2 (CH6) | 1300–1700 | not mapped yet |
| BEEPER | AUX3 (CH7) | 1700–2100 | not mapped yet |
| USER1 | AUX5 (CH9) | 1300–1700 | not mapped yet |

> [!warning] Unmapped channels sit at mid-stick
> Channels with no switch assigned sit at about 1500 µs. That value is inside the HORIZON and USER1 ranges, so both show as active in `MSP_STATUS` until the switches are assigned. MSP OVERRIDE is not configured yet ([[ADR-010 MSP Override|ADR-010]]).

### 1.2 Motors (CLI read 2026-10-23)

| Setting | Value | Meaning |
|---|---|---|
| `mixer` | QUADX | M1 rear right, M2 front right, M3 rear left, M4 front left (seen from above) |
| `yaw_motors_reversed` | ON | Props out: seen from above, M1 and M4 turn counter-clockwise, M2 and M3 clockwise |
| `motor_pwm_protocol` | DSHOT600 | |
| `dshot_bidir` | ON | RPM telemetry from each ESC (used by the RPM filter) |
| `motor_poles` | 12 | **To confirm:** 1405 motors are often 14-pole. If they are, RPM readings are about 17 % high and the RPM filter is mistuned ([[Open Questions]]) |
| `dshot_idle_value` | 550 | 5.5 % motor speed while armed |

> [!warning] Motor direction lives in the ESC, not in `diff all`
> On 2026-10-23 M2 and M3 were reversed in the ESC with the Motors tab *Motor direction* tool, because all four motors turned the same way ([[Radio Link Tests]] §3.1). This setting is not part of the Betaflight `diff all`. If the ESC is replaced or re-flashed, check and set the directions again.

## 2. Baseline on 4.5.1 (Module 1 week 1)

1. `version`, `status`, `diff all`. Commit them to the software repo under `drone/config/betaflight/4.5.1/`.
2. Hover test + blackbox log (16 MB onboard flash).

## 3. Upgrade to 2026.6.2 (week 2, [[ADR-014 Betaflight Version|ADR-014]])

1. Instructor permission granted (2026-10-14, [[Open Questions]]). Save `diff all` and screenshots of every tab.
2. Configurator → Firmware Flasher → board `ODDITYRCF7AIO`, 2026.6.2, cloud build. Include CRSF and Tramp. Try ALTHOLD only if the baro is detected. It may not fit 512 KB.
3. Full chip erase, then re-enter the settings **by hand** using the old diff as a checklist.
4. Calibrate the accelerometer, re-check motor order/direction (props off), failsafe, then hover.
5. `diff all` → `drone/config/betaflight/2026.6.2/`.

## 4. CLI for the ESP link (after approval)

The `serial 3 1 ...` line is **already applied** (2026-10-13, `diff_all_2026-10-13_v3`, §1). The MSP Override lines still wait: props off, bench only on 4.5.1 ([[Safety Rules]] #14).

```text
# UART4 = MSP for the ESP (serial id 3), function MSP (1), 115200
serial 3 1 115200 57600 0 115200
# MSP Override, stage 1 = yaw only (raw AETR bit 3)
set msp_override_channels_mask = 8
set msp_override_failsafe = OFF
save
```

UART2 (ELRS) and UART3 (Tramp) are **left untouched** (REQ-SAF-008 (was SAF-08)). Set the modes in the **Modes tab**: ARM, ANGLE, **MSP OVERRIDE** (on its own 2-position switch), and ALTHOLD if built. Masks: 8 = yaw, 11 = roll+pitch+yaw, 15 = all four ([[ADR-010 MSP Override|ADR-010]]).

## 5. Failsafe defaults (verified in 4.5.1 source)

| Setting | Default | Meaning |
|---|---|---|
| `failsafe_delay` | 15 | 1.5 s Stage 1 before Stage 2 |
| `failsafe_procedure` | DROP | Disarm at Stage 2 |
| `failsafe_recovery_delay` | 5 | 0.5 s of valid data to recover |
| RX loss detection | ~100 ms, then the last values are held for 300 ms | then Stage 1 values (sticks centred, throttle low) |

Values read on the drone on 2026-10-22 (CLI `get failsafe`): `failsafe_procedure = DROP`, `failsafe_delay = 15`, `failsafe_recovery_delay = 5`, `failsafe_throttle = 1000`, `failsafe_throttle_low_delay = 100` (if the throttle has been low for 10 s, a radio loss disarms at once), `failsafe_switch_mode = STAGE1`, `msp_override_failsafe = OFF`. `rxfail`: roll, pitch, yaw and throttle `auto`, AUX channels `hold`. Bench result: [[Radio Link Tests]] RL-11.

Start with **DROP** in the cage (low altitude). These values are Betaflight's own RX failsafe (radio loss). The program's link-loss requirement is a different failsafe: on **ESP link loss** the ESP sends neutral sticks, so the drone levels in Angle mode (it can drift: no position hold), and the pilot takes over with the radio ([[ADR-004 ESP Failsafe|ADR-004]], 2026-10-20 and 2026-10-23). Re-evaluate the DROP choice after the upgrade.

## 6. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **API** | Application Programming Interface: a defined way for software components to communicate. |
| **Commit** | A recorded snapshot of changes in a version-control repository. |
| **CRSF** | Crossfire serial protocol: the serial wire format between the ELRS receiver and the FC (UART2), and between the radio and its ELRS module. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **VTX** | Video transmitter: hardware that sends the aircraft video feed. |
| **FC** | Flight Controller: on this project the OddityRC F7 AIO (STM32F722, Betaflight target `ODDITYRCF7AIO`) of the 2.5" drone. It stabilises the aircraft and takes MSP from the ESP on UART4. |
| **ELRS** | ExpressLRS: the open-source radio-control link between the RadioMaster Pocket and the BETAFPV Nano receiver on the drone. |
| **CLI** | Command-line interface: here the Betaflight CLI in the Configurator, used for `diff all` and `status`. |
| **REQ-SAF-001 to 008** | Safety requirements (old IDs SAF-01 to SAF-08) defined in ESP_to_FC §8 and registered in [[Requirements]]: independent ELRS path, pilot takeover, no arming from code. |
| **AUX1** | First auxiliary RC channel (CH5). On this drone it carries the ARM switch (SA). |
| **ESC** | Electronic Speed Controller: drives one motor. The FC's 4-in-1 ESC also stores each motor's spin direction. |
| **RPM** | Motor revolutions per minute, reported by each ESC over bidirectional DShot. |
| **Props out** | Motor layout where the front propellers sweep outwards at the front (`yaw_motors_reversed = ON` in Betaflight). |
| **AETR** | Raw receiver channel order: roll (A), pitch (E), throttle (T), yaw (R), then AUX. |
| **ALTHOLD** | Betaflight altitude-hold mode that uses the barometer. Only in 2026.6.x builds, and not default on 512 KB F7 targets (ADR-014). |
| **Angle mode** | Betaflight mode where the sticks command a tilt angle; centred sticks level the drone. It does not hold position: the drone can drift. |
| **Betaflight** | The open-source FC firmware on the drone: 4.5.1 installed, 2026.6.2 planned (ADR-014). |
| **diff all** | Betaflight CLI command that prints every setting changed from default. Baselines are in `drone/config/betaflight/`. |
| **Kill switch** | SA on the RadioMaster, which is also the ARM switch: releasing it disarms and stops the motors ([[Radio Link Tests]] RL-12). |
| **Mask** | `msp_override_channels_mask`: which stick channels the ground controls (8 = yaw, 11 = roll+pitch+yaw, 15 = all four). |
| **MSP Override** | Betaflight feature that lets MSP supply the values of selected RC channels, while ELRS stays the primary receiver (ADR-010). |
| **Takeover switch** | The MSP Override switch on the radio: turning it off ends ground control, and from then on the drone takes commands only from the radio (ADR-010). Its channel is not mapped yet. |
