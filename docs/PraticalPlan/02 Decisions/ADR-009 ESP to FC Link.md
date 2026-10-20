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

## 3. Related pages

- [[ESP_to_FC]] §4, §7 · [[MSP Cheat Sheet]] · [[Decision Register]]

## 4. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **FC** | Flight Controller: the onboard controller responsible for stabilization and control. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **OD-xx / FC-xx / TF-xx** | Open-decision IDs in PC_to_ESP, ESP_to_FC and ThoneFlow. The Decision Register shows which ADR resolves each one. |
