---
title: ADR-021 — Removable ESP Connector
type: adr
status: accepted
date: 2026-10-12
project: SEA autonomous drone
tags:
  - adr
  - hardware
  - esp8266
---

# ADR-021 — Removable ESP Connector

> [!success] Status: ACCEPTED (team, 2026-10-20)
> Amends the mounting part of [[ADR-013 Wiring and Power|ADR-013]].

> [!info] Update 2026-10-20: accepted (GitHub #55)
> - The team accepted the proposal: use a removable connector, so we stop soldering and desoldering.
> - **Where the install stands:** since 2026-10-19 the ESP-12F (ESP8266) is soldered to FC UART4 with wires that are easy to remove, so the install is not permanent. The connector is **not fitted yet**; the connector type and pin order are still to be chosen and recorded in [[Wiring and Pinout]].
> - The ESP's USB stays disconnected while the FC link is wired ([[ADR-012 ESP UART Pins|ADR-012]]). Until the connector is fitted, this is a procedure, not something the hardware enforces.

## 1. Context

- On the ESP, USB flashing and the FC link share UART0, so they can never be connected together ([[ADR-012 ESP UART Pins|ADR-012]]).
- Desoldering an ESP from a 2.5" AIO risks lifting FC pads. Spares are limited.

## 2. Options considered

| Option | Pros | Cons |
|---|---|---|
| Solder the ESP wires straight to the FC | Lightest, no extra contact | Hard to remove for USB flashing or replacement; repeated soldering on the FC |
| **4-pin connector** (5V, GND, TX, RX) soldered to the FC pads, plug on the ESP | Unplug for USB flashing (removes the UART0/USB conflict by construction), swap a dead ESP in minutes, solder the FC only once | 1–2 g extra; one more contact that can loosen (vibration) |

## 3. Decision (accepted 2026-10-20)

Use a small locking 4-pin connector (e.g. JST-SH or JST-GH; the exact part is still to be chosen), with the pin order recorded in [[Wiring and Pinout]]. Secure the ESP mechanically (tape or a 3D-printed holder) so the connector carries no load.

## 4. Consequences

- Until the connector is fitted, the ESP stays on its soldered, removable wires. Re-flashing the ESP over USB means removing those wires first ([[ADR-012 ESP UART Pins|ADR-012]]).
- The connector goes on the pre-flight checklist ([[Safety Rules]]).
- A loose connector looks like the "UART disconnection" failure (F2 in [[ESP_to_FC]]). It is already covered by the readback and failsafe tests ([[ESP Bench Test Plan]]).

## 5. Related pages

- [[ESP Bench Test Plan]] · [[Decision Register]]

## 6. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **ESP8266** | The Wi-Fi microcontroller of the drone's ESP module (an ESP-12F). It bridges the ground PC to the FC over UART4. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **FC** | Flight Controller: on this project the OddityRC F7 AIO (STM32F722, Betaflight target `ODDITYRCF7AIO`) of the 2.5" drone. It stabilises the aircraft and takes MSP from the ESP on UART4. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **USB** | Universal Serial Bus: used to flash the ESP and to power or configure the FC from a PC. |
| **ESP** | The ESP-12F module (ESP8266) on the drone: the Wi-Fi bridge between the ground PC and the FC, wired to FC UART4. |
| **OTA** | Over-the-air: flashing the ESP firmware over Wi-Fi instead of USB (env `d1_mini_ota`). |
