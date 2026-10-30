---
title: SEA:ME Drone — Practical Plan Home
type: moc
status: active
date: 2026-10-12
updated: 2026-10-23
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

> [!info] Where we are (2026-10-23)
> - SEA:ME started on **7 Oct 2026**. Sprint 0 ran **7–18 Oct**. **Module 1 started 19 Oct**: we are in week 1 of Sprint 1.
> - Closed GitHub issues so far: safety induction (#13), Betaflight 4.5.1 `diff all` baseline (#14), barometer re-check (#15), FC multimeter checks (#16), ESP AT firmware backup (#17), instructor questions (#18), XT30 to XT60 adapter and battery check (#19), PX4 SITL + Gazebo default quad (#29), docs vault in the repo (#36), `MSP_API_VERSION` / `MSP_STATUS` / `MSP_ATTITUDE` read over the FC USB (#24, closed 2026-10-19). See [[Study Plan v2#2. Sprint 0 — status (7–18 Oct)]].
> - Done since 2026-10-20: ELRS link restored on 4.1.0 CE_LBT (2026-10-21, [[ADR-023 ELRS Firmware Version and Regulatory Domain|ADR-023]]). Failsafe with the TX off, with a video (2026-10-22, #22). Pi 4 HIL runner registered (2026-10-22, #48). `gesture_node` and `intent_node` in `dev` (#78). Betaflight SITL MSP tests in CI (#85, #87).
> - Open: the first 4.5.1 hover (#23), the link-loss tests, being redone after hardware problems (#30), incident #69, the HIL runner setup (#48) and the HIL-without-spare-hardware spike (#61). Tasks live on the board, not here.
> - All decisions are **accepted** as ADRs. See [[Decision Register]].
> - Project: **gesture control** with MediaPipe, accepted by instructors as our Module 2 "edge inference in the loop".

---

## 1. Map of this folder

| Area | Notes |
|---|---|
| Plan | [[Study Plan v2]] · [[Roadmap to April 2027]] · [[Team Tracks]] · [[Requirements]] · [[Weekly Rituals]] · [[Dailys]] |
| Decisions | [[Decision Register]] (ADR-001 to ADR-023, all accepted) |
| Architecture | [[System Overview]] · [[Protocol Spec v0]] · [[ESP Link State Machine]] · [[MSP Cheat Sheet]] · [[Betaflight Setup]] · [[Wiring and Pinout]] · [[PX4 SITL Setup]] |
| CI/CD | [[CI-CD Overview]] · [[Repository Layout]] · [[GitHub Actions Starter]] · [[Testing Strategy]] · [[HIL Runner]] |
| Hardware | [[Hardware Inventory]] · [[Safety Rules]] · [[Battery Log]] · [[ESP Bench Test Plan]] · [[Radio Link Tests]] |
| Gestures | [[00 Gesture Pipeline]] · [[01 ROS2 Nodes Installation]] · [[02 Gesture Node]] · [[03 Intent Node]] · [[04 Mission Node]] · [[05 Future Nodes]] |
| Reference | [[Verified Facts and Sources]] · [[Open Questions]] |

## 2. Team documents this plan builds on

| Topic | Note |
|---|---|
| Development process, Git, CI, ADRs | [[Dev_Strategy]] · [[Development_Plan]] · Git Learning: [[Feature_and_Bugfix_Branches]] · [[Pull_Request_Workflow]] · [[Rebase_Policy]] · [[Merge_Strategy]] · [[Branch_Protection]] |
| Hardware identification | [[2.5Drone]] |
| Ground ↔ ESP link | [[PC_to_ESP]] · [[ESP8266_AT_Commands_and_UDP]] (stock AT firmware, replaced by [[ADR-007 Portable Protocol Library and Custom ESP Firmware\|ADR-007]]) |
| ESP ↔ FC link | [[ESP_to_FC]] |
| Optical flow | [[ThoneFlow]] |
| Gesture recogniser | [[02 Gesture Node\|Gesture Node]] |

## 3. The five things to remember

1. On Betaflight **4.5.1**, MSP Override **holds the last values** if the ESP stops. Never flip the override switch before the ESP is streaming. We upgrade to **2026.6.2** ([[ADR-014 Betaflight Version|ADR-014]]).
2. The **ELRS radio and the safety pilot always win**. Gestures never arm the drone.
3. ESP on **UART4 (T4/R4)**. ELRS stays on **UART2**, the VTX on **UART3** ([[ADR-011 FC UART Allocation|ADR-011]]).
4. Gestures come from a **ground webcam** in the coding room ([[ADR-015 Gesture Input Source|ADR-015]]).
5. Module 1 needs a **green CI (>80 % coverage, 90 % on the ROS 2 nodes) and a HIL job** by 2026-12-11. Start in week 1.

## 4. Status vocabulary

The values used in the frontmatter `status` of these notes:

| Status | Meaning |
|---|---|
| `draft` | Written but not reviewed or confirmed. |
| `active` | In use and kept current (plans, process, maps). |
| `proposed` | A decision waiting for the team to accept it (for example the 2026-10-20 update of ADR-016). |
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
| **Betaflight** | The open-source FC firmware on the drone: 2026.6.2 installed since 2026-10-28, 4.5.1 before (ADR-014). |
| **ESP** | The ESP-12F module (ESP8266) on the drone: the Wi-Fi bridge between the ground PC and the FC, wired to FC UART4. |
| **MSP Override** | Betaflight feature that lets MSP supply the values of selected RC channels, while ELRS stays the primary receiver (ADR-010). |
| **SITL** | Software In The Loop: the flight code runs on the PC against a simulator. |
| **PX4** | An open-source flight-control stack, used here only in simulation (PX4 SITL + Gazebo). |
| **Sprint** | A 2-week iteration of the board. Sprint 0 = 2026-10-07 to 2026-10-18; Sprint 1 = 2026-10-19 to 2026-11-01. |
| **Safety pilot** | The trained person at the cage with the RadioMaster and line of sight. The radio always wins over code. |
| **E-stop** | Emergency stop: when something fails, the drone is brought to a safe stop, in the air or on the ground. The faults it must cover are in [[Testing Strategy]] §2. |
| **Kill switch** | SA on the RadioMaster, which is also the ARM switch: releasing it disarms and stops the motors ([[Radio Link Tests]] RL-12). |
| **Takeover switch** | The MSP Override switch on the radio: turning it off ends ground control, and from then on the drone takes commands only from the radio (ADR-010). Its channel is not mapped yet. |
