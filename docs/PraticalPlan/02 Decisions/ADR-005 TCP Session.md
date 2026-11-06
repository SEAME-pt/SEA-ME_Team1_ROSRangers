---
title: ADR-005 — TCP Session as Prerequisite for Control
type: adr
status: accepted
date: 2026-10-12
updated: 2026-11-06
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

> 2026-11-06: **The decision is extended: a new `HELLO` does not end the current session** (GitHub #156, decided by the team). The ESP answers it with `HELLO_ACK` and keeps the current session; the new session is *pending* and takes over only when its **first valid frame under the new keys** arrives. Then the ESP sends `BYE` reason 3 to the old session. A pending session with no valid frame within 2 s is dropped, and `HELLO` is rate-limited (at most one per 500 ms). Why: a `HELLO` is tagged with the PSK and carries nothing fresh, so a recorded one could be replayed at any time and end a live session in flight. A replayed `HELLO` cannot produce that first frame (no PSK, no new keys), so the live session survives. The same first exchange confirms the keys on the ground side: a ground that took a replayed old `HELLO_ACK` has the wrong keys, gets no answer to its first frame and redoes the handshake. No wire change. "New TCP connection: a new session" above now means: a TCP connection by itself never starts or ends a session; only a valid `HELLO` on it opens a *pending* session, and the switch waits for its first valid frame. **Reconnecting while the old socket still holds the one slot** (team, 2026-11-06): the ESP accepts **one extra TCP connection, only to carry a `HELLO`**, for at most 2 s (until the pending session is confirmed, which closes the old connection, or expires, which closes the extra one); and **TCP keep-alive** on the ESP frees a dead connection's slot, tuned to seconds (for example first probe after 5 s idle, 3 probes 1 s apart), far slower than the 250 ms UDP failsafe. Neither affects UDP or the failsafe: liveness is decided by UDP only (above). #102 checks the ESP8266 RAM budget for the extra socket. "Exactly one TCP client at a time" above now reads: one session connection, plus at most one `HELLO`-only connection for up to 2 s. If the ground re-handshakes on the connection it already has, `BYE` reason 3 goes out on that same connection, which stays open for the new session.

**Rate limit:** at most one accepted `HELLO` per 500 ms; a `HELLO` refused by the limit does not restart the window, and a later accepted `HELLO` replaces a pending session that is not confirmed yet. **Known limit:** someone replaying a recorded `HELLO` every 500 ms can keep replacing a real ground's pending session and so keep it from reconnecting. It can never end the live session (the goal of #156). If it happens after a link loss, the drone stays in LINK_FAILSAFE and the safety pilot takes over. Recorded in [[ESP Link State Machine]] and [[Protocol Spec v0]] §3.1.

## 5. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **HELLO / HELLO_ACK** | The handshake that opens a session over TCP ([[Protocol Spec v0]] §1). |
| **Pending session** | A new session after a `HELLO`, not yet confirmed by a valid frame under its keys; the current session keeps running meanwhile (#156). |
| **TCP** | Transmission Control Protocol: reliable, ordered, connection-oriented transport. |
| **UDP** | User Datagram Protocol: connectionless transport suited to refreshed, time-sensitive data. |
| **OD-xx / FC-xx / TF-xx** | Open-decision IDs in PC_to_ESP, ESP_to_FC and ThoneFlow. The Decision Register shows which ADR resolves each one. |
