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
> Bench checks of the safety pilot's radio link: RadioMaster Pocket → BETAFPV ELRS Nano receiver → FC (UART2, CRSF). Tracked in GitHub #22. The firmware update that made the link work is recorded in [[ADR-023 ELRS Firmware Version and Regulatory Domain|ADR-023]]. All tests are props off ([[Safety Rules]] #1).

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

## 3. Still to do before the radio link counts as fully working

| ID | Test | Pass criterion | Link |
|---|---|---|---|
| RL-10 | Arm with SA (battery, props off, throttle low) | Motors spin at idle; throttle stick raises motor speed; SA off disarms and stops the motors | #22, REQ-SAF-003 |
| RL-11 | **Failsafe: radio off while armed (props off)** | Motors stop (Stage 2 `DROP`, [[Betaflight Setup]] §5); video saved | #22 exit criterion, REQ-SAF-006 |
| RL-12 | Kill switch | The team decides whether SA alone is the kill switch or a separate one is added; then tested like RL-10 | #22 |
| RL-13 | Motor order and direction | Betaflight Motors tab, props off: order and direction match the frame | #22 |
| RL-14 | All sticks and switches swept | Each stick and switch lands on the expected channel and direction in the Receiver tab | REQ-SAF-001 |
| RL-15 | Radio telemetry on the Pocket | TELEMETRY → Discover new shows RQly, 1RSS and the receiver battery; radio alarms enabled (the radio boots with "alarms disabled") | radio setup |
| RL-16 | Range in the lab | Link quality recorded at the safety pilot's position and across the cage | [[ESP Bench Test Plan]] |
| RL-17 | Wi-Fi ↔ ELRS coexistence | ELRS link quality recorded with the ESP streaming, Wi-Fi channels 1, 6, 11 | ESP-S2-05 in [[ESP Bench Test Plan]] |

## 4. Related pages

- [[ADR-023 ELRS Firmware Version and Regulatory Domain|ADR-023]] · [[Betaflight Setup]] · [[Hardware Inventory]] · [[ESP Bench Test Plan]] · [[Safety Rules]]

## 5. Glossary

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
