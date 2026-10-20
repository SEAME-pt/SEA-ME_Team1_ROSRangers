---
title: ADR-013 — ESP ↔ FC Wiring and Power
type: adr
status: accepted
date: 2026-10-12
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

## 1. Decision

- Crossover: **FC T4 → D1 mini RX (GPIO3)**, **FC R4 ← D1 mini TX (GPIO1)**, common GND.
- Logic: STM32F722 and ESP8266 are both 3.3 V logic devices. **Measure** the T4 idle level before connecting (FC-04).
- Power: **FC 5V pad → D1 mini 5V pin**. The D1 mini's own regulator makes 3.3 V. **Never put 5 V on a GPIO.**

## 2. Open items

- The FC 5V rail current rating is not documented in our sources. The ESP draws peaks of hundreds of mA during Wi-Fi transmit. Measure on the bench before flying (FC-05).
- Which 5V pad: use a free one, e.g. the buzzer/LED group (#14 in [[2.5Drone]]), after checking it with a multimeter.

## 3. Mounting

Test before soldering: the [[ESP Bench Test Plan]] Stages 1–2 must pass first. Mounting with a removable connector is proposed in [[ADR-021 Removable ESP Connector|ADR-021]].

## 4. Related pages

- [[Wiring and Pinout]] · [[ESP_to_FC]] §10 · [[Decision Register]]

## 5. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **ESP8266** | A WiFi-enabled microcontroller used as the drone communication module. |
| **FC** | Flight Controller: the onboard controller responsible for stabilization and control. |
| **OD-xx / FC-xx / TF-xx** | Open-decision IDs in PC_to_ESP, ESP_to_FC and ThoneFlow. The Decision Register shows which ADR resolves each one. |
