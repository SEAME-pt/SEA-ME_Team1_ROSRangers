---
title: System Overview
type: architecture
status: draft
date: 2026-10-12
project: SEA autonomous drone
tags:
  - architecture
---

# System Overview

> [!warning] Status: DRAFT
> This is the architecture we are building towards. Nothing in the drone-side chain has been wired or tested yet.

Follows the Module 1 reference layering: mission → ROS 2 → hardware abstraction → real-time layer → sensors/actuators.

---

## 1. Architecture

```mermaid
flowchart LR
  subgraph Room["Coding room (ground)"]
    CAM[Webcam] --> GEST["gesture recogniser<br/>MediaPipe, own venv"]
    GEST --> GN[gesture_node]
    GN -->|/gesture/intent| MISSION[mission / controller]
    EST["estimator<br/>flow + IMU + height"] --> MISSION
    MISSION -->|setpoints| FI["flight_interface<br/>lifecycle node"]
    FI -->|state| EST
  end
  subgraph Air["Drone (cage)"]
    ESP["D1 mini ESP8266<br/>custom firmware: gateway + failsafe"]
    FC["OddityRC F7 AIO<br/>Betaflight: attitude/rate loops"]
    FLOW["ThoneFlow-3901U<br/>(not installed)"]
    ELRS["ELRS BETAFPV Nano"]
    VTX["VT5804 VTX"]
  end
  FI <-->|"UDP control/telemetry<br/>TCP session/events<br/>WPA2 SoftAP + HMAC"| ESP
  ESP <-->|"MSP · UART4 · 115200"| FC
  FLOW -.->|"CX-OF 19200<br/>see ADR-016"| ESP
  PILOT["Safety pilot<br/>RadioMaster Pocket"] -->|"ELRS 2.4 GHz<br/>ARM, modes, override switch"| ELRS
  ELRS -->|"CRSF · UART2"| FC
  FC -->|"Tramp · UART3"| VTX
  VTX -.->|"5.8 GHz analog"| GRAB["MS210x USB grabber<br/>(Module 2 perception)"]
  SIM["PX4 SITL + Gazebo"] <-.same interface.-> FI
```

## 2. Authority order

Safety pilot (ELRS) > Betaflight failsafe > ESP link failsafe ([[ADR-004 ESP Failsafe|ADR-004]]) > `flight_interface` watchdog > mission/gestures.

## 3. Rates (initial budget, to be measured)

| Link | Rate | Source |
|---|---|---|
| Ground → ESP control | 50 Hz | OD-05 method: confirm by measurement |
| ESP → ground telemetry | 25 Hz | [[ADR-008 Telemetry Transport\|ADR-008]] |
| ESP → FC `MSP_SET_RAW_RC` | 50 Hz, 8 channels, 22 bytes | [[ADR-009 ESP to FC Link\|ADR-009]] |
| ESP polls `MSP_ATTITUDE` | 25 Hz; `MSP_ANALOG`/`MSP_STATUS` 5 Hz | [[ADR-009 ESP to FC Link\|ADR-009]] |
| ThoneFlow | 66 Hz (datasheet) or 25 Hz (ArduPilot), to measure | [[ThoneFlow]] |
| Webcam + MediaPipe | ≥ 20 FPS | [[01 Gesture Recognition with OpenCV and MediaPipe|Gesture Recognition Program]] |

At 115200 baud (about 11.5 kB/s), the MSP traffic above uses under 30% of the UART.

## 4. Latency budget (gesture → motors)

camera frame → inference → intent filter → controller → UDP → ESP validation → UART → Betaflight RC smoothing.
Telemetry echoes the **last applied control seq and its send timestamp**, so the ground can measure the round trip ([[Protocol Spec v0]]).

## 5. Related pages

- [[PC_to_ESP]] · [[ESP_to_FC]] · [[ThoneFlow]] · [[Decision Register]]

## 6. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **CRSF** | Crossfire Serial Protocol, a serial protocol used by radio-control equipment. |
| **ESP8266** | A WiFi-enabled microcontroller used as the drone communication module. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **FC** | Flight Controller: the onboard controller responsible for stabilization and control. |
| **MediaPipe** | A framework used here for detecting and interpreting hand landmarks and gestures. |
| **Mermaid** | A text-based diagram syntax rendered by compatible Markdown tools. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **ROS 2** | The second-generation Robot Operating System middleware used for robotics integration. |
| **TCP** | Transmission Control Protocol: reliable, ordered, connection-oriented transport. |
| **Telemetry** | Periodic data sent from a system to report state, measurements, or health. |
| **UDP** | User Datagram Protocol: connectionless transport suited to refreshed, time-sensitive data. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **VTX** | Video transmitter: hardware that sends the aircraft video feed. |
