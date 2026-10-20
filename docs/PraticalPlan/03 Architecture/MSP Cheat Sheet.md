---
title: MSP Cheat Sheet
type: reference
status: active
date: 2026-10-12
project: SEA autonomous drone
tags:
  - msp
  - betaflight
  - reference
---

# MSP Cheat Sheet

> [!info] Sources
> Command IDs come from Betaflight's `msp_protocol.h`. The byte vectors were sent to a running **Betaflight 2026.6.2 SITL** on 2026-10-12, and the replies below are what it answered. Our FC runs **4.5.1 (MSP API 1.46)** ([[2.5Drone]]).

---

## 1. Framing

- **v1:** `$M<` · size u8 · cmd u8 · payload · checksum = XOR(size, cmd, payload). Replies use `$M>`, errors `$M!`.
- **v2:** `$X<` · flag u8 · cmd u16 LE · size u16 LE · payload · CRC8-DVB-S2 (poly 0xD5) over flag..payload.

## 2. Golden vectors (verified against Betaflight 2026.6.2 SITL)

| Request | Bytes | SITL reply |
|---|---|---|
| v1 `MSP_API_VERSION` (1) | `24 4d 3c 00 01 01` | payload `[0, 1, 48]` → API 1.48 (our 4.5.1 FC reports 1.46) |
| v2 `MSP_FC_VARIANT` (2) | `24 58 3c 00 02 00 00 00 8a` | `BTFL`, CRC valid |
| v1 `MSP_FC_VERSION` (3) | `24 4d 3c 00 03 03` | `26.6.2` |
| v1 `MSP_SET_RAW_RC` (200), channels 1100, 1200 … 1800 | `24 4d 3c 10 c8 4c 04 b0 04 14 05 78 05 dc 05 40 06 a4 06 08 07 7a` | accepted |

## 3. Command IDs

| ID | Name | Use |
|---|---|---|
| 1 | MSP_API_VERSION | link check |
| 2 / 3 / 4 | FC_VARIANT / FC_VERSION / BOARD_INFO | identification |
| 101 | MSP_STATUS | flight-mode flags (is MSP OVERRIDE active?) |
| 102 | MSP_RAW_IMU | acc/gyro |
| 105 | MSP_RC | read back channel values (central to [[ESP_to_FC]] §11) |
| 108 | MSP_ATTITUDE | roll/pitch (0.1°), heading (°) |
| 109 | MSP_ALTITUDE | baro altitude (if the baro is detected) |
| 110 | MSP_ANALOG | battery voltage |
| 150 | MSP_STATUS_EX | extended status |
| 200 | MSP_SET_RAW_RC | setpoints (AETR order) |

> [!danger] Never forwarded from the network (gateway allow-list)
> `MSP_SET_MOTOR` (214, spins motors when disarmed), `MSP_EEPROM_WRITE` (250), `MSP_REBOOT` (68), and every other `MSP_SET_*` except 200. The week-2 transparent-bridge spike forwards all of these: use it only with props off on the isolated network.

## 4. Channel-order trap

`MSP_SET_RAW_RC` and `msp_override_channels_mask` use the **raw receiver order** (default `AETR1234`: roll, pitch, **throttle**, **yaw**). Verify on the bench by sending distinct values and watching the Receiver tab / `MSP_RC` ([[ESP_to_FC]] §11 Phase 3).

## 5. Glossary

| Term | Meaning |
| --- | --- |
| **API** | Application Programming Interface: a defined way for software components to communicate. |
| **FC** | Flight Controller: on this project the OddityRC F7 AIO (STM32F722, Betaflight target `ODDITYRCF7AIO`) of the 2.5" drone. It stabilises the aircraft and takes MSP from the ESP on UART4. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **CRC** | Cyclic Redundancy Check: a checksum that detects accidental corruption (not tampering). |
| **SITL** | Software In The Loop: the flight code runs on the PC against a simulator. |
