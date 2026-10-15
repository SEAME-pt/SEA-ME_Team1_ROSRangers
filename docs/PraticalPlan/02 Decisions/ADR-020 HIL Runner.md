---
title: ADR-020 — Hardware-in-the-Loop Runner
type: adr
status: accepted
date: 2026-10-12
project: SEA autonomous drone
aliases:
  - D20
  - D20 HIL Runner
tags:
  - adr
  - ci
  - hil
---

# ADR-020 — Hardware-in-the-Loop Runner

> [!success] Status: ACCEPTED (team, 2026-10-12). Instructors confirmed there are no lab runners (2026-10-14): we build our own.

## 1. Context

- Module 1 requires "a hardware-in-the-loop job on a self-hosted runner that flashes and tests the target".
- The program lists lab self-hosted runners as shared equipment, but instructors confirmed none are available (2026-10-14).
- The team has Raspberry Pis and Arduinos. The **Raspberry Pi 5** in [[2.5Drone]] is the ground computer option, so keep it free for that.

## 2. Decision

If the lab provides a runner, use it. Otherwise:

- A **dedicated Raspberry Pi** registered as a GitHub Actions self-hosted runner (label `hil-drone`).
- Attached by USB: a spare D1 mini and the FC (or a spare FC).
- **No battery, no props.** USB power alone cannot spin the motors.

Details in [[HIL Runner]].

## 3. Related pages

- [[HIL Runner]] · [[CI-CD Overview]] · [[Decision Register]]

## 4. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **CI** | Continuous Integration: automated validation of proposed changes. |
| **FC** | Flight Controller: the onboard controller responsible for stabilization and control. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
