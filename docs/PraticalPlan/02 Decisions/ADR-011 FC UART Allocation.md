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

> [!success] Status: ACCEPTED (team, 2026-10-12). Continuity checks still required.
> Accepts **P3**, resolves **FC-02** and partly resolves **FC-01** in [[ESP_to_FC]].

## 1. Context (team inspection + Betaflight CLI)

| UART | Betaflight function today | Pads | Source / certainty |
|---|---|---|---|
| UART1 | none | **T1, R1 free** | [[2.5Drone]] #24, High |
| UART2 | Serial RX (CRSF) | R2/T2 under the ELRS wires (5V, G, R2, T2) | [[2.5Drone]] #22, Medium |
| UART3 | IRC Tramp VTX | T3/R3 near the VTX pads | CLI, [[ESP_to_FC]] §2 |
| UART4 | none | **T4, R4 exposed** | team pad list, 2026-10-12 |
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

- No change to the existing ELRS or VTX wiring (SAF-08).
- Before soldering: continuity-check T4/R4 to the MCU side and check for shorts to neighbouring pads ([[ESP_to_FC]] §10.1).
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
