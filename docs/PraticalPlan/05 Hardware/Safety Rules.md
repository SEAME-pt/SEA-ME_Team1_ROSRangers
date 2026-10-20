---
title: Safety Rules
type: safety
status: active
date: 2026-10-20
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
13. **Transparent UART↔UDP bridge: props off only** (added 2026-10-20). A battery is allowed, because the drone needs it to power the FC and the ESP. The existing LiPo rules still apply (#2, #3, #8). Network isolation is **not a rule yet**: see §2.
14. **MSP Override is bench-only until Betaflight 2026.6.2** (added 2026-10-20). It stays props-off on 4.5.1 ([[ADR-010 MSP Override|ADR-010]] condition 6).
15. **Nobody flies alone** (added 2026-10-20). A flight needs the pilot at the cage plus at least one more person present ([[Team Tracks]] §2).

---

## 2. Open items (not rules yet)

- **Network isolation for the bridge (open, 2026-10-20).** Whether bridge tests need an isolated network is still being tested. The shared SEAME Wi-Fi is under evaluation. Tracked in [[Open Questions]]. Until the team decides, treat the bridge as props-off only (#13).
- **Conflict between #13 and #10 (open, 2026-10-20).** #13 allows a battery for bridge tests, but #10 says the HIL bench never has a LiPo ([[HIL Runner]]). When the flight unit is also the HIL target (GitHub #61), a bridge test there would need a battery. The team must decide: a separate bridge setup, a spare FC for the HIL bench, or a stated exception. Until then, **no battery on the HIL bench** (#10 wins).

## 3. Safety roles

- **Safety pilot:** BrunoBMFT. The safety induction is done (GitHub #13, closed). The second trained safety pilot is **to be named** ([[Team Tracks]] §2).

---

## 4. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **ESP8266** | A WiFi-enabled microcontroller used as the drone communication module. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **FC** | Flight Controller: the onboard controller responsible for stabilization and control. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **Telemetry** | Periodic data sent from a system to report state, measurements, or health. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **VTX** | Video transmitter: hardware that sends the aircraft video feed. |
| **LiPo** | Lithium-polymer battery: the 4S flight pack. Handle it by the rules in Safety Rules. |
| **Bridge** | The transparent UART↔UDP bridge: the ESP forwards bytes between the FC UART and the ground over Wi-Fi without parsing them. |
| **USB** | Universal Serial Bus: used to flash the ESP and to power or configure the FC from a PC. |
| **SAF-xx** | Safety requirement IDs SAF-01 to SAF-08 defined in ESP_to_FC section 8 (independent ELRS path, pilot takeover, no arming from code). |
