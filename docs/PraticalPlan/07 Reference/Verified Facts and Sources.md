---
title: Verified Facts and Sources
type: reference
status: active
date: 2026-10-12
updated: 2026-10-23
project: SEA autonomous drone
tags:
  - reference
---

# Verified Facts and Sources

> [!info] How these were checked
> Verified on 2026-10-12, either by reading the source (code, official docs) or by running code. Facts from the team's own inspection are marked **team**.

---

## 1. Program

| Fact | Source |
|---|---|
| Module dates, deliverables, required tools (GoogleTest, pytest, clang-tidy, cppcheck, coverage >80%, HIL) | `github.com/SEAME-pt/seame-2.0` READMEs |
| Start date 7 Oct 2026 | team |

## 2. Flight controller and Betaflight

| Fact | Source |
|---|---|
| Target `ODDITYRCF7AIO`: F722, ICM42688P, baro drivers DPS310/QMP6988, 16 MB flash, UART1/2/3/4/6, UART4 = PA0/PA1 | `betaflight/config` → `configs/ODTY/ODDITYRCF7AIO/config.h` |
| Pads (vendor diagram): `CAM` = camera video in, `VTX` = video out, `DV5V` = receiver 5 V, UART4 row `T4 5V SCL DV5V R2` / `R4 GND 3V3 SDA GND T2`. Target has no camera-control pin (`CAMERA_CONTROL_PIN` commented out). | [OddityRC F7 40A AIO wiring diagram](https://www.rotorama.com/cms/assets/docs/6d96909d797b5e734f781ca47138a567/35018-0/6x.jpg), `config.h` above |
| ELRS on R2/T2. T4/R4 continuity to the MCU side, no shorts to neighbours (2026-10-16) | **team**, multimeter (GitHub #16) |
| Installed 4.5.1, MSP API 1.46. UART2 Serial RX, UART3 Tramp. `status` `I2C:1`, `BARO=DPS310` (2026-10-13/14; earlier `I2C:0` superseded) | **team** ([[2.5Drone]], [[ESP_to_FC]]) |
| MSP Override is compiled into every 4.5.1 build. `msp_override_failsafe` defaults to OFF. | Betaflight 4.5.1 `target/common_pre.h`, `pg/rx.c` |
| 4.5.1 override has no freshness check (885 µs before the first frame; values held) | Betaflight 4.5.1 `rx/msp_override.c`, `rx/msp.c` |
| 300 ms freshness guard first in 2026.6.0-rc1; absent in 4.5.5 and 2025.12.5 | `rx/msp.c` at each tag |
| Failsafe defaults (1.5 s Stage 1, DROP, 0.5 s recovery, 100 ms detection, 300 ms hold) | Betaflight 4.5.1 `flight/failsafe.c`, `rx/rx.c` |
| **4.5.1 has no optical-flow driver**. 2026.6.2 has MT and UPT1 only. ALTHOLD/POS HOLD not default below 1 MB flash. | Betaflight source trees |
| 2026.6.2 SITL builds (~25 s) and answers MSP v1/v2 on TCP 5761. 4.5.1 SITL link error. | built and run |
| INAV supports CX-OF but has no OddityRC target | `iNavFlight/inav` source tree |

## 3. Sensors, ESP and software

| Fact | Source |
|---|---|
| ThoneFlow datasheet: 19200 baud, 66 Hz, 3.3–3.6 V, 9-byte frame, checksum at byte 6 | **team**, [[ThoneFlow]] §2 |
| ArduPilot lists the ThoneFlow under CX-OF (25 Hz, different layout); a rangefinder is needed for height | ArduPilot wiki `common-cheerson-cxof.rst`, `AP_OpticalFlow_CXOF.cpp` |
| ESP8266: `Serial.swap()` → GPIO15/13; `Serial1` TX-only; boot-strap pins; `WIFI_NONE_SLEEP`; signed OTA (RSA-2048 + SHA256); core 3.1.2 | `esp8266/Arduino` docs and tags |
| micro-ROS supports ESP32, not ESP8266 | micro_ros_arduino / micro_ros_platformio READMEs |
| MediaPipe 1.1.0: aarch64 wheels, Python 3.9–3.14, no `mp.solutions` | PyPI + wheel contents |

## 4. Not verified (treat with care)

- CLI command syntax in [[Betaflight Setup]]: confirm with `help` on your version.
- The GitHub Actions workflows run on every PR (see the Actions tab).

> 2026-10-23: the ROS 2 / PX4 / Ubuntu pairing is verified ([[PX4 SITL Setup]] §1, [[ADR-018 Ground OS and ROS 2|ADR-018]] update), and the SITL and HIL jobs now have their files, so both bullets were removed from this list.

## 5. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **API** | Application Programming Interface: a defined way for software components to communicate. |
| **ESP8266** | The Wi-Fi microcontroller of the drone's ESP module (an ESP-12F). It bridges the ground PC to the FC over UART4. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **FC** | Flight Controller: on this project the OddityRC F7 AIO (STM32F722, Betaflight target `ODDITYRCF7AIO`) of the 2.5" drone. It stabilises the aircraft and takes MSP from the ESP on UART4. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **MediaPipe** | A framework used here for detecting and interpreting hand landmarks and gestures. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **ROS 2** | The second-generation Robot Operating System middleware used for robotics integration. |
| **TCP** | Transmission Control Protocol: reliable, ordered, connection-oriented transport. |
| **ELRS** | ExpressLRS: the open-source radio-control link between the RadioMaster Pocket and the BETAFPV Nano receiver on the drone. |
| **OTA** | Over-the-air update: flashing firmware through Wi-Fi instead of USB. |
| **SITL** | Software In The Loop: the flight code runs on the PC against a simulator. |
| **PX4** | An open-source flight-control stack, used here only in simulation (PX4 SITL + Gazebo). |
| **GND** | Ground: the common 0 V reference wire. |
