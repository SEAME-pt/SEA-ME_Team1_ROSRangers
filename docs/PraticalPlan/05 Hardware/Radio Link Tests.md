---
title: Radio Link Tests
type: test-plan
status: active
date: 2026-10-22
project: SEA autonomous drone
tags:
  - hardware
  - radio
  - elrs
  - safety
---

# Radio Link Tests

> [!info] Scope
> Bench checks of the safety pilot's radio link and of the motors it controls: RadioMaster Pocket → BETAFPV ELRS Nano receiver → FC (UART2, CRSF) → ESC → motors. Tracked in GitHub #22. The firmware update that made the link work is recorded in [[ADR-023 ELRS Firmware Version and Regulatory Domain|ADR-023]]. All tests are props off ([[Safety Rules]] #1).

---

## 1. Configuration under test

| Item | Value | Source |
|---|---|---|
| Radio | RadioMaster Pocket, EdgeTX, model `MODEL06`, Internal RF = CRSF, 250 Hz | radio menus, 2026-10-21 |
| Radio ELRS module | ELRS 4.1.0, CE_LBT, target "RadioMaster Pocket Internal 2.4GHz TX", team binding phrase | [[ADR-023 ELRS Firmware Version and Regulatory Domain\|ADR-023]] |
| Receiver | BETAFPV ELRS Nano RX, ELRS 4.1.0, CE_LBT, same binding phrase | [[ADR-023 ELRS Firmware Version and Regulatory Domain\|ADR-023]] |
| Radio mixes | CH5 (AUX1) = switch **SA** | radio, 2026-10-21 |
| FC | Betaflight 4.5.1, UART2 Serial RX, `serialrx_provider = CRSF` | CLI read, 2026-10-21 |
| FC modes | as in [[Betaflight Setup]] §1.1 (ARM on AUX1 1700–2100) | CLI read, 2026-10-21 |

## 2. Results (2026-10-21)

| ID | Test | Result | Evidence |
|---|---|---|---|
| RL-01 | Radio and receiver link after the update | **PASS.** Receiver LED solid with the Pocket on | bench observation |
| RL-02 | Link comes back after a receiver power cycle, with no new bind | **PASS.** Re-linked after drone power cycles, with no new bind | bench observation (team, 2026-10-21) |
| RL-03 | Sticks reach the FC | **PASS (throttle).** Throttle read back from the FC moved from 989 to 1843 µs. Roll and yaw moved only slightly in this session (not swept) | `MSP_RC` read through the ESP bridge, local CSV `2026-10-21_15-56-27_manual.csv` |
| RL-04 | Link quality seen by the FC | **PASS.** RSSI 82–85 %, no RX loss (`RXLOSS` flag clear) | `MSP_ANALOG` / `MSP_STATUS`, same session |
| RL-05 | Arming blockers with sticks at rest | **PASS.** No blocker with throttle low; `THROTTLE` only while the throttle was raised | `MSP_STATUS` arming-disable flags, same session |
| RL-06 | ARM switch reaches the FC | **PASS.** AUX1 moves from about 1000 to about 2000 µs with SA after mapping CH5 = SA | `MSP_RC`, 2026-10-21 |
| RL-07 | FC modes unchanged | **PASS.** `aux` lines identical to the 2026-10-13 baseline (`drone/config/betaflight/4.5.1/diff_all_2026-10-13_v2.txt`) | CLI `aux`, 2026-10-21 |

