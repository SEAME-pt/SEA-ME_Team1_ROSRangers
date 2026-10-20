---
title: ADR-011 — FC UART Allocation
type: adr
status: accepted
date: 2026-10-12
project: SEA autonomous drone
aliases:
  - D11
  - D11 FC UART Allocation
tags:
  - adr
  - hardware
  - uart
---

# ADR-011 — FC UART Allocation

> [!success] Status: ACCEPTED (team, 2026-10-12). Continuity checks done 2026-10-16 (see the update below).
> Accepts **P3**, resolves **FC-02** and partly resolves **FC-01** in [[ESP_to_FC]].

> [!info] Update 2026-10-16: physical checks done (GitHub #16)
> - ELRS wires confirmed on **R2/T2** with a multimeter. UART2 certainty is now High.
> - **T4/R4**: continuity to the MCU side, no shorts to neighbouring pads. The continuity condition in §3 is met.
> - The UART decision does not change.
> - The ELRS red wire is on **5V**, not on DV5V (the receiver pad in the vendor wiring diagram). The team will move it to **DV5V** to free the 5V pad and to keep radio interference low. This changes the ELRS wiring, so it is an exception to "no change to the existing ELRS wiring" below and is reviewed on its own under SAF-08 (GitHub #41). UART2, R2/T2 and the receiver configuration stay the same.

## 1. Context (team inspection + Betaflight CLI)

| UART | Betaflight function today | Pads | Source / certainty |
|---|---|---|---|
| UART1 | none | **T1, R1 free** | [[2.5Drone]] #24, High |
| UART2 | Serial RX (CRSF) | R2/T2 under the ELRS wires (5V, G, R2, T2) | [[2.5Drone]] #22, Medium → **High** (multimeter, 2026-10-16) |
| UART3 | IRC Tramp VTX | T3/R3 near the VTX pads | CLI, [[ESP_to_FC]] §2 |
| UART4 | none | **T4, R4 exposed** | team pad list, 2026-10-12. Continuity OK, no shorts (2026-10-16) |
| UART6 | none | only on the digital-VTX connector (R6/T6) | [[2.5Drone]] |

The board target `ODDITYRCF7AIO` defines UART4 as PA0 (TX) / PA1 (RX). There is no UART5.

## 2. Decision

| Device | UART | Serial ID (CLI) |
|---|---|---|
| ELRS receiver | **UART2**, unchanged | 1 |
| Tramp VTX | **UART3**, unchanged | 2 |
| **D1 mini (MSP)** | **UART4 (T4/R4)** | 3 |
| Spare / fallback for the ESP | UART1 (T1/R1) | 0 |

## 3. Consequences

- No change to the existing ELRS or VTX wiring (SAF-08). Exception: the ELRS power wire moves from 5V to DV5V (2026-10-16 update, GitHub #41).
- Before soldering: continuity-check T4/R4 to the MCU side and check for shorts to neighbouring pads ([[ESP_to_FC]] §10.1). **Done 2026-10-16, passed** (GitHub #16).
- The ThoneFlow does not go on an FC UART: Betaflight 4.5.1 has no optical-flow driver ([[ADR-016 Flow Sensor Path|ADR-016]]).

## 4. Related pages

- [[Wiring and Pinout]] · [[Betaflight Setup]] · [[Decision Register]]

## 5. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **CRSF** | Crossfire Serial Protocol, a serial protocol used by radio-control equipment. |
| **FC** | Flight Controller: the onboard controller responsible for stabilization and control. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **VTX** | Video transmitter: hardware that sends the aircraft video feed. |
| **ELRS** | ExpressLRS: the open-source radio-control link between the RadioMaster Pocket and the BETAFPV Nano receiver on the drone. |
| **CLI** | Command-line interface: here the Betaflight CLI in the Configurator, used for `diff all` and `status`. |
| **MCU** | Microcontroller unit: the processor chip (STM32F722 on the FC, ESP8266 on the ESP module). |
| **SAF-xx** | Safety requirement IDs SAF-01 to SAF-08 defined in ESP_to_FC section 8 (independent ELRS path, pilot takeover, no arming from code). |
| **OD-xx / FC-xx / TF-xx** | Open-decision IDs in PC_to_ESP, ESP_to_FC and ThoneFlow. The Decision Register shows which ADR resolves each one. |
