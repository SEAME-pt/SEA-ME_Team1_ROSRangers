---
title: ADR-009 — ESP ↔ FC Physical Link and Protocol
type: adr
status: accepted
date: 2026-10-12
project: SEA autonomous drone
aliases:
  - D09
tags:
  - adr
  - msp
  - uart
---

# ADR-009 — ESP ↔ FC Physical Link and Protocol

> [!success] Status: ACCEPTED (team, 2026-10-12). Bench validation still required ([[ESP_to_FC]] §11).
> Accepts **P1** and resolves **FC-03** in [[ESP_to_FC]].

## 1. Decision

- **UART**, MSP v1 framing (v2 for any command ID above 255), **115200 baud** 8N1. This is Betaflight's default MSP baud. Change it only on measured need.
- The ESP polls the FC (`MSP_ATTITUDE` 25 Hz, `MSP_ANALOG` / `MSP_STATUS` 5 Hz) and sends `MSP_SET_RAW_RC` at 50 Hz. That is well under 30% of the UART capacity ([[System Overview]]).
- The gateway **allow-lists** MSP commands. It never forwards raw MSP from the network ([[MSP Cheat Sheet]]).

## 2. Consequences

- Answers TEL-05: one scheduler on the ESP owns the UART, so polling and override frames never collide.
- **2026-11-16 (#176, proposed, waiting for the team decision on #176):** the ESP also polls `MSP_BOXIDS` (119) at 1 Hz. It is read-only and ESP-originated, and it is never forwarded. It finds where MSP OVERRIDE (permanent box ID 50) sits in the `MSP_STATUS` mode flags, so `STATE.override_active` can be reported. Cost: a 6-byte request and a reply of about 33 bytes once a second, under 0.5 % of the UART. The `MSP_API_VERSION` poll (1 Hz, #102) was already added the same way. See [[MSP Cheat Sheet]] §3.

## 3. Related pages

- [[ESP_to_FC]] §4, §7 · [[MSP Cheat Sheet]] · [[Decision Register]]

## 4. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **FC** | Flight Controller: on this project the OddityRC F7 AIO (STM32F722, Betaflight target `ODDITYRCF7AIO`) of the 2.5" drone. It stabilises the aircraft and takes MSP from the ESP on UART4. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **OD-xx / FC-xx / TF-xx** | Open-decision IDs in PC_to_ESP, ESP_to_FC and ThoneFlow. The Decision Register shows which ADR resolves each one. |
