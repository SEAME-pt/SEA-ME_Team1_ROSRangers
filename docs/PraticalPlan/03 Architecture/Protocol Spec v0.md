---
title: Protocol Spec v0 (Ground ↔ ESP)
type: interface-spec
status: accepted
version: 1.0
date: 2026-10-12
updated: 2026-11-03
project: SEA autonomous drone
aliases:
  - Communication Message Specification
tags:
  - architecture
  - protocol
  - communication
---

# Protocol Spec v0 (Ground ↔ ESP)

> [!success] Status: ACCEPTED v1.0, frozen at the design review of 2026-11-03 (Guilherme, Bruno, Paulo; GitHub #97). Changes from now on go through a dated entry in §8 or an ADR. The items still open are listed in §7.5.
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

Session key ([[ADR-002 Message Integrity|ADR-002]]): `session key = HMAC-SHA256(PSK, "sess" || ground_nonce || esp_nonce)`. `HELLO` is tagged with the PSK.

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
| 0x20 / 0x21 | CONFIG_GET / CONFIG_SET | key u8, value i32. Keys in §7.3. `CONFIG_SET` is rejected while control is enabled (OD-16) |
| 0x40 | EVENT | code u16, text[≤64] |
| 0x60 | SETPOINT | mode u8 (0 neutral, 1 attitude), roll i16, pitch i16, yaw_rate i16 (−1000..1000), throttle i16 (0..1000), flags u8 |
| 0x80 | STATE | esp_state u8, override_active u8, last_ctrl_seq u32, last_ctrl_t_send_us u32, roll i16 (0.1°), pitch i16 (0.1°), heading u16 (°), vbat u16 (0.01 V), flow_x i16, flow_y i16, flow_quality u8, flow_status u8, rx_rejected u16, msp_errors u16 |

`flow_status` uses the codes from [[ThoneFlow]] §8.1 (`OK`, `LOW_QUALITY`, `INVALID_FRAME`, `STALE`, `NO_DATA`). "No data" is never sent as zero motion (VAL-01).

## 4. Receive rules

1. Check length, magic and version, then the **tag** (constant time). On failure: drop and count `rx_rejected` (REQ-COM-001 to 004, old IDs REQ-VAL-01 to 04).
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

Stored in `drone/tests/vectors/*.json` in the software repo. The C++ (#99) and Python (#100) implementations must both pass every vector in CI ([[Testing Strategy]]). List agreed at the 2026-11-03 review:

| Group | Cases | Proves |
|---|---|---|
| 1 Valid frames | One per message type in §3 | Both sides encode and decode the same bytes |
| 2 HMAC | Tag with the PSK (`HELLO`) and with the session key; one flipped payload bit; one flipped tag bit | Any changed bit is rejected |
| 3 Header | Wrong magic, wrong version, `payload_len` > 256, length mismatch, truncated frame | Garbage is rejected before it is read (§4 rule 1) |
| 4 Session | Wrong `session_id`; `session_id` 0 outside `HELLO` | Other sessions are rejected (§4 rule 2) |
| 5 Sequence | In order, duplicate, older, gap (accepted, counted), `seq ≥ 2^31` | Anti-replay ([[ADR-003 Sequence Acceptance\|ADR-003]]) |
| 6 Clamps and mapping | −1000 / 0 / +1000 → 1000 / 1500 / 2000 µs, out of range clamped, throttle 0..1000, aux always 1500 | The RC channels sent to the FC (§4–5) |

- A vector is one fixed example: `name`, `key`, `fields`, `bytes` (hex) and `expect` (`accept` or `reject:<reason>`). Groups 5 and 6 list several steps or the expected channels.
- The test key is 32 bytes `00 01 … 1F`. It is committed and used only for tests, never the real PSK.
- Adding a vector is free. Changing or deleting one changes the protocol and needs a dated entry in §8.

## 7. Decisions of the 2026-11-03 review

Attendees: Guilherme, Bruno, Paulo. These close the three items that were open in v0.1 (OD-04, OD-06, configuration keys).

### 7.1 Ports (OD-04, 11:10)

Static IPs are set by [[ADR-006 Network|ADR-006]].

| Port | Use | Status |
|---|---|---|
| UDP 5759 | Control and telemetry (told apart by `type`) | v0 |
| TCP 5759 | Session | v0 |
| 5760 | Reserved for a future channel. Until the gateway firmware (#101) replaces it, this is the transparent bridge's port | reserved |
| TCP 5761–5768 | Betaflight SITL on the PC (UART1–8). Never used | taken |
| UDP 5770 | Timing test, diagnostic build only | test |
| UDP 5771 | Watchdog test, diagnostic build only | test |

- 5759 and not 5760: the transparent bridge forwards every byte on UDP 5760 to the FC. Protocol traffic sent to a drone that still runs the bridge then gets no answer instead of reaching the FC.
- New data is first a new message `type` on these ports. A new port only for a separate program or transport, and a new UDP port must fit the ESP's limit of 4 UDP sockets (`MEMP_NUM_UDP_PCB`).

### 7.2 Freshness and link loss (OD-06, 11:12)

| Timer | Value | Counted from | On expiry | Recovery |
|---|---|---|---|---|
| `T_fresh` | **150 ms** | Last accepted `SETPOINT` (ESP receive clock, OD-10) | Neutral sticks: roll/pitch/yaw 1500, throttle not taken from the ground. The drone levels in Angle mode | Next valid `SETPOINT` is used at once |
| `T_loss` | **250 ms** (unchanged, [[ADR-004 ESP Failsafe\|ADR-004]]) | Last accepted control message | `LINK_FAILSAFE` | Only through `RESUME_REQUEST` ([[ESP Link State Machine]]) |

- "No command" is not "no link". The ground sends `SETPOINT` at a fixed 50 Hz at all times. With no gesture it sends `mode 0` (neutral). So an operator who does not gesture never triggers the failsafe; only real link loss does.
- Liveness comes from accepted UDP control messages, never from the Wi-Fi or TCP state ([[ADR-005 TCP Session|ADR-005]], REQ-COM-016). There is no separate heartbeat message in v0: the setpoint stream is the heartbeat.
- Why 150 ms: silences per 10 min at 50 Hz ([[ESP Bench Test Plan]] §4).

| Run | ≥ 100 ms | ≥ 150 ms | ≥ 200 ms | ≥ 250 ms |
|---|---|---|---|---|
| Close range, ESP-S1-05 (3 runs) | 15–19 | 2–4 | 0–1 | 0 |
| Close + TCP load, ESP-S1-07 (3 runs) | 28–37 | 0–4 | 0–1 | 0 |
| Cage, ESP-S1-09 runs 1 and 3 | 94–103 | 20–24 | 4–6 | 0–1 |
| Cage, ESP-S1-09 run 2 (worst case) | 205 | 66 | 28 | 11 |

### 7.3 Configuration keys (11:28)

`key` is one byte. Its range says what the key is and who may change it.

| Range | Group | Access |
|---|---|---|
| `0x00` | Invalid, never used | – |
| `0x01–0x3F` | Info (versions, IDs) | Read-only |
| `0x40–0x7F` | Safety parameters (timeouts, limits) | Read-only from the ground, set in firmware only |
| `0x80–0xBF` | Tunable (rates, logging) | Read and write |
| `0xC0–0xEF` | Reserved. A new group is defined here by a dated entry in §8 | – |
| `0xF0–0xFF` | Diagnostic builds only | Test firmware only |

Rules:

1. Every key belongs to a range. No key outside a defined range.
2. A key number is never reused or renumbered. An unused key is marked "retired" and keeps its number.
3. An unknown key is answered with "unknown key", so a newer ground can find out what an older firmware supports.
4. Writes out of range are refused, never quietly clamped.
5. No writes while control is enabled (OD-16).
6. No saving across reboots in v0: after a reboot the ESP is back to its defaults.
7. Every key is documented: number, name with its unit (`_ms`, `_hz`), range, default, access, version added.
8. Adding a key = next free number in its range + a new row below + `config_table_version` + 1. No protocol change.
9. More than 256 keys would need a two-byte key, which is a new protocol version.

v0 keys:

| Key | Name | Unit | Range | Default | Access |
|---|---|---|---|---|---|
| `0x01` | `config_table_version` | – | – | 1 | RO |
| `0x02` | `proto_version` | – | – | 1 | RO |
| `0x40` | `t_loss_ms` | ms | – | 250 | RO |
| `0x41` | `t_fresh_ms` | ms | – | 150 | RO |
| `0x42` | `setpoint_limit` | ± units | – | 1000 | RO |
| `0x80` | `telemetry_rate_hz` | Hz | 1–50 | 20 | RW |
| `0x81` | `event_level` | – | 0–3 | 1 | RW |

`telemetry_rate_hz` fixes the v0 telemetry rate ([[ADR-008 Telemetry Transport|ADR-008]]: 20–50 Hz).

### 7.4 Implementation of the port changes

The diagnostic builds and their scripts still use the old ports (timing 5761, TCP echo 5762, watchdog 5763). They move to 5770, 5759 and 5771 in #101. Recorded bench results keep the ports they were measured with.

### 7.5 Still open after the review

Found while preparing the review, not discussed for lack of time. Each has a suggested answer in the follow-up issue and is tracked in [[Open Questions]]. Do not assume them until a dated entry in §8 records the decision.

| Item | Suggested answer |
|---|---|
| Reply to `CONFIG_GET` / `CONFIG_SET` (REQ-COM-018) | New `0x22 CONFIG_ACK`: key u8, result u8 (0 OK, 1 unknown key, 2 read-only, 3 out of range, 4 refused while control is enabled), value i32 |
| Header `flags` bit 0 "ack requested" | Remove in v0: reserved, sent as 0, ignored |
| `result`, `BYE.reason`, `EVENT.code` values | Short numbered lists; event codes grouped by range, never reused |
| `STATE.esp_state` values | 0 BOOT, 1 NO_SESSION, 2 SESSION_IDLE, 3 STREAMING, 4 LINK_FAILSAFE |
| `SETPOINT.flags` | Reserved in v0: sent as 0, ignored |
| Key that tags `HELLO_ACK` | The PSK |
| `seq` on TCP | One counter per sender, session and transport, from 1; only `seq > last seen` accepted |
| Range limits (REQ-COM-002) | §4 clamps are the range check; unknown `mode` rejected; max tilt stays in Betaflight |
| Repeated malformed TCP input (REQ-COM-019) | Bad tag: drop and count. Bad magic, version or length: close the TCP connection |
| Wi-Fi re-association (REQ-COM-023) | Settle with the gateway firmware (#101) |

## 8. Changelog

| Date | Version | Change |
|---|---|---|
| 2026-10-12 | 0.1 | Draft. |
| 2026-11-03 | 1.0 | Design review (Guilherme, Bruno, Paulo; #97). Accepted: ports (§7.1), `T_fresh` = 150 ms and "no command is not no link" (§7.2), configuration keys (§7.3), test vector list (§6). Session key formula copied from ADR-002 (§1). Open items listed in §7.5. |

## 9. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **FC** | Flight Controller: on this project the OddityRC F7 AIO (STM32F722, Betaflight target `ODDITYRCF7AIO`) of the 2.5" drone. It stabilises the aircraft and takes MSP from the ESP on UART4. |
| **TCP** | Transmission Control Protocol: reliable, ordered, connection-oriented transport. |
| **Telemetry** | Periodic data sent from a system to report state, measurements, or health. |
| **UDP** | User Datagram Protocol: connectionless transport suited to refreshed, time-sensitive data. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **REQ** | Requirement ID of the form `REQ-<SYS, NAV, SEN, COM, CTL, SAF, PWR, SW or HW>-NNN`, used for traceability. |
| **OD-xx / FC-xx / TF-xx** | Open-decision IDs in PC_to_ESP, ESP_to_FC and ThoneFlow. The Decision Register shows which ADR resolves each one. |
| **HMAC** | Hash-based Message Authentication Code: a keyed tag that proves a message came from someone holding the key (ADR-002 uses a truncated HMAC-SHA256). |
| **PSK** | Pre-shared key: a secret agreed in advance (Wi-Fi password, HMAC key). Never committed. |
| **T_loss** | The ESP link-failsafe timeout: no accepted control message for this long enters LINK_FAILSAFE (initial 250 ms, ADR-004). |
| **T_fresh** | Maximum age of the last accepted setpoint (150 ms, §7.2). Older → neutral sticks until the next valid setpoint; no failsafe. |
| **LINK_FAILSAFE** | ESP state after `T_loss` without an accepted control message. Left only through `RESUME_REQUEST` ([[ESP Link State Machine]]). |
| **Configuration key** | One-byte number in `CONFIG_GET` / `CONFIG_SET` that names one setting on the ESP (§7.3). |
| **Test vector** | One fixed example (fields, exact bytes, expected result) that both the C++ and the Python implementation must pass (§6). |
| **SITL** | Software In The Loop: the flight controller firmware running as a program on the PC (Betaflight SITL uses TCP 5761+). |
| **SoftAP** | The Wi-Fi access point run by the ESP8266 itself ([[ADR-006 Network\|ADR-006]]). |
| **AETR** | Raw receiver channel order: roll (A), pitch (E), throttle (T), yaw (R), then AUX. |
