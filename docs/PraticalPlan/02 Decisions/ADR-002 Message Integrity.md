---
title: ADR-002 — Message Integrity
type: adr
status: accepted
date: 2026-10-12
updated: 2026-11-05
project: SEA autonomous drone
aliases:
  - D02
  - D02 Message Integrity
tags:
  - adr
  - communication
  - security
---

# ADR-002 — Message Integrity

> [!success] Status: ACCEPTED (team, 2026-10-12)
> Resolves **OD-11** in [[PC_to_ESP]] §12. It supersedes the earlier "CRC for bench, revisit MAC before flight" recommendation.

## 1. Context

- UDP has no source authenticity. Anyone on the network, or anyone holding the Wi-Fi key, can inject datagrams ([[PC_to_ESP]] §8.1).
- UDP and Wi-Fi already detect accidental corruption, so a CRC adds little protection.
- Module 3 of the program requires the fleet to "authenticate, encrypt and sign".
- Adding a MAC later means changing the wire format, the golden test vectors and both implementations.

## 2. Options considered

| Option | Pros | Cons |
|---|---|---|
| CRC-16/32 only | Trivial, no keys | Forgery possible. Little gain over UDP/Wi-Fi checks. |
| **HMAC-SHA256, truncated to 8 bytes** | Authenticates the sender. Standard. Available in BearSSL (ESP8266 Arduino core) and in Python `hmac`. | Key handling. Some ESP CPU time. |
| AEAD (ChaCha20-Poly1305) | Also encrypts the payload | More complex. WPA2 already encrypts the air link. |

## 3. Decision

Every UDP and TCP message carries an **8-byte truncated HMAC-SHA256 tag** over header + payload, **from the first bench test**.

- A pre-shared key (PSK) is injected at build time from a secret. It is **never committed** (CI uses a GitHub secret).
- Session key = `HMAC(PSK, "sess" || ground_nonce || esp_nonce)`, derived in the [[ADR-005 TCP Session|session handshake]].
- The session ID is covered by the tag, so packets from older sessions are rejected.
- The tag is compared in constant time.
- Since 2026-11-05 (#150) the tag key of an in-session frame is a direction key, not the session key itself (entry below).

## 4. Consequences

- The ESP CPU cost must be **measured** at the real control rate (acceptance threshold to be set in [[Testing Strategy]]).
- Key provisioning (OD-18) is partly resolved: build-time secret. Rotation is still open.
- Wi-Fi WPA2 stays mandatory ([[ADR-006 Network|ADR-006]]). The MAC does not replace it.

## 5. Related pages

- [[PC_to_ESP]] §6, §8 · [[Protocol Spec v0]] · [[Decision Register]]

> 2026-11-05: `HELLO` and `HELLO_ACK` are tagged with the PSK. The session key needs `esp_nonce`, which is inside `HELLO_ACK`, and the tag is checked before the payload is read. Every later message uses the session key. Decided on GitHub #142, recorded in [[Protocol Spec v0]] §7.5. The decision is unchanged. (Superseded in part by the next entry: since #150 later messages use a direction key derived from the session key.)

> 2026-11-05: **The decision is extended: one key per direction** (GitHub #150, decided by the team). After the handshake, ground→ESP frames use `HMAC-SHA256(session key, "g2e")` and ESP→ground frames `"e2g"` (details in [[Protocol Spec v0]] §1); the session key itself tags nothing. Why: with one key for both directions, a node accepted its own frames sent back to it (reflection). One reflected `SETPOINT` could push the ground's window past every real `STATE` for the rest of the session, and could hide a real link loss. Separate keys make a reflected frame fail the tag, for every message type, and an implementation that gets it wrong refuses everything instead of letting reflections through. Cost: two extra HMACs once per session and 32 bytes of RAM; nothing extra per frame (the per-frame HMAC cost of §4 is unchanged). Recorded in [[Protocol Spec v0]] §1 (v1.2).

## 6. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **CI** | Continuous Integration: automated validation of proposed changes. |
| **ESP8266** | The Wi-Fi microcontroller of the drone's ESP module (an ESP-12F). It bridges the ground PC to the FC over UART4. |
| **TCP** | Transmission Control Protocol: reliable, ordered, connection-oriented transport. |
| **UDP** | User Datagram Protocol: connectionless transport suited to refreshed, time-sensitive data. |
| **CRC** | Cyclic Redundancy Check: a checksum that detects accidental corruption (not tampering). |
| **MAC** | Message Authentication Code: a keyed tag that proves a message is intact and from a key holder (here a truncated HMAC-SHA256). |
| **HMAC** | Hash-based Message Authentication Code: a keyed tag that proves a message came from someone holding the key. |
| **PSK** | Pre-shared key: a secret agreed in advance (Wi-Fi password, HMAC key). Never committed. |
| **Nonce** | A random number used once per session (`ground_nonce`, `esp_nonce`), so every session gets a different session key. |
| **Session key** | `HMAC-SHA256(PSK, "sess" \|\| ground_nonce \|\| esp_nonce)`, from which the two direction keys are derived ([[Protocol Spec v0]] §1). |
| **Direction key** | The key that tags frames going one way: ground→ESP (`"g2e"`) or ESP→ground (`"e2g"`) ([[Protocol Spec v0]] §1, #150). |
| **Reflection** | Sending a node one of its own frames back. Refused because each direction has its own key. |
| **OD-xx / FC-xx / TF-xx** | Open-decision IDs in PC_to_ESP, ESP_to_FC and ThoneFlow. The Decision Register shows which ADR resolves each one. |
