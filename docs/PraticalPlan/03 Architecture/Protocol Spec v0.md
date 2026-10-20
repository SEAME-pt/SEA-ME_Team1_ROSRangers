---
title: Protocol Spec v0 (Ground ↔ ESP)
type: interface-spec
status: draft
version: 0.1
date: 2026-10-12
project: SEA autonomous drone
aliases:
  - Communication Message Specification
tags:
  - architecture
  - protocol
  - communication
---

# Protocol Spec v0 (Ground ↔ ESP)

> [!warning] Status: DRAFT v0.1. To be frozen in the week-3 design review.
> Implements [[ADR-001 Message Categories|ADR-001]], [[ADR-002 Message Integrity|ADR-002]], [[ADR-003 Sequence Acceptance|ADR-003]], [[ADR-005 TCP Session|ADR-005]] and [[ADR-008 Telemetry Transport|ADR-008]]. This is the "Communication Message Specification" suggested in [[PC_to_ESP]] (OD-02, OD-03, OD-08, OD-19). All integers are little-endian.

---

## 1. Header (20 bytes) + payload + tag (8 bytes)

| Off | Size | Field | Notes |
|---|---|---|---|
| 0 | 2 | magic | `0x53 0x44` ("SD"), fast reject |
| 2 | 1 | version | `1` |
| 3 | 1 | type | ranges in §2 |
| 4 | 1 | flags | bit 0 = ack requested (TCP only) |
| 5 | 1 | reserved | 0 |
| 6 | 2 | payload_len | ≤ 256. Also the **TCP framing** length (OD-03) |
| 8 | 4 | session_id | 0 only in `HELLO` |
| 12 | 4 | seq | per sender, per session, starts at 1 |
| 16 | 4 | t_send_us | sender monotonic µs, **for latency logging only** (OD-10). Freshness uses the ESP's receive clock. |
| 20 | n | payload | |
| 20+n | 8 | tag | HMAC-SHA256(session key, bytes 0..20+n-1), first 8 bytes |

## 2. Type ranges (five categories)

| Range | Category | Transport |
|---|---|---|
| 0x01–0x1F | Session / command | TCP |
| 0x20–0x3F | Configuration | TCP |
| 0x40–0x5F | Diagnostics (logs, events) | TCP |
| 0x60–0x7F | Control setpoints | UDP |
| 0x80–0x9F | Telemetry (periodic state) | UDP |

## 3. Messages v0

| Type | Name | Payload |
|---|---|---|
| 0x01 | HELLO | proto u8, ground_nonce[16] (tag uses the PSK) |
| 0x02 | HELLO_ACK | session_id u32, esp_nonce[16], fw_version[12] |
| 0x03 | ARM_CONTROL_REQUEST | mask u8 (expected override mask) |
| 0x04 | ARM_CONTROL_ACK | result u8 |
| 0x05 | RESUME_REQUEST | none |
| 0x06 | RESUME_ACK | result u8 |
| 0x07 | BYE | reason u8 |
| 0x20 / 0x21 | CONFIG_GET / CONFIG_SET | key u8, value i32 (rejected while control is enabled, OD-16) |
| 0x40 | EVENT | code u16, text[≤64] |
| 0x60 | SETPOINT | mode u8 (0 neutral, 1 attitude), roll i16, pitch i16, yaw_rate i16 (−1000..1000), throttle i16 (0..1000), flags u8 |
| 0x80 | STATE | esp_state u8, override_active u8, last_ctrl_seq u32, last_ctrl_t_send_us u32, roll i16 (0.1°), pitch i16 (0.1°), heading u16 (°), vbat u16 (0.01 V), flow_x i16, flow_y i16, flow_quality u8, flow_status u8, rx_rejected u16, msp_errors u16 |

`flow_status` uses the codes from [[ThoneFlow]] §8.1 (`OK`, `LOW_QUALITY`, `INVALID_FRAME`, `STALE`, `NO_DATA`). "No data" is never sent as zero motion (VAL-01).

## 4. Receive rules

1. Check length, magic and version, then the **tag** (constant time). On failure: drop and count `rx_rejected` (REQ-VAL-01 to 04).
2. `session_id` must equal the current session.
3. Control (0x60) is accepted only if `seq > last_ctrl_seq` ([[ADR-003 Sequence Acceptance|ADR-003]]). Otherwise drop and count.
4. Values are clamped. The ESP maps −1000..1000 → 1000..2000 µs, and throttle 0..1000 → 1000..2000 µs.

## 5. Command → RC channel mapping (resolves FC-08 in [[ESP_to_FC]])

`MSP_SET_RAW_RC` always carries **8 channels in AETR order**: `[roll, pitch, throttle, yaw, aux1..aux4]`.

| Channel | Source | Neutral |
|---|---|---|
| roll, pitch, yaw | SETPOINT, clamped | 1500 |
| throttle | SETPOINT (only used when masked, mask 15) | 1000 |
| aux1..aux4 | **never driven by the ground**. The ESP sends 1500. These channels are never in the mask. | 1500 |

Clamps apply on the ground **and** on the ESP, the last point under team control (FC-08).

## 6. Test vectors

Stored in `drone/tests/vectors/*.json` in the software repo. The C++ and Python implementations must both pass them ([[Testing Strategy]]).

## 7. Still open

- Port numbers (OD-04).
- `T_fresh` (OD-06). Initial: equal to `T_loss` = 250 ms.
- Configuration keys.

## 8. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **FC** | Flight Controller: the onboard controller responsible for stabilization and control. |
| **TCP** | Transmission Control Protocol: reliable, ordered, connection-oriented transport. |
| **Telemetry** | Periodic data sent from a system to report state, measurements, or health. |
| **UDP** | User Datagram Protocol: connectionless transport suited to refreshed, time-sensitive data. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **REQ** | Requirement ID of the form `REQ-<SYS, NAV, SEN, COM, CTL, SAF, PWR, SW or HW>-NNN`, used for traceability. |
| **OD-xx / FC-xx / TF-xx** | Open-decision IDs in PC_to_ESP, ESP_to_FC and ThoneFlow. The Decision Register shows which ADR resolves each one. |
