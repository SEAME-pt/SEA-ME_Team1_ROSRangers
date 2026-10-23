---
title: ADR-003 — Sequence Acceptance
type: adr
status: accepted
date: 2026-10-12
project: SEA autonomous drone
aliases:
  - D03
tags:
  - adr
  - communication
---

# ADR-003 — Sequence Acceptance

> [!success] Status: ACCEPTED (team, 2026-10-12)
> Resolves **OD-08** and **OD-09** in [[PC_to_ESP]] §12, and proposal **P3**.

## 1. Context

REQ-COM-006 to 013 (old IDs REQ-SEQ-01 to 04 and REQ-ORD-01 to 04) in [[PC_to_ESP]] §6 require duplicates and reordering to be handled, sequence width to be defined, and a restarted sender not to be confused with replayed traffic.

## 2. Decision

- **32-bit** sequence number per sender, per session, starting at 1.
- A **random 32-bit session ID** is chosen at every session start ([[ADR-005 TCP Session|ADR-005]]). It is covered by the MAC ([[ADR-002 Message Integrity|ADR-002]]).
- Control is accepted only if `seq > last_accepted_ctrl_seq` **in the same session**. Duplicates and older messages are dropped and counted. They do not reset the loss timer.
- Gaps are counted only. No action on a gap alone: the loss timeout handles outages.
- Wrap-around: at 50 Hz, 32 bits last about 2.7 years. The ESP rejects `seq ≥ 2^31` and asks for a new session, so no wrap-aware comparison is needed.

## 3. Consequences

- A ground restart opens a new session and the window resets. A replay from an old session fails the session check.
- Telemetry exposes the counters (duplicates, stale, gaps, MAC failures) required by [[PC_to_ESP]] §5.

## 4. Related pages

- [[Protocol Spec v0]] · [[ESP Link State Machine]] · [[Decision Register]]

> 2026-10-20: requirement IDs renumbered to the `REQ-<cat>-NNN` scheme (see [[Requirements]], issue #57). Old IDs are kept in brackets. The decision is unchanged.

## 5. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **TCP** | Transmission Control Protocol: reliable, ordered, connection-oriented transport. |
| **Telemetry** | Periodic data sent from a system to report state, measurements, or health. |
| **REQ** | Requirement ID of the form `REQ-<SYS, NAV, SEN, COM, CTL, SAF, PWR, SW or HW>-NNN`, used for traceability. |
| **OD-xx / FC-xx / TF-xx** | Open-decision IDs in PC_to_ESP, ESP_to_FC and ThoneFlow. The Decision Register shows which ADR resolves each one. |
