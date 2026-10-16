---
title: ADR-001 — Message Categories
type: adr
status: accepted
date: 2026-10-12
project: SEA autonomous drone
aliases:
  - D01
tags:
  - adr
  - communication
---

# ADR-001 — Message Categories

> [!success] Status: ACCEPTED (team, 2026-10-12)
> Amends proposal **P1** in [[PC_to_ESP]] §3.3 (four categories → five).

## 1. Context

[[PC_to_ESP]] §5 proposed four categories: configuration, diagnostics, control setpoints, telemetry. Arm requests, resume handshakes and session messages did not fit cleanly in any of them, and would otherwise end up inside "configuration".

## 2. Decision

**Five** logical categories:

| Category | Transport | Examples |
|---|---|---|
| Session / command | TCP | HELLO, ARM_CONTROL_REQUEST, RESUME_REQUEST, BYE |
| Configuration | TCP | parameter get/set |
| Diagnostics | TCP | events, logs, counters |
| Control setpoints | UDP | SETPOINT |
| Telemetry | UDP | STATE |

Type ID ranges are defined in [[Protocol Spec v0]].

## 3. Consequences

- The [[ESP Link State Machine]] reacts to *events* (session/command) separately from *parameters* (configuration). This keeps the state machine simple.
- Configuration can be rejected while control is active (OD-16 in [[PC_to_ESP]]) without blocking the resume/arm commands.

## 4. Related pages

- [[PC_to_ESP]] §5 · [[Protocol Spec v0]] · [[Decision Register]]

## 5. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **TCP** | Transmission Control Protocol: reliable, ordered, connection-oriented transport. |
| **Telemetry** | Periodic data sent from a system to report state, measurements, or health. |
| **UDP** | User Datagram Protocol: connectionless transport suited to refreshed, time-sensitive data. |
