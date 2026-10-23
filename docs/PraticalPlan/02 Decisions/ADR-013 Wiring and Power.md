---
title: ADR-013 — ESP ↔ FC Wiring and Power
type: adr
status: accepted
date: 2026-10-12
updated: 2026-10-23
project: SEA autonomous drone
aliases:
  - D13
tags:
  - adr
  - hardware
---

# ADR-013 — ESP ↔ FC Wiring and Power

> [!success] Status: ACCEPTED (team, 2026-10-12). The electrical checks are still required before connecting.
> Accepts **P5** and partly resolves **FC-04** and **FC-05** in [[ESP_to_FC]].

> [!info] Update 2026-10-20: the install (GitHub #55)
> - The board is an **ESP-12F** module (ESP8266). These notes call it the ESP (team decision, 2026-10-20). The backup notes record its USB-serial chip as an FTDI FT232R (`drone/firmware/esp8266/backup/README.md`).
> - Since 2026-10-19 the ESP is **soldered to FC UART4** (T4/R4, 5V, GND) with wires that are easy to remove. It is not a permanent install, and the removable connector of [[ADR-021 Removable ESP Connector|ADR-021]] is not fitted yet.
> - S2-01 passed on the soldered link on 2026-10-19: the FC answers MSP through UART4, so the crossover is right ([[ESP Bench Test Plan]] §4).
> - **Still open:** the ESP current (S1-08, FC-05) is not recorded, and the [[ESP_to_FC]] §10 evidence on the soldered joints is still to be posted (GitHub #30).
> - The decision does not change.

## 1. Decision

- Crossover: **FC T4 → ESP RX (GPIO3)**, **FC R4 ← ESP TX (GPIO1)**, common GND.
- Logic: STM32F722 and ESP8266 are both 3.3 V logic devices. **Measure** the T4 idle level before connecting (FC-04).
- Power: **FC 5V pad → ESP 5V pin**. The ESP's own regulator makes 3.3 V. **Never put 5 V on a GPIO.**

## 2. Open items

- The FC 5V rail current rating is not documented in our sources. The ESP draws peaks of hundreds of mA during Wi-Fi transmit. Measure on the bench before flying (FC-05).
- Which 5V pad: use a free one, e.g. the buzzer/LED group (#14 in [[2.5Drone]]), after checking it with a multimeter.

### Update 2026-10-23: which 5V pad

The ESP takes the **5V pad next to T4** (the UART4 row, [[Wiring and Pinout]] §1). The ELRS receiver moved to **DV5V** (GitHub #41), so that pad is free. The buzzer/LED pad suggested in §2 is not used. The decision is unchanged.

## 3. Mounting

Test before soldering: the [[ESP Bench Test Plan]] Stages 1–2 must pass first. Mounting with a removable connector is accepted in [[ADR-021 Removable ESP Connector|ADR-021]] (2026-10-20); today the ESP is on soldered, easily removed wires.

## 4. Related pages

- [[Wiring and Pinout]] · [[ESP_to_FC]] §10 · [[Decision Register]]

## 5. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **ESP8266** | The Wi-Fi microcontroller of the drone's ESP module (an ESP-12F). It bridges the ground PC to the FC over UART4. |
| **FC** | Flight Controller: on this project the OddityRC F7 AIO (STM32F722, Betaflight target `ODDITYRCF7AIO`) of the 2.5" drone. It stabilises the aircraft and takes MSP from the ESP on UART4. |
| **OD-xx / FC-xx / TF-xx** | Open-decision IDs in PC_to_ESP, ESP_to_FC and ThoneFlow. The Decision Register shows which ADR resolves each one. |
