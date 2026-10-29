---
title: Safety Rules
type: safety
status: active
date: 2026-10-20
updated: 2026-10-29
project: SEA autonomous drone
tags:
  - safety
---

# Safety Rules (all weeks, no exceptions)

> [!danger] These rules apply to every bench session and every flight
> They implement REQ-SAF-001 (was SAF-01) to REQ-SAF-008 (was SAF-08) in [[ESP_to_FC]] §8 and [[Dev_Strategy]] §22.

---

## 1. Rules

1. **Props off** for every bench test. Props go on only in the cage, for a planned flight.
2. **Antenna on the VTX before any battery** is connected, or the VTX burns out ([[2.5Drone]]).
3. Order: USB only → battery without props → props in the cage.
4. **Safety pilot** with the RadioMaster at the cage, line of sight, with the kill switch (SA) and the takeover switch under a finger (§3). The radio always wins over code.
5. **Gestures never arm or disarm.** The gesture operator is in the coding room and may not see the drone ([[ADR-015 Gesture Input Source|ADR-015]]).
6. Switch MSP Override on only when telemetry shows the ESP is `STREAMING` ([[ADR-010 MSP Override|ADR-010]]).
7. Pre-arm checklist: battery charged and inspected, props tight and in the right direction, failsafe tested today, ESP wires or connector inspected ([[ADR-021 Removable ESP Connector|ADR-021]]), zone clear, nobody in the cage.
8. LiPo: charge only at the lab station/bags, never unattended, land at 3.5 V/cell.
9. ESP8266 GPIOs are 3.3 V. Feed 5 V only to the ESP **5V** pin. Never connect USB and the FC to the ESP at the same time ([[ADR-012 ESP UART Pins|ADR-012]]).
10. HIL bench: **no LiPo** connected, ever.
11. **Nothing is soldered to the FC** until the [[ESP Bench Test Plan]] Stages 1–2 and the [[ESP_to_FC]] §10 checklist have passed. (2026-10-20: the ESP was soldered on 2026-10-19 before these passed; logged as incident GitHub #69. The rule still applies to every new joint.)
12. Every crash, incident or fault goes in GitHub Issues with cause and fix (program rule; spares are limited).
13. **Transparent UART↔UDP bridge: props off only** (added 2026-10-20). A battery is allowed, because the drone needs it to power the FC and the ESP. The existing LiPo rules still apply (#2, #3, #8). Network isolation is **not a rule yet**: see §2.
14. **MSP Override is bench-only until Betaflight 2026.6.2** (added 2026-10-20). It stays props-off on 4.5.1 ([[ADR-010 MSP Override|ADR-010]] condition 6).
15. **Nobody flies alone** (added 2026-10-20). Nobody powers up, arms, test-runs or flies the drone alone. A pilot and at least one other person are always present ([[Team Tracks]] §2).
16. **Battery and USB together are allowed with props off** (added 2026-10-23, team decision). Some bench work needs both, for example the Betaflight Motors tab and the motor-direction tool ([[Radio Link Tests]] §3.1). Props off (#1) is the primary rule; this replaces the "never battery and USB together" warning in [[2.5Drone]].
17. **The HIL bench may run unattended only with no FC, no LiPo and no motors** (added 2026-10-26, team decision, GitHub #61). The bench is the Pi 4 runner with the spare ESP on USB ([[HIL Runner]] §3.1). The flight unit is never connected to it. Every other bench or flight session is attended. Rule #15 covers the drone, not this bench, but #10 still applies.
18. **Hands off an armed drone** (added 2026-10-29, incident GitHub #131). Nobody touches, catches, pushes or guards an armed drone by hand, even close to the ground. Everyone except the pilot stays outside the flight area while it is armed. If the drone moves towards someone, the pilot disarms with the kill switch (SA) and the person steps back. Hands only after disarm and the props have stopped.

---

## 2. Open items (not rules yet)

- **Network isolation for the bridge (open, 2026-10-20).** Whether bridge tests need an isolated network is still being tested. The shared SEAME Wi-Fi is under evaluation. Tracked in [[Open Questions]]. Until the team decides, treat the bridge as props-off only (#13).
- **Height limit and overhead clearance (open, 2026-10-29).** The flight area has no roof; on 2026-10-28 the drone climbed into a steel ceiling beam at about 2 m (incident #126). The team decides a maximum flight height and keeps flights away from beams and anything else overhead.
- ~~**Conflict between #13 and #10 (open, 2026-10-20).**~~ **Closed 2026-10-26.** The HIL scope decision (GitHub #61, [[ADR-020 HIL Runner|ADR-020]]) keeps the flight unit off the HIL bench, so bridge tests with a battery (#13) never run there. #10 stands unchanged: no battery on the HIL bench.

## 3. Safety roles

- **Safety pilot:** BrunoBMFT. **One trained safety pilot is enough** (team decision 2026-10-23). The safety induction is done (GitHub #13, closed). The second person is **rotated** among team members per session, so no fixed name ([[Team Tracks]] §2, rule #15).

### 3.1 Stops and switches (team definitions, 2026-10-23)

| Term | What it does | Where |
|---|---|---|
| **Kill switch** | SA on the RadioMaster, which is also the ARM switch. Releasing it disarms and stops the motors. | Tested on the bench 2026-10-22 ([[Radio Link Tests]] RL-12), REQ-SAF-022 |
| **Takeover switch** | The MSP Override switch. Turning it off ends ground control: from then until it is turned on again, the drone takes commands only from the radio. Its AUX channel is not mapped yet. | [[ADR-010 MSP Override\|ADR-010]] (update 2026-10-23), REQ-SAF-002 |
| **E-stop** | Emergency stop: when something fails, the drone is brought to a safe stop, in the air or on the ground. The faults it must cover are in [[Testing Strategy]] §2. | REQ-SAF-021, Module 1 demo |

## 4. Related pages

- [[Radio Link Tests]] · [[Battery Log]] · [[ADR-004 ESP Failsafe|ADR-004]] · [[ADR-010 MSP Override|ADR-010]] · [[ADR-023 ELRS Firmware Version and Regulatory Domain|ADR-023]]

---

## 5. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **ESP8266** | The Wi-Fi microcontroller of the drone's ESP module (an ESP-12F). It bridges the ground PC to the FC over UART4. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **FC** | Flight Controller: on this project the OddityRC F7 AIO (STM32F722, Betaflight target `ODDITYRCF7AIO`) of the 2.5" drone. It stabilises the aircraft and takes MSP from the ESP on UART4. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **Telemetry** | Periodic data sent from a system to report state, measurements, or health. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **VTX** | Video transmitter: hardware that sends the aircraft video feed. |
| **LiPo** | Lithium-polymer battery: the 4S flight pack. Handle it by the rules in Safety Rules. |
| **Bridge** | The transparent UART↔UDP bridge: the ESP forwards bytes between the FC UART and the ground over Wi-Fi without parsing them. |
| **USB** | Universal Serial Bus: used to flash the ESP and to power or configure the FC from a PC. |
| **REQ-SAF-001 to 008** | Safety requirements (old IDs SAF-01 to SAF-08) defined in ESP_to_FC §8 and registered in [[Requirements]]: independent ELRS path, pilot takeover, no arming from code. |
| **Cage** | Not a physical cage: a clear area in the lab with at least 1.20 m to any obstacle on every side, and no net or roof. Flights happen only there. (Corrected 2026-10-29: earlier pages said "netted cage", #126.) |
| **ELRS** | ExpressLRS: the open-source radio-control link between the RadioMaster Pocket and the BETAFPV Nano receiver on the drone. |
| **ESP** | The ESP-12F module (ESP8266) on the drone: the Wi-Fi bridge between the ground PC and the FC, wired to FC UART4. |
| **MSP Override** | Betaflight feature that lets MSP supply the values of selected RC channels, while ELRS stays the primary receiver (ADR-010). |
| **Props off** | Propellers physically removed. Required for every bench test ([[Safety Rules]] #1). |
| **Safety pilot** | The trained person at the cage with the RadioMaster and line of sight. The radio always wins over code. |
| **STREAMING** | ESP link state in which ground setpoints are being forwarded to the FC ([[ESP Link State Machine]]). |
