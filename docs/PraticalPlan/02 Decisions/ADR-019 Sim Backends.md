---
title: ADR-019 — Simulation Backends
type: adr
status: accepted
date: 2026-10-12
project: SEA autonomous drone
aliases:
  - D19
  - D19 Sim Backends
tags:
  - adr
  - simulation
---

# ADR-019 — Simulation Backends

> [!success] Status: ACCEPTED (team, 2026-10-12)

## 1. Decision

Two simulators, both behind the **same `flight_interface`**:

| Backend | Used for | Why |
|---|---|---|
| **PX4 SITL + Gazebo** | Flight behaviour, controller, missions, sim-to-real report | Prescribed by the program |
| **Betaflight 2026.6.2 SITL** | MSP-level integration tests in CI | Same firmware family as the drone. Builds in about 25 s. UART1 is exposed on TCP 5761 (**verified**). |

## 2. Consequences

The sim-to-real gap is largest at the interface: PX4 takes setpoints, Betaflight takes stick values. The adapter that converts attitude/thrust into channel values is tested against Betaflight SITL, and the flight behaviour against PX4.

## 3. Related pages

- [[Testing Strategy]] · [[GitHub Actions Starter]] · [[Decision Register]]

## 4. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **CI** | Continuous Integration: automated validation of proposed changes. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **TCP** | Transmission Control Protocol: reliable, ordered, connection-oriented transport. |
| **SITL** | Software In The Loop: the flight code runs on the PC against a simulator. |
| **PX4** | An open-source flight-control stack, used here only in simulation (PX4 SITL + Gazebo). |
| **Gazebo** | The 3D physics simulator used with PX4 (Harmonic release). |
