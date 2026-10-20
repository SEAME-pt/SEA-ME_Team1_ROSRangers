---
title: Team Tracks
type: plan
status: active
date: 2026-10-12
project: SEA autonomous drone
tags:
  - plan
  - team
---

# Team Tracks

The program evaluates **individual** contributions, and everyone must understand the whole drone. So: a **shared core** in week 1, **owned tracks** after, and **pairing and rotation** every sprint.

---

## 1. Tracks

| Track | Owns | Pairs with | Module 1 evidence |
|---|---|---|---|
| A · Flight & Safety | Betaflight config + upgrade, ELRS, failsafe, flight tests, safety-pilot rota, [[Safety Rules]] | B (override tests) | failsafe videos, `diff all` history, flight log |
| B · Embedded Link | D1 mini firmware, MSP, C++ protocol lib, ThoneFlow reader | A, D | [[ESP Link State Machine]], unit tests, HIL passing, [[ESP_to_FC]] §11 results |
| C · Ground & ROS 2 | `flight_interface` lifecycle node, Python protocol, controller | E | node graph, message contracts |
| D · CI/CD & Test | workflows, coverage gates, static analysis, SITL job, [[HIL Runner]] | B | green pipeline, HIL job |
| E · Sim, Estimation & Gestures | PX4 SITL/Gazebo, flow estimation, [[00 Gesture Pipeline|Gesture Pipeline]] | C | sim-to-real report |

## 2. Rules

- Every PR is reviewed by two people **outside** the author's track (knowledge spreads). Merge rules follow [[Dev_Strategy]] §8–§9.
- Each person gives one 20-min **teach-back** per sprint on their track.
- Rotate one person between tracks each sprint. CI (D) and Safety (A) keep one stable owner.
- Safety pilot: at least 2 trained people. Nobody flies alone ([[Safety Rules]] #15). The safety induction is done (GitHub #13, closed, 2026-10-20). Named safety pilot: BrunoBMFT. The second person rotates among trained team members each session.

## 3. Glossary

| Term | Meaning |
| --- | --- |
| **CI** | Continuous Integration: automated validation of proposed changes. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **PR** | Pull Request: a proposed change submitted for review before merging. |
| **ROS 2** | The second-generation Robot Operating System middleware used for robotics integration. |
| **FC** | Flight Controller: the onboard controller responsible for stabilization and control. |
| **ELRS** | ExpressLRS: the open-source radio-control link between the RadioMaster Pocket and the BETAFPV Nano receiver on the drone. |
| **SITL** | Software In The Loop: the flight code runs on the PC against a simulator. |
| **PX4** | An open-source flight-control stack, used here only in simulation (PX4 SITL + Gazebo). |
