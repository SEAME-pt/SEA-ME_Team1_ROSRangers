---
title: Hardware Inventory
type: hardware
status: active
date: 2026-10-12
project: SEA autonomous drone
tags:
  - hardware
---

# Hardware Inventory

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
| Radio | BETAFPV ELRS Nano 2.4 GHz on UART2 + RadioMaster Pocket | [[2.5Drone]] |
| Wi-Fi bridge | **Wemos D1 mini** (ESP8266), currently **AT firmware 1.7.4.0** → custom firmware ([[ADR-007 Portable Protocol Library and Custom ESP Firmware\|ADR-007]]) | [[ESP_to_FC]] |
| Flow sensor | ThoneFlow-3901U, **not installed**, CX-OF-like protocol, no height | [[ThoneFlow]] |
| Battery | CNHL 4S 850 mAh (the manual suggests 450–650 mAh). Waiting for the XT30 ↔ XT60 adapter. | team, [[2.5Drone]] |

## 2. Ground and lab

| Item | Details | Source |
|---|---|---|
| Video grabber | MacroSilicon MS210x USB (UVC), `534d:0021` | [[2.5Drone]] |
| Ground computer | Linux laptop (Ubuntu 24.04 + ROS 2 Jazzy, [[ADR-018 Ground OS and ROS 2\|ADR-018]]) + webcam for gestures | team |
| Raspberry Pi 5 | Ground-computer option (Hailo in the program) | [[2.5Drone]] |
| CI bench | A dedicated Pi + spare D1 mini (+ FC) | [[HIL Runner]] |
| Lab | Netted cage, Bambu P2S printer, LiPo station, simulation workstations | SEA:ME README |

## 3. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **API** | Application Programming Interface: a defined way for software components to communicate. |
| **CI** | Continuous Integration: automated validation of proposed changes. |
| **ESC** | Electronic Speed Controller: hardware that drives a motor. |
| **ESP8266** | A WiFi-enabled microcontroller used as the drone communication module. |
| **FC** | Flight Controller: the onboard controller responsible for stabilization and control. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **ROS 2** | The second-generation Robot Operating System middleware used for robotics integration. |
| **VTX** | Video transmitter: hardware that sends the aircraft video feed. |
