---
title: Safety Rules
type: safety
status: active
date: 2026-10-12
project: SEA autonomous drone
tags:
  - safety
---

# Safety Rules (all weeks, no exceptions)

> [!danger] These rules apply to every bench session and every flight
> They implement SAF-01 to SAF-08 in [[ESP_to_FC]] §8 and [[Dev_Strategy]] §22.

---

## 1. Rules

1. **Props off** for every bench test. Props go on only in the cage, for a planned flight.
2. **Antenna on the VTX before any battery** is connected, or the VTX burns out ([[2.5Drone]]).
3. Order: USB only → battery without props → props in the cage.
4. **Safety pilot** with the RadioMaster at the cage, line of sight, with ARM and the override switch under a finger. The radio always wins over code.
5. **Gestures never arm or disarm.** The gesture operator is in the coding room and may not see the drone ([[ADR-015 Gesture Input Source|ADR-015]]).
6. Switch MSP Override on only when telemetry shows the ESP is `STREAMING` ([[ADR-010 MSP Override|ADR-010]]).
7. Pre-arm checklist: battery charged and inspected, props tight and in the right direction, failsafe tested today, zone clear, nobody in the cage.
8. LiPo: charge only at the lab station/bags, never unattended, land at 3.5 V/cell.
9. ESP8266 GPIOs are 3.3 V. Feed 5 V only to the D1 mini **5V** pin. Never connect USB and the FC to the D1 mini at the same time ([[ADR-012 ESP UART Pins|ADR-012]]).
10. HIL bench: **no LiPo** connected, ever.
11. **Nothing is soldered to the FC** until the [[ESP Bench Test Plan]] Stages 1–2 and the [[ESP_to_FC]] §10 checklist have passed.
12. Every crash, incident or fault goes in GitHub Issues with cause and fix (program rule; spares are limited).

---

## 2. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **ESP8266** | The Wi-Fi microcontroller of the drone's ESP module (an ESP-12F, Wemos D1 mini class). It bridges the ground PC to the FC over UART4. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **FC** | Flight Controller: on this project the OddityRC F7 AIO (STM32F722, Betaflight target `ODDITYRCF7AIO`) of the 2.5" drone. It stabilises the aircraft and takes MSP from the ESP on UART4. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **Telemetry** | Periodic data sent from a system to report state, measurements, or health. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **VTX** | Video transmitter: hardware that sends the aircraft video feed. |
| **LiPo** | Lithium-polymer battery: the 4S flight pack. Handle it by the rules in Safety Rules. |
| **USB** | Universal Serial Bus: used to flash the ESP and to power or configure the FC from a PC. |
| **SAF-xx** | Safety requirement IDs SAF-01 to SAF-08 defined in ESP_to_FC section 8 (independent ELRS path, pilot takeover, no arming from code). |