> [!note] Telemetry source
> RL-03 to RL-06 were read through the ESP transparent bridge (props off, [[Safety Rules]] #13). The raw CSV files are kept locally, not in Git (`*.csv` is ignored).

## 3. Results (2026-10-22 and 2026-10-23)

Battery B1 checked before the session ([[Battery Log]]). Props off. Battery and USB together only for the Motors tab ([[Safety Rules]] #16).

| ID | Test | Result | Evidence |
|---|---|---|---|
| RL-14 | All sticks and switches swept (Receiver tab, USB only) | **PASS.** Each stick lands on its channel in the right direction; SA moves AUX1 | Betaflight Receiver tab, 2026-10-22 |
| RL-08 | SA polarity (Modes tab, USB only) | **PASS.** SA released = AUX1 1000 = disarmed; SA pressed = AUX1 2000, inside the ARM range 1700–2100 | Betaflight Modes tab, 2026-10-22 |
| RL-10 | Arm with SA (battery, props off, throttle low) | **PASS.** Motors spin at idle when armed (normal: Betaflight never stops the motors while armed); the throttle raises motor speed; releasing SA disarms and stops them | bench, 2026-10-22 |
| RL-11 | **Failsafe: radio off while armed (props off)** | **PASS.** The motors stopped immediately. The throttle had been low for more than 10 s, so Betaflight disarms at once (`failsafe_throttle_low_delay`, [[Betaflight Setup]] §5) | video attached to GitHub #22 by BrunoBMFT (2026-10-23) |
| RL-12 | Kill switch | **PASS.** Releasing SA disarms and stops the motors. The team confirmed SA as the ARM and kill switch | #22 |
| RL-13 | Motor order and direction (Motors tab, 1015, tape on each bell) | **Order PASS** (M1 rear right, M2 front right, M3 rear left, M4 front left). **Direction FAIL, then fixed:** all four motors spun the same way. M2 and M3 were reversed in the ESC on 2026-10-23 (§3.1). **PASS** after a full power cycle | bench, 2026-10-23 |

### 3.1 Motor direction fault and fix (2026-10-23)

- **Found:** with the drone upside down, all four motors turned clockwise (seen from below). On a quad, diagonal pairs must turn in opposite directions; with all four the same way the drone yaws out of control at take-off.
- **Expected** for `yaw_motors_reversed = ON` (props out), seen **from above**: M1 and M4 counter-clockwise, M2 and M3 clockwise ([[Betaflight Setup]] §1.2). Seen from below, every direction is mirrored.
- **Fix:** Betaflight Motors tab → *Motor direction* → *Individual* → motor 2 → *Reverse*, then motor 3 → *Reverse*. M1 and M4 were already right. The FC configuration was not changed: the direction is stored **in the ESC**.
- **Check:** after removing battery and USB and powering up again, M1/M4 and M2/M3 turned in opposite directions as expected, so the setting persisted.
- **Props:** fit counter-clockwise props on M1 and M4 and clockwise props on M2 and M3 (seen from above), so the front props sweep outwards at the front. Check the marking on each prop before the first hover (#23).

## 4. Still to do

| ID | Test | Pass criterion | Link |
|---|---|---|---|
| RL-15 | Radio telemetry on the Pocket | TELEMETRY → Discover new shows RQly, 1RSS and the receiver battery; radio alarms enabled (the radio boots with "alarms disabled") | radio setup |
| RL-16 | Range in the lab | Link quality recorded at the safety pilot's position and across the cage | [[ESP Bench Test Plan]] |
| RL-17 | Wi-Fi ↔ ELRS coexistence | ELRS link quality recorded with the ESP streaming, Wi-Fi channels 1, 6, 11 | ESP-S2-05 in [[ESP Bench Test Plan]] |

## 5. Related pages

- [[ADR-023 ELRS Firmware Version and Regulatory Domain|ADR-023]] · [[Betaflight Setup]] · [[Hardware Inventory]] · [[ESP Bench Test Plan]] · [[Safety Rules]]

## 6. Glossary

| Term | Meaning |
| --- | --- |
| **AUX1** | First auxiliary RC channel (CH5). On this drone it carries the ARM switch (SA). |
| **CRSF** | Crossfire serial protocol: the wire format between the EdgeTX radio and its ELRS module, and between the receiver and the FC. |
| **ELRS** | ExpressLRS: the open-source radio-control link between the RadioMaster Pocket and the BETAFPV Nano receiver on the drone. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **FC** | Flight Controller: on this project the OddityRC F7 AIO (STM32F722, Betaflight target `ODDITYRCF7AIO`) of the 2.5" drone. It stabilises the aircraft and takes MSP from the ESP on UART4. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **RSSI** | Received signal strength, as reported by the FC from the receiver. |
| **RQly / 1RSS** | ELRS telemetry sensors on the radio: link quality (%) and received signal strength (dBm). |
| **CE_LBT** | EU regulatory domain of ELRS 2.4 GHz: listens before it transmits (Listen Before Talk). |
| **ESC** | Electronic Speed Controller: drives one motor. The FC's 4-in-1 ESC also stores each motor's spin direction. |
| **Props out** | Motor layout where the front propellers sweep outwards at the front (`yaw_motors_reversed = ON` in Betaflight). |
