---
title: SEA:ME Drone — Practical Plan Home
type: moc
status: active
date: 2026-10-12
project: SEA autonomous drone
aliases:
  - Home
  - Practical Plan
tags:
  - moc
  - plan
---

# SEA:ME Drone — Practical Plan Home

> [!summary] In one sentence
> This folder turns the project's documentation into a **dated, team-of-5 plan** with **accepted decisions (ADRs)**, an **architecture**, and a **CI/CD + HIL setup** that meets the Module 1 deliverables.

> [!info] Where we are (2026-10-20)
> - SEA:ME started on **7 Oct 2026**. Sprint 0 ran **7–18 Oct**. **Module 1 started 19 Oct**: we are in week 1 of Sprint 1.
> - Closed GitHub issues so far: safety induction (#13), Betaflight 4.5.1 `diff all` baseline (#14), barometer re-check (#15), FC multimeter checks (#16), ESP AT firmware backup (#17), instructor questions (#18), XT30 to XT60 adapter and battery check (#19), PX4 SITL + Gazebo default quad (#29), docs vault in the repo (#36), `MSP_API_VERSION` / `MSP_STATUS` / `MSP_ATTITUDE` read over the FC USB (#24, closed 2026-10-19). See [[Study Plan v2#2. Sprint 0 — status (7–18 Oct)]].
> - Open: the Module 1 week 1 board issues (for example #21 to #23, #25 to #28), the HIL runner (#48) and the HIL-without-spare-hardware spike (#61). Tasks live on the board, not here.
> - Decisions are **accepted** as ADRs, except ADR-022 (proposed, PR #74). See [[Decision Register]].
> - Project: **gesture control** with MediaPipe, accepted by instructors as our Module 2 "edge inference in the loop".

---

## 1. Map of this folder

| Area | Notes |
|---|---|
| Plan | [[Study Plan v2]] · [[Roadmap to April 2027]] · [[Team Tracks]] · [[Requirements]] · [[Weekly Rituals]] |
| Decisions | [[Decision Register]] (ADR-001 to ADR-022; ADR-022 is proposed) |
| Architecture | [[System Overview]] · [[Protocol Spec v0]] · [[ESP Link State Machine]] · [[MSP Cheat Sheet]] · [[Betaflight Setup]] · [[Wiring and Pinout]] · [[PX4 SITL Setup]] |
| CI/CD | [[CI-CD Overview]] · [[Repository Layout]] · [[GitHub Actions Starter]] · [[Testing Strategy]] · [[HIL Runner]] |
| Hardware | [[Hardware Inventory]] · [[Safety Rules]] |
| Gestures | [[00 Gesture Pipeline]] · [[01 ROS2 Nodes Installation]] · [[02 Gesture Node]] · [[03 Intent Node]] · [[04 Mission Node]] · [[05 Future Nodes]] |
| Reference | [[Verified Facts and Sources]] · [[Open Questions]] |

## 2. Team documents this plan builds on

| Topic | Note |
|---|---|
| Development process, Git, CI, ADRs | [[Dev_Strategy]] · [[Development_Plan]] · Git Learning notes |
| Hardware identification | [[2.5Drone]] |
| Ground ↔ ESP link | [[PC_to_ESP]] |
| ESP ↔ FC link | [[ESP_to_FC]] |
| Optical flow | [[ThoneFlow]] |
| Gesture prototype | [[02 Gesture Node|Gesture Recognition Program]] |

## 3. The five things to remember

1. On Betaflight **4.5.1**, MSP Override **holds the last values** if the ESP stops. Never flip the override switch before the ESP is streaming. We upgrade to **2026.6.2** ([[ADR-014 Betaflight Version|ADR-014]]).
2. The **ELRS radio and the safety pilot always win**. Gestures never arm the drone.
3. ESP on **UART4 (T4/R4)**. ELRS stays on **UART2**, the VTX on **UART3** ([[ADR-011 FC UART Allocation|ADR-011]]).
4. Gestures come from a **ground webcam** in the coding room ([[ADR-015 Gesture Input Source|ADR-015]]).
5. Module 1 needs a **green CI with >80% coverage and a HIL job** by 11 Dec. Start in week 1.

## 4. Status vocabulary

The values used in the frontmatter `status` of these notes:

| Status | Meaning |
|---|---|
| `draft` | Written but not reviewed or confirmed. |
| `active` | In use and kept current (plans, process, maps). |
| `proposed` | A decision waiting for the team to accept it (for example ADR-022). |
| `accepted` | A decision the team accepted (ADRs). |
| `accepted-with-conditions` | Accepted, but a stated condition must hold. |
| `verified` | Facts checked against a source. |
| `legacy` | Kept for history, not a source. Tasks and plans live elsewhere. |

## 5. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **CI** | Continuous Integration: automated validation of proposed changes. |
| **FC** | Flight Controller: on this project the OddityRC F7 AIO (STM32F722, Betaflight target `ODDITYRCF7AIO`) of the 2.5" drone. It stabilises the aircraft and takes MSP from the ESP on UART4. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **MediaPipe** | A framework used here for detecting and interpreting hand landmarks and gestures. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **VTX** | Video transmitter: hardware that sends the aircraft video feed. |
| **ELRS** | ExpressLRS: the open-source radio-control link between the RadioMaster Pocket and the BETAFPV Nano receiver on the drone. |
