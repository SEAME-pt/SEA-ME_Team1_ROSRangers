---
title: Hardware Inventory
type: hardware
status: active
date: 2026-10-12
updated: 2026-10-23
project: SEA autonomous drone
tags:
  - hardware
---

# Hardware Inventory

> [!danger] Hardware work follows [[Safety Rules]]: props off on the bench, the radio and the safety pilot win over code, no LiPo on the HIL bench.

> [!info] Summary of the team's hardware notes
> Details and photos are in [[2.5Drone]]. This table only collects what the plan depends on.

---

## 1. Drone

| Item | Details | Source |
|---|---|---|
| Drone | Pilotix Brook 2.5" Cinewhoop 4S Analog PNP (SKU 5297) | [[2.5Drone]] |
| FC + ESC | OddityRC F7 40A AIO, target `ODDITYRCF7AIO` (STM32F722, ICM-42688-P gyro, 16 MB blackbox flash, AT7456E OSD) | [[2.5Drone]], Betaflight config repo |
| Barometer | **DPS310, detected** (`status` 2026-10-13/14: `I2C:1`, `BARO=DPS310`, 0 I2C errors) | [[2.5Drone]] |
| Firmware | Betaflight 4.5.1, MSP API 1.46 → upgrade to 2026.6.2 ([[ADR-014 Betaflight Version\|ADR-014]]) | [[2.5Drone]] |
| Motors / props | 1405-4800KV / HQ DT63-3 V2 | [[2.5Drone]] |
| Camera | Ratel 2 (analog) | [[2.5Drone]] |
| VTX | PandaRC VT5804 BAT LITE, up to 1.3 W, Tramp on UART3, MMCX antenna | [[2.5Drone]] |
| Radio | BETAFPV ELRS Nano 2.4 GHz on UART2 (antenna soldered, 5 V on DV5V; failsafe not re-tested, GitHub #22/#41) + RadioMaster Pocket. **Both on ELRS 4.1.0, CE_LBT, team binding phrase** since 2026-10-21 (receiver was 3.3.0 CE_LBT, Pocket 4.1.0 ISM2G4 with the "Zorro" target: no link). Pocket model `MODEL06`, CH5 = SA (ARM) | [[2.5Drone]], [[ADR-023 ELRS Firmware Version and Regulatory Domain\|ADR-023]], [[Radio Link Tests]] |
| Wi-Fi bridge | **ESP-12F** module (ESP8266), **soldered to FC UART4 with removable wires** since 2026-10-19 ([[ADR-021 Removable ESP Connector\|ADR-021]]: connector to follow), currently **AT firmware 1.7.4.0** → custom firmware ([[ADR-007 Portable Protocol Library and Custom ESP Firmware\|ADR-007]]) | [[ESP_to_FC]] |
| Flow sensor | ThoneFlow-3901U, **not installed**, CX-OF-like protocol, no height | [[ThoneFlow]] |
| Battery | CNHL 4S 850 mAh. **850 mAh is the maximum capacity for this drone** (team, 2026-10-23, REQ-HW-001). The 450–650 mAh in the manual is for a different model ([[2.5Drone]]). XT30 ↔ XT60 adapter received, polarity OK. Checked 2026-10-16: no swelling, 3.90 V/cell ([[Battery Log]]). | team, [[2.5Drone]] |

## 2. Ground and lab

| Item | Details | Source |
|---|---|---|
| Video grabber | MacroSilicon MS210x USB (UVC), `534d:0021` | [[2.5Drone]] |
| Ground computer | Linux laptop (Ubuntu 24.04 + ROS 2 Jazzy, [[ADR-018 Ground OS and ROS 2\|ADR-018]]) + webcam for gestures | team |
| Raspberry Pi 5 | Ground-computer option (Hailo in the program) | [[2.5Drone]] |
| CI bench | A dedicated Pi 4 + one spare ESP, no spare FC (§3.1 of the HIL Runner note) | [[HIL Runner]] |
| Lab | Netted cage, Bambu P2S printer, LiPo station, simulation workstations | SEA:ME README |

## 3. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **API** | Application Programming Interface: a defined way for software components to communicate. |
| **CI** | Continuous Integration: automated validation of proposed changes. |
| **ESC** | Electronic Speed Controller: hardware that drives a motor. |
| **ESP8266** | The Wi-Fi microcontroller of the drone's ESP module (an ESP-12F). It bridges the ground PC to the FC over UART4. |
| **FC** | Flight Controller: on this project the OddityRC F7 AIO (STM32F722, Betaflight target `ODDITYRCF7AIO`) of the 2.5" drone. It stabilises the aircraft and takes MSP from the ESP on UART4. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **ROS 2** | The second-generation Robot Operating System middleware used for robotics integration. |
| **VTX** | Video transmitter: hardware that sends the aircraft video feed. |
| **ELRS** | ExpressLRS: the open-source radio-control link between the RadioMaster Pocket and the BETAFPV Nano receiver on the drone. |
| **LiPo** | Lithium-polymer battery: the 4S flight pack. Handle it by the rules in Safety Rules. |
| **XT60** | The battery connector on the pack. An XT30 to XT60 adapter is used on the drone. |
| **mAh** | Milliamp-hour: battery capacity. |
| **Binding phrase** | Secret text set on both radio and receiver; both derive the same UID from it, so they link on every power-up. |
| **CE_LBT** | EU regulatory domain of ELRS 2.4 GHz: listens before it transmits (Listen Before Talk). |
| **ESP** | The ESP-12F module (ESP8266) on the drone: the Wi-Fi bridge between the ground PC and the FC, wired to FC UART4. |
