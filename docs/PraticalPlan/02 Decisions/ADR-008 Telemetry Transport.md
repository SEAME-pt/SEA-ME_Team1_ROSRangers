---
title: ADR-008 — Telemetry Transport
type: adr
status: accepted
date: 2026-10-12
project: SEA autonomous drone
aliases:
  - D08
  - D08 Telemetry Transport
tags:
  - adr
  - communication
---

# ADR-008 — Telemetry Transport

> [!success] Status: ACCEPTED (team, 2026-10-12)
> Resolves **OD-15** and **supersedes P8** ("UDP by default, TCP fallback") in [[PC_to_ESP]].

## 1. Context

Two versions existed: "TCP for telemetry" (first project brief) and "UDP by default, TCP fallback" (P8). "Fallback" was undefined. When UDP fails, TCP on the same Wi-Fi usually fails too.

## 2. Decision

Split by the **meaning** of the data:

| Data | Transport | Why |
|---|---|---|
| Periodic state: attitude, battery, link counters, flow, `last_ctrl_seq` | **UDP**, 20–50 Hz | Latest value wins. A lost packet is replaced in 20–50 ms. No head-of-line blocking. |
| Events: state transitions, failsafe entered/left, warnings | **TCP** | Each one matters |
| Logs, config reads/writes, acknowledgements | **TCP** | Reliability and ordering |

## 3. Consequences

- TEL-01 to TEL-05 in [[ESP_to_FC]] §7 map onto the STATE message ([[Protocol Spec v0]]).
- No "fallback" path to specify or test.

## 4. Related pages

- [[PC_to_ESP]] §5 · [[Decision Register]]

## 5. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **TCP** | Transmission Control Protocol: reliable, ordered, connection-oriented transport. |
| **Telemetry** | Periodic data sent from a system to report state, measurements, or health. |
| **UDP** | User Datagram Protocol: connectionless transport suited to refreshed, time-sensitive data. |
| **FC** | Flight Controller: the onboard controller responsible for stabilization and control. |
