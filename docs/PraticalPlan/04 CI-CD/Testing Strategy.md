---
title: Testing Strategy
type: test-plan
status: draft
date: 2026-10-12
project: SEA autonomous drone
aliases:
  - Communication Test Plan
tags:
  - testing
  - ci
---

# Testing Strategy

> [!info] Builds on [[Dev_Strategy]] §12–§13 (test levels and pyramid)
> It also covers the test requirements in [[PC_to_ESP]] §9, [[ESP_to_FC]] §11 and [[ThoneFlow]] §9.

---

## 1. Test levels

| Level | What | Where | Example tests |
|---|---|---|---|
| 1 Unit (C++) | framing, MAC, sequence window, failsafe timers, AETR mapping, MSP encode/decode, ThoneFlow parser | GitHub runner, GoogleTest | stale seq rejected; 251 ms without control → failsafe; tag flip → reject; false `0xFE` header resync |
| 1 Unit (Python) | same spec in `sd_protocol_py`, controller, gesture filter | GitHub runner, pytest | |
| 2 Cross-language | golden vectors (`drone/tests/vectors/*.json`) | both | C++ encoder and Python decoder agree byte for byte |
| 2 MSP integration | our MSP code ↔ **the real Betaflight parser** | Betaflight 2026.6.2 SITL, TCP 5761 | API handshake, v2 CRC, malformed frames ignored |
| 2 Impaired link | loss, delay, duplication, reordering, corruption between a simulated sender and receiver | GitHub runner | [[PC_to_ESP]] §9.1 |
| 3 Simulation | controller + flight interface | PX4 SITL + Gazebo | square path, link-loss injection |
| 4 HIL | real D1 mini + FC, Wi-Fi, no battery | Pi runner ([[HIL Runner]]) | passthrough, failsafe timing, latency |
| 4 Bench (manual) | D1 mini alone, then with the FC on temporary wires | lab | [[ESP Bench Test Plan]] Stages 1–2 |
| 4 Bench (manual) | props off, battery on | lab | [[ESP_to_FC]] §11 Phases 1–6 |
| 5 Flight | cage, safety pilot | lab | staged masks ([[ADR-010 MSP Override\|ADR-010]]) |

## 2. Fault-injection list (Module 1 E-stop demo)

Kill `flight_interface` · kill the ground app · Wi-Fi off · ESP reset · bad MAC · replayed packet · out-of-range values · UART unplugged · ELRS TX off. These map onto **F1–F9** in [[ESP_to_FC]] §9.
Each one has an **expected state** and a **measured time-to-safe-state**.

## 3. Metrics to publish (the program wants numbers)

UDP RTT (median/p99/max), loss %, ESP loop jitter, ESP CPU cost of the MAC ([[ADR-002 Message Integrity|ADR-002]]), Betaflight task timings, gesture → setpoint latency, ThoneFlow frame-loss rate (threshold agreed **before** testing, TF-10), flow velocity error, coverage %.

## 4. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **API** | Application Programming Interface: a defined way for software components to communicate. |
| **CI** | Continuous Integration: automated validation of proposed changes. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **FC** | Flight Controller: on this project the OddityRC F7 AIO (STM32F722, Betaflight target `ODDITYRCF7AIO`) of the 2.5" drone. It stabilises the aircraft and takes MSP from the ESP on UART4. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **TCP** | Transmission Control Protocol: reliable, ordered, connection-oriented transport. |
| **UDP** | User Datagram Protocol: connectionless transport suited to refreshed, time-sensitive data. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **CRC** | Cyclic Redundancy Check: a checksum that detects accidental corruption (not tampering). |
| **ELRS** | ExpressLRS: the open-source radio-control link between the RadioMaster Pocket and the BETAFPV Nano receiver on the drone. |
| **SITL** | Software In The Loop: the flight code runs on the PC against a simulator. |
| **PX4** | An open-source flight-control stack, used here only in simulation (PX4 SITL + Gazebo). |
| **Gazebo** | The 3D physics simulator used with PX4 (Harmonic release). |
| **RTT** | Round-trip time: how long a packet takes to go out and come back. |
| **OD-xx / FC-xx / TF-xx** | Open-decision IDs in PC_to_ESP, ESP_to_FC and ThoneFlow. The Decision Register shows which ADR resolves each one. |
