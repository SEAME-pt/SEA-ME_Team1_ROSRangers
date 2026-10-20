---
title: ADR-021 — Removable ESP Connector
type: adr
status: proposed
date: 2026-10-12
project: SEA autonomous drone
tags:
  - adr
  - hardware
  - esp8266
---

# ADR-021 — Removable ESP Connector

> [!question] Status: PROPOSED, pending team approval
> Amends the mounting part of [[ADR-013 Wiring and Power|ADR-013]].

## 1. Context

- On the D1 mini, USB flashing and the FC link share UART0, so they can never be connected together ([[ADR-012 ESP UART Pins|ADR-012]]).
- Desoldering a D1 mini from a 2.5" AIO risks lifting FC pads. Spares are limited.

## 2. Options considered

| Option | Pros | Cons |
|---|---|---|
| Solder the D1 mini wires straight to the FC | Lightest, no extra contact | Hard to remove for USB flashing or replacement; repeated soldering on the FC |
| **4-pin connector** (5V, GND, TX, RX) soldered to the FC pads, plug on the D1 mini | Unplug for USB flashing (removes the UART0/USB conflict by construction), swap a dead D1 mini in minutes, solder the FC only once | 1–2 g extra; one more contact that can loosen (vibration) |

## 3. Decision (proposed)

Use a small locking 4-pin connector (e.g. JST-SH or JST-GH), with the pin order recorded in [[Wiring and Pinout]]. Secure the D1 mini mechanically (tape or a 3D-printed holder) so the connector carries no load.

## 4. Consequences

- The connector goes on the pre-flight checklist ([[Safety Rules]]).
- A loose connector looks like the "UART disconnection" failure (F2 in [[ESP_to_FC]]). It is already covered by the readback and failsafe tests ([[ESP Bench Test Plan]]).

## 5. Related pages

- [[ESP Bench Test Plan]] · [[Decision Register]]

## 6. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **ESP8266** | A WiFi-enabled microcontroller used as the drone communication module. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **FC** | Flight Controller: the onboard controller responsible for stabilization and control. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **USB** | Universal Serial Bus: used to flash the ESP and to power or configure the FC from a PC. |
