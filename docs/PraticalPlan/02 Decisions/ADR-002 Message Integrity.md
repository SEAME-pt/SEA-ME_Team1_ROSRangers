---
title: ADR-002 — Message Integrity
type: adr
status: accepted
date: 2026-10-12
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

## 4. Consequences

- The ESP CPU cost must be **measured** at the real control rate (acceptance threshold to be set in [[Testing Strategy]]).
- Key provisioning (OD-18) is partly resolved: build-time secret. Rotation is still open.
- Wi-Fi WPA2 stays mandatory ([[ADR-006 Network|ADR-006]]). The MAC does not replace it.

## 5. Related pages

- [[PC_to_ESP]] §6, §8 · [[Protocol Spec v0]] · [[Decision Register]]

## 6. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **CI** | Continuous Integration: automated validation of proposed changes. |
| **ESP8266** | The Wi-Fi microcontroller of the drone's ESP module (an ESP-12F). It bridges the ground PC to the FC over UART4. |
| **TCP** | Transmission Control Protocol: reliable, ordered, connection-oriented transport. |
| **UDP** | User Datagram Protocol: connectionless transport suited to refreshed, time-sensitive data. |
| **CRC** | Cyclic Redundancy Check: a checksum that detects accidental corruption (not tampering). |
| **OD-xx / FC-xx / TF-xx** | Open-decision IDs in PC_to_ESP, ESP_to_FC and ThoneFlow. The Decision Register shows which ADR resolves each one. |
