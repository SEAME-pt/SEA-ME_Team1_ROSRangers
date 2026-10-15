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

> [!info] Where we are (2026-10-12)
> - SEA:ME started on **7 Oct 2026**. We are in **week 2 of Sprint 0** (12–16 Oct). Module 1 starts **19 Oct**.
> - Done so far: documentation and reading (see [[Study Plan v2#2. Sprint 0 — status (7–16 Oct)]]).
> - All proposals are **accepted** as ADRs. See [[Decision Register]].
> - Project: **gesture control** with MediaPipe, accepted by instructors as our Module 2 "edge inference in the loop".

---

## 1. Map of this folder

| Area | Notes |
|---|---|
| Plan | [[Study Plan v2]] · [[Roadmap to April 2027]] · [[Team Tracks]] · [[Weekly Rituals]] |
| Decisions | [[Decision Register]] (ADR-001 to ADR-020) |
| Architecture | [[System Overview]] · [[Protocol Spec v0]] · [[ESP Link State Machine]] · [[MSP Cheat Sheet]] · [[Betaflight Setup]] · [[Wiring and Pinout]] · [[PX4 SITL Setup]] |
| CI/CD | [[CI-CD Overview]] · [[Repository Layout]] · [[GitHub Actions Starter]] · [[Testing Strategy]] · [[HIL Runner]] |
| Hardware | [[Hardware Inventory]] · [[Safety Rules]] |
| Gestures | [[Gesture Pipeline]] |
| Reference | [[Verified Facts and Sources]] · [[Open Questions]] |

## 2. Team documents this plan builds on

| Topic | Note |
|---|---|
| Development process, Git, CI, ADRs | [[Dev_Strategy]] · [[Development_Plan]] · Git Learning notes |
| Hardware identification | [[2.5Drone]] |
| Ground ↔ ESP link | [[PC_to_ESP]] |
| ESP ↔ FC link | [[ESP_to_FC]] |
| Optical flow | [[ThoneFlow]] |
| Gesture prototype | [[Gesture Recognition Program]] |

## 3. The five things to remember

1. On Betaflight **4.5.1**, MSP Override **holds the last values** if the ESP stops. Never flip the override switch before the ESP is streaming. We upgrade to **2026.6.2** ([[ADR-014 Betaflight Version|ADR-014]]).
2. The **ELRS radio and the safety pilot always win**. Gestures never arm the drone.
3. ESP on **UART4 (T4/R4)**. ELRS stays on **UART2**, the VTX on **UART3** ([[ADR-011 FC UART Allocation|ADR-011]]).
4. Gestures come from a **ground webcam** in the coding room ([[ADR-015 Gesture Input Source|ADR-015]]).
5. Module 1 needs a **green CI with >80% coverage and a HIL job** by 11 Dec. Start in week 1.

## 4. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **CI** | Continuous Integration: automated validation of proposed changes. |
| **FC** | Flight Controller: the onboard controller responsible for stabilization and control. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **MediaPipe** | A framework used here for detecting and interpreting hand landmarks and gestures. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **VTX** | Video transmitter: hardware that sends the aircraft video feed. |
