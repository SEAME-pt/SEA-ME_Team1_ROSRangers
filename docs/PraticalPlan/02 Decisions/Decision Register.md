---
title: Decision Register (ADR index)
type: adr-index
status: active
date: 2026-10-12
project: SEA autonomous drone
aliases:
  - ADR Register
  - Decisions
tags:
  - adr
  - decisions
---

# Decision Register (ADR index)

> [!info] How this register works
> Every important decision is an ADR, as defined in [[Dev_Strategy]] §19 (Status / Context / Decision / Consequences). To change a decision, add a new dated entry to the ADR or write a new ADR that supersedes it. Never edit silently.
> The **"Resolves"** column links each ADR to the open-decision IDs in the communication drafts: **OD-xx** in [[PC_to_ESP]], **FC-xx** in [[ESP_to_FC]], **TF-xx** in [[ThoneFlow]].

## 1. Register

| ADR | Decision | Status | Resolves |
|---|---|---|---|
| [[ADR-001 Message Categories\|ADR-001]] | Five message categories | ✅ Accepted | P1 (amended) |
| [[ADR-002 Message Integrity\|ADR-002]] | Truncated HMAC-SHA256 on every message | ✅ Accepted | OD-11, P2 |
| [[ADR-003 Sequence Acceptance\|ADR-003]] | 32-bit seq + random session ID, newer-only | ✅ Accepted | OD-08, OD-09, P3 |
| [[ADR-004 ESP Failsafe\|ADR-004]] | 250 ms link failsafe, explicit resume | ✅ Accepted (values to confirm) | OD-06/07 (initial), OD-12 (partly), OD-13, P4 |
| [[ADR-005 TCP Session\|ADR-005]] | Session over TCP; UDP decides liveness | ✅ Accepted | OD-14, P5 |
| [[ADR-006 Network\|ADR-006]] | ESP SoftAP, WPA2-PSK, static IPs | ✅ Accepted | OD-17, OD-04 (partly), P6 |
| [[ADR-007 Portable Protocol Library and Custom ESP Firmware\|ADR-007]] | Custom firmware + portable C++ lib + Python twin | ✅ Accepted | OD-01, OD-19, FC-07, P7 |
| [[ADR-008 Telemetry Transport\|ADR-008]] | State over UDP; events/logs/acks over TCP | ✅ Accepted | OD-15, supersedes P8 |
| [[ADR-009 ESP to FC Link\|ADR-009]] | UART, MSP, 115200, allow-list | ✅ Accepted | FC-03, P1 (FC page) |
| [[ADR-010 MSP Override\|ADR-010]] | MSP Override with staged masks | ✅ Accepted with conditions | FC-09, P2 (FC page) |
| [[ADR-011 FC UART Allocation\|ADR-011]] | ESP on UART4; ELRS stays on UART2 | ✅ Accepted | FC-02, FC-01 (partly), P3 (FC page) |
| [[ADR-012 ESP UART Pins\|ADR-012]] | UART0 GPIO1/3, never with USB attached | ✅ Accepted with conditions | FC-06, P4 (FC page) |
| [[ADR-013 Wiring and Power\|ADR-013]] | Crossover, 3.3 V logic, FC 5V → ESP 5V | ✅ Accepted (checks pending) | FC-04/05 (partly), P5 (FC page) |
| [[ADR-014 Betaflight Version\|ADR-014]] | **Yes**, upgrade to 2026.6.2 | ✅ Accepted (reflash permission granted 2026-10-14) | FC-11 (mitigation) |
| [[ADR-015 Gesture Input Source\|ADR-015]] | Ground webcam | ✅ Accepted | n/a |
| [[ADR-016 Flow Sensor Path\|ADR-016]] | Characterise → ESP SoftwareSerial experiment (no extra MCU) · height from the DPS310 baro | ✅ Accepted (revised 2026-10-14) | TF-01, TF-02, TF-11, TF-12 |
| [[ADR-017 Branching Model\|ADR-017]] | The team's `main` + `dev` model ([[Dev_Strategy]]) | ✅ Accepted | n/a |
| [[ADR-018 Ground OS and ROS 2\|ADR-018]] | Ubuntu 24.04 + ROS 2 Jazzy | ✅ Accepted | n/a |
| [[ADR-019 Sim Backends\|ADR-019]] | PX4 SITL + Betaflight SITL | ✅ Accepted | n/a |
| [[ADR-020 HIL Runner\|ADR-020]] | Dedicated Pi as self-hosted runner | ✅ Accepted | n/a |
| [[ADR-021 Removable ESP Connector\|ADR-021]] | Removable connector for the ESP | ✅ Accepted (team, 2026-10-20). Connector not fitted yet | n/a |

## 2. What changed on 2026-10-12

- The team approved all proposals ("go with the suggestions").
- **Superseded earlier recommendations** in the communication drafts:
  - OD-11: CRC for bench → **HMAC from day one**.
  - OD-14: TCP loss invalidates control → **UDP decides liveness**.
  - OD-17: ground-side access point → **ESP SoftAP**.
  - P8: telemetry fallback → **split by meaning**.
- ADR-016 was **revised** after reading [[ThoneFlow]]. SoftwareSerial is now an experiment, not the plan.
- ADR-017 now follows the team's own [[Dev_Strategy]] instead of GitHub Flow.

## 3. Still open

See [[Open Questions]] and the remaining **Open** rows in [[PC_to_ESP]] §12, [[ESP_to_FC]] §12.2 and [[ThoneFlow]] §10.2.

## 4. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **FC** | Flight Controller: on this project the OddityRC F7 AIO (STM32F722, Betaflight target `ODDITYRCF7AIO`) of the 2.5" drone. It stabilises the aircraft and takes MSP from the ESP on UART4. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **ROS 2** | The second-generation Robot Operating System middleware used for robotics integration. |
| **TCP** | Transmission Control Protocol: reliable, ordered, connection-oriented transport. |
| **Telemetry** | Periodic data sent from a system to report state, measurements, or health. |
| **UDP** | User Datagram Protocol: connectionless transport suited to refreshed, time-sensitive data. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **CRC** | Cyclic Redundancy Check: a checksum that detects accidental corruption (not tampering). |
| **ELRS** | ExpressLRS: the open-source radio-control link between the RadioMaster Pocket and the BETAFPV Nano receiver on the drone. |
| **HMAC** | Hash-based Message Authentication Code: a keyed tag that proves a message came from someone holding the key (ADR-002 uses a truncated HMAC-SHA256). |
| **SITL** | Software In The Loop: the flight code runs on the PC against a simulator. |
| **PX4** | An open-source flight-control stack, used here only in simulation (PX4 SITL + Gazebo). |
| **OD-xx / FC-xx / TF-xx** | Open-decision IDs in PC_to_ESP, ESP_to_FC and ThoneFlow. The Decision Register shows which ADR resolves each one. |
