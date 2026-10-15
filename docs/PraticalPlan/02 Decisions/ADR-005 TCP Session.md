---
title: ADR-005 — TCP Session as Prerequisite for Control
type: adr
status: accepted
date: 2026-10-12
project: SEA autonomous drone
aliases:
  - D05
  - D05 TCP Session
tags:
  - adr
  - communication
---

# ADR-005 — TCP Session as Prerequisite for Control

> [!success] Status: ACCEPTED (team, 2026-10-12)
> Accepts proposal **P5** and resolves **OD-14** in [[PC_to_ESP]]. It supersedes the earlier recommendation that "loss of the TCP session makes control invalid".

## 1. Context

- A session gives one place for version negotiation, nonce/key exchange and sequence reset ([[ADR-003 Sequence Acceptance|ADR-003]]).
- TCP can look alive while the peer is gone, and it can stall briefly while UDP still works ([[PC_to_ESP]] §4.1).
- Treating every TCP hiccup as a control loss would cause unnecessary failsafes in flight.

## 2. Decision

- Control over UDP is accepted **only inside a session** opened over TCP (HELLO / HELLO_ACK).
- **UDP control traffic decides liveness** ([[ADR-004 ESP Failsafe|ADR-004]]), not TCP state.
- **TCP drops, UDP fine:** the current session keeps flying. New `ARM_CONTROL_REQUEST` and configuration changes are blocked, and the ground shows a warning.
- **UDP stops:** link failsafe, regardless of TCP.
- **New TCP connection:** a new session. Traffic from the old session is rejected.
- Exactly **one** TCP client at a time (ESP RAM).

## 3. Consequences

- Fewer spurious failsafes, and liveness has one clear owner.
- The ground must reconnect TCP to change configuration or re-arm control after a TCP drop.

## 4. Related pages

- [[PC_to_ESP]] §7.3–7.4 · [[ESP Link State Machine]] · [[Decision Register]]

## 5. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **TCP** | Transmission Control Protocol: reliable, ordered, connection-oriented transport. |
| **UDP** | User Datagram Protocol: connectionless transport suited to refreshed, time-sensitive data. |
