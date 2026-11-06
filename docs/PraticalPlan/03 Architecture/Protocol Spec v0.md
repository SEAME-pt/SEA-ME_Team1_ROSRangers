---
title: Protocol Spec v0 (Ground ↔ ESP)
type: interface-spec
status: accepted
version: 1.3
date: 2026-10-12
updated: 2026-11-05
project: SEA autonomous drone
aliases:
  - Communication Message Specification
tags:
  - architecture
  - protocol
  - communication
---

# Protocol Spec v0 (Ground ↔ ESP)

> [!success] Status: ACCEPTED v1.3. v1.0 was frozen at the design review of 2026-11-03 (Guilherme, Bruno, Paulo; GitHub #97). Changes from now on go through a dated entry in §8 or an ADR. The items left open by the review were decided on 2026-11-05 (GitHub #142, §7.5); one key per direction was added the same day (#150, §1).
> Implements [[ADR-001 Message Categories|ADR-001]], [[ADR-002 Message Integrity|ADR-002]], [[ADR-003 Sequence Acceptance|ADR-003]], [[ADR-005 TCP Session|ADR-005]] and [[ADR-008 Telemetry Transport|ADR-008]]. This is the "Communication Message Specification" suggested in [[PC_to_ESP]] (OD-02, OD-03, OD-08, OD-19). All integers are little-endian.

---

## 1. Header (20 bytes) + payload + tag (8 bytes)

| Off | Size | Field | Notes |
|---|---|---|---|
| 0 | 2 | magic | `0x53 0x44` ("SD"), fast reject |
| 2 | 1 | version | `1` |
| 3 | 1 | type | ranges in §2 |
| 4 | 1 | flags | reserved in v0: sent as 0, ignored (§7.5) |
| 5 | 1 | reserved | 0 |
| 6 | 2 | payload_len | ≤ 256. Also the **TCP framing** length (OD-03) |
| 8 | 4 | session_id | 0 only in `HELLO`. `HELLO_ACK` carries the new `session_id` in the header and in the payload |
| 12 | 4 | seq | per sender, per session, **per transport** (TCP and UDP each count on their own), starts at 1. `HELLO` and `HELLO_ACK` use 1 |
| 16 | 4 | t_send_us | sender monotonic µs, **for latency logging only** (OD-10). Freshness uses the ESP's receive clock. |
| 20 | n | payload | |
| 20+n | 8 | tag | HMAC-SHA256(key of this direction, bytes 0..20+n-1), first 8 bytes |

Session key ([[ADR-002 Message Integrity|ADR-002]]): `session key = HMAC-SHA256(PSK, "sess" || ground_nonce || esp_nonce)`. `HELLO` and `HELLO_ACK` are tagged with the PSK (the session key needs `esp_nonce`, which is inside `HELLO_ACK`). Before a session, the ESP accepts only `HELLO` and the ground only `HELLO_ACK`: any other type checked with the PSK is dropped (`reject:wrong_direction`), so a reflected `HELLO` or `HELLO_ACK` does nothing. A `HELLO` must have `session_id` 0; a `HELLO_ACK` must carry the same non-zero `session_id` in its header and payload (`reject:wrong_session` otherwise). Both use seq 1 (`reject:bad_seq` otherwise). Payload sizes are checked as in §3. Checked in this order: tag (with the PSK), type, payload size, `session_id`, seq. `HELLO` and `HELLO_ACK` are TCP types (§2): only frames from the TCP connection go to this check.

Random values: `ground_nonce` comes from the ground's operating-system random source, `esp_nonce` and `session_id` from the ESP8266 hardware random number generator (#101/#102). They must never repeat: a repeated pair of nonces gives the same keys, and a recorded session could then be replayed whole. Every `HELLO` (type 0x01) goes to this check, also during a session: a new `HELLO` replaces the session (§3.1, `BYE` reason 3), and the in-session receiver never accepts it.

Every later message is tagged with the key of its direction (2026-11-05, #150): `ground→ESP key = HMAC-SHA256(session key, "g2e")` and `ESP→ground key = HMAC-SHA256(session key, "e2g")`. Each side seals with its sending key and checks with its receiving key, so a frame sent back to its own sender fails the tag (`reject:bad_tag`). The session key itself tags nothing and is wiped once both keys are derived.

## 2. Type ranges (five categories)

| Range | Category | Transport |
|---|---|---|
| 0x01–0x1F | Session / command | TCP |
| 0x20–0x3F | Configuration | TCP |
| 0x40–0x5F | Diagnostics (logs, events) | TCP |
| 0x60–0x7F | Control setpoints | UDP |
| 0x80–0x9F | Telemetry (periodic state) | UDP |

A type that arrives on the other transport is rejected as `reject:wrong_transport`, with its own counter, counted in `rx_rejected`, after the type check and before rule 3. Both transports use the same direction keys and count `seq` apart, so without this check a frame captured on one transport could be replayed on the other (added 2026-11-05, #99).

Each side also accepts only the types the **other** side sends (2026-11-05, #152): the ESP receives `SETPOINT`, `ARM_CONTROL_REQUEST`, `RESUME_REQUEST`, `BYE`, `CONFIG_GET` and `CONFIG_SET`; the ground receives `STATE`, `ARM_CONTROL_ACK`, `RESUME_ACK`, `BYE`, `CONFIG_ACK` and `EVENT`. Any other type is `reject:wrong_direction`, with its own counter, counted in `rx_rejected`, after the transport check. The direction keys (§1) already refuse a node's own frames; this check still does if both ends got the keys wrong, except for `BYE`, which both sides send: a reflected `BYE` can then end the session (never inject control). The direction keys stop it.

## 3. Messages v0

| Type | Name | Bytes | Payload |
|---|---|---|---|
| 0x01 | HELLO | 17 | proto u8, ground_nonce[16] (tag uses the PSK) |
| 0x02 | HELLO_ACK | 32 | session_id u32, esp_nonce[16], fw_version[12] (tag uses the PSK) |
| 0x03 | ARM_CONTROL_REQUEST | 1 | mask u8 (expected override mask) |
| 0x04 | ARM_CONTROL_ACK | 1 | result u8 (§3.1) |
| 0x05 | RESUME_REQUEST | 0 | none |
| 0x06 | RESUME_ACK | 1 | result u8 (§3.1) |
| 0x07 | BYE | 1 | reason u8 (§3.1) |
| 0x20 / 0x21 | CONFIG_GET / CONFIG_SET | 5 | key u8, value i32 (ignored in `CONFIG_GET`, sent as 0). Keys in §7.3. `CONFIG_SET` is rejected while control is enabled (OD-16) |
| 0x22 | CONFIG_ACK | 6 | key u8, result u8 (§3.1), value i32 (the current value; after a refused `CONFIG_SET`, the unchanged one; 0 when there is no value, for example an unknown key). Reply to `CONFIG_GET` and `CONFIG_SET` |
| 0x40 | EVENT | 2–66 | code u16 (§3.1), text[≤64] |
| 0x60 | SETPOINT | 10 | mode u8 (0 neutral, 1 attitude), roll i16, pitch i16, yaw_rate i16 (−1000..1000), throttle i16 (0..1000), flags u8 (reserved in v0: sent as 0, ignored) |
| 0x80 | STATE | 28 | esp_state u8 (§3.1), override_active u8, last_ctrl_seq u32, last_ctrl_t_send_us u32, roll i16 (0.1°), pitch i16 (0.1°), heading u16 (°), vbat u16 (0.01 V), flow_x i16, flow_y i16, flow_quality u8, flow_status u8, rx_rejected u16, msp_errors u16 |

Bytes is the payload size (`EVENT`: 2 + text length, text ≤ 64). A payload of any other size is rejected as `reject:bad_payload_size`, a type not in this table as `reject:bad_type`. Each has its own counter and counts in `rx_rejected`. Both are checked after §4 rule 2 and before rule 3, in the order of §4 (type → transport → direction → size → `mode` → `seq`), so they change no state (REQ-COM-001, REQ-COM-004).

`flow_status` uses the codes from [[ThoneFlow]] §8.1 (`OK`, `LOW_QUALITY`, `INVALID_FRAME`, `STALE`, `NO_DATA`). "No data" is never sent as zero motion (VAL-01).

Every TCP request has its own reply: `HELLO` → `HELLO_ACK`, `ARM_CONTROL_REQUEST` → `ARM_CONTROL_ACK`, `RESUME_REQUEST` → `RESUME_ACK`, `CONFIG_GET` / `CONFIG_SET` → `CONFIG_ACK`. `BYE` ends the session and gets no reply. `EVENT` is sent by the ESP on its own.

### 3.1 Codes (2026-11-05, #142)

A published code keeps its meaning forever. A code no longer used is marked "retired" and its number is never given to something else. Adding a code is free; changing one needs a dated entry in §8.

`STATE.esp_state` ([[ESP Link State Machine]]). A stale setpoint (`T_fresh`) is an event, not a state.

| Value | State |
|---|---|
| 0 | BOOT |
| 1 | NO_SESSION |
| 2 | SESSION_IDLE |
| 3 | STREAMING |
| 4 | LINK_FAILSAFE |

`ARM_CONTROL_ACK.result` and `RESUME_ACK.result` share one list, so a number means the same in both.

| Value | Meaning | Sent in | Example |
|---|---|---|---|
| 0 | OK | both | `ARM`: control is on. `RESUME`: back to `STREAMING` |
| 1 | wrong state | both | `ARM_CONTROL_REQUEST` while `STREAMING` or in `LINK_FAILSAFE` (which needs `RESUME_REQUEST`); `RESUME_REQUEST` outside `LINK_FAILSAFE` |
| 2 | override mask mismatch | `ARM_CONTROL_ACK` | The ground expects mask 15 (throttle included) but the drone is set up for 7 ([[ADR-010 MSP Override\|ADR-010]]). The ground must not fly |
| 3 | resume condition not met | `RESUME_ACK` | Fewer than 500 ms of neutral `SETPOINT` before the request ([[ESP Link State Machine]]) |

`CONFIG_ACK.result`. A `CONFIG_SET` is checked in this order, and the first refusal that applies is returned, so both implementations give the same answer: **5** (state, OD-16), then 1, 2, 3, 4 (per key). The ground hears "not while streaming" whatever the key or value. `CONFIG_GET` skips the state check.

| Value | Meaning | When |
|---|---|---|
| 0 | OK | Any valid `CONFIG_GET`, or a valid `CONFIG_SET` on a Tunable key |
| 1 | unknown key | Key not in the §7.3 table, including `0xF0–0xFF` on a normal build |
| 2 | read-only | `CONFIG_SET` on an Info key (`0x01–0x3F`) |
| 3 | safety parameter, firmware only | `CONFIG_SET` on a Safety key (`0x40–0x7F`) |
| 4 | out of range | `CONFIG_SET` with a value outside the key's range (never clamped, §7.3 rule 4) |
| 5 | refused while control is enabled | Any `CONFIG_SET` while `STREAMING` (OD-16) |

Info and Safety keys are both read-only from the ground, but they get separate codes so the log tells which protection refused the write without a key table: an Info key can never change, a Safety key only changes in firmware.

`BYE.reason` (either side can send it).

| Value | Meaning | Example |
|---|---|---|
| 0 | normal | Operator stops the ground node |
| 1 | timeout | No traffic from the other side for too long |
| 2 | protocol error | The other side sent invalid input (§4 rule 5) |
| 3 | replaced by a new session | A new `HELLO` arrived; the ESP tells the old session it has ended |

`EVENT.code` (u16): the high byte is the subsystem, the low byte the event, so a log filter on `0x01xx` shows all link events.

| Range | Subsystem | v0 codes |
|---|---|---|
| `0x00xx` | System (boot, watchdog, memory) | defined with the gateway firmware (#101) |
| `0x01xx` | Link | `0x0101` setpoint stale (`T_fresh` expired, neutral sticks), `0x0102` `LINK_FAILSAFE` entered, `0x0103` resumed |
| `0x02xx` | FC / MSP | defined with the gateway firmware (#101) |

## 4. Receive rules

Order of the checks: rule 1, rule 2, message type, transport (§2), direction (§2), payload size (§3), `mode` (rule 4), then rule 3. A frame refused before rule 3 changes no state.

1. Check, in this order: the minimum length (header + tag, else truncated), magic, version, then `payload_len` ≤ 256 and the exact length (shorter than header + `payload_len` + tag: truncated; `payload_len` > 256 or extra bytes after the tag: bad length), then the **tag** (constant time). `payload_len` is not trusted before magic and version have passed. On failure: drop and count `rx_rejected` (REQ-COM-001 to 004, old IDs REQ-VAL-01 to 04).
2. `session_id` must equal the current session.
3. A message is accepted only if `seq` is greater than the last one accepted from that sender **on the same transport** ([[ADR-003 Sequence Acceptance|ADR-003]]). Control uses `last_ctrl_seq`. Otherwise drop and count. `seq` 0 is never accepted.
4. Range check (REQ-COM-002): the clamps are the v0 range check. Values are clamped; the ESP maps −1000..1000 → 1000..2000 µs as `1500 + v/2` (integer division, rounding toward 1500), and throttle 0..1000 → 1000..2000 µs. A `SETPOINT` with `mode` other than 0 or 1 is rejected as `reject:bad_mode`, with its own counter, and counted in `rx_rejected`. The `mode` check runs **before rule 3**, so a rejected `SETPOINT` does not move `last_ctrl_seq` and changes no state (REQ-COM-004). The maximum tilt stays in Betaflight (`angle_limit`), not in the protocol.
5. Malformed TCP input (REQ-COM-019): a bad tag is dropped and counted, and the connection stays open. A bad magic, version or length closes the TCP connection, because the framing can no longer be trusted. On TCP a frame shorter than its `payload_len` says is not yet complete: the receiver waits for more bytes, it is not "truncated". An unknown type or a wrong payload size (§3) is dropped and counted, and the connection stays open: the framing is intact. None of these triggers the failsafe on its own: only `T_loss` does (§7.2).

## 5. Command → RC channel mapping (resolves FC-08 in [[ESP_to_FC]])

`MSP_SET_RAW_RC` always carries **8 channels in AETR order**: `[roll, pitch, throttle, yaw, aux1..aux4]`.

| Channel | Source | Neutral |
|---|---|---|
| roll, pitch, yaw | SETPOINT, clamped | 1500 |
| throttle | SETPOINT (only used when masked, mask 15) | 1000 |
| aux1..aux4 | **never driven by the ground**. The ESP sends 1500. These channels are never in the mask. | 1500 |

Clamps apply on the ground **and** on the ESP, the last point under team control (FC-08).

In the C++ library, `setpoint_to_rc` (#99) does only the clamp and map step. Choosing neutral for `mode 0`, a stale setpoint or an unmasked throttle is the link state machine's job (#102).

## 6. Test vectors

Stored in `drone/tests/vectors/*.json` in the software repo (format in `drone/tests/vectors/README.md`; the files are generated by `drone/tests/vectors/generate.py`). The C++ (#99) and Python (#100) implementations must both pass every vector in CI ([[Testing Strategy]]). List agreed at the 2026-11-03 review:

| Group | Cases | Proves |
|---|---|---|
| 1 Valid frames | One per message type in §3 | Both sides encode and decode the same bytes |
| 2 HMAC | Tag with the PSK (`HELLO`) and with each direction key; a reflected frame (sealed for the other direction); one flipped payload bit; one flipped tag bit | Any changed bit, and any reflection, is rejected |
| 3 Header | Wrong magic, wrong version, `payload_len` > 256, length mismatch, truncated frame; unknown type (`bad_type`) and wrong payload size for the type (`bad_payload_size`, added 2026-11-05) | Garbage is rejected before it is read (§4 rule 1, §3 sizes) |
| 4 Session | Wrong `session_id`; `session_id` 0 outside `HELLO` | Other sessions are rejected (§4 rule 2) |
| 5 Sequence | In order, duplicate, older, gap (accepted, counted), `seq` 0, `seq ≥ 2^31` | Anti-replay ([[ADR-003 Sequence Acceptance\|ADR-003]]) |
| 6 Clamps and mapping | −1000 / 0 / +1000 → 1000 / 1500 / 2000 µs, out of range clamped, throttle 0..1000, aux always 1500, `mode` 2 rejected (`reject:bad_mode`) and then a valid `SETPOINT` with the same `seq` accepted | The RC channels sent to the FC (§4–5) |
| 7 Configuration (added 2026-11-05) | One `CONFIG_ACK` encoding per result 0–5 | Config replies (§3.1). The check order (state first) and the replies per key group need the ESP state and key table, so they are tested in the link state machine (#102), not in the shared vectors |
| 8 Reserved bits and transports (added 2026-11-05) | Header `flags` bit 0 set and `SETPOINT.flags` ≠ 0 are accepted and ignored; TCP and UDP `seq` counted apart; `HELLO_ACK` tagged with the PSK; a frame on the wrong transport or from the wrong side refused | The 2026-11-05 decisions (§7.5, §2) |
| 9 Handshake (added 2026-11-05, #152) | The ESP accepts `HELLO`, the ground `HELLO_ACK`; each refuses the other's frame and any other PSK-tagged type; wrong `session_id` and seq ≠ 1 refused | The pre-session rule (§1) |

- A vector is one fixed example: `name`, `key`, `fields`, `bytes` (hex) and `expect` (`accept` or `reject:<reason>`). Groups 5, 6 and 8 list several steps; group 6 also lists the expected channels; group 9 says which side (`handshake`) checks the frame.
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
| `0x80` | `telemetry_rate_hz` | Hz | 1–50 | 25 | RW |
| `0x81` | `event_level` | – | 0–3 | 1 | RW |

`telemetry_rate_hz` fixes the v0 telemetry rate ([[ADR-008 Telemetry Transport|ADR-008]]: 20–50 Hz). Default 25 Hz since 2026-11-05 (#142, was 20): the rate at which the ESP polls `MSP_ATTITUDE` ([[ADR-009 ESP to FC Link|ADR-009]]), so every attitude reading is sent once. No firmware shipped the old default, so `config_table_version` stays 1.

### 7.4 Implementation of the port changes

The diagnostic builds and their scripts still use the old ports (timing 5761, TCP echo 5762, watchdog 5763). They move to 5770, 5759 and 5771 in #101. Recorded bench results keep the ports they were measured with.

### 7.5 Items decided on 2026-11-05 (GitHub #142)

Left open by the review for lack of time, decided in writing on #142. Applied in §1, §3, §4, §6 and §7.3.

| Item | Decision |
|---|---|
| Reply to `CONFIG_GET` / `CONFIG_SET` (REQ-COM-018) | New `0x22 CONFIG_ACK`: key u8, result u8, value i32. Results in §3.1, with Info (2) and Safety (3) refusals told apart |
| Header `flags` bit 0 "ack requested" | Removed in v0: reserved, sent as 0, ignored. Every request already has its own reply (§3) |
| `result`, `BYE.reason`, `EVENT.code` values | §3.1. Event codes grouped by range, never reused |
| `STATE.esp_state` values | 0 BOOT, 1 NO_SESSION, 2 SESSION_IDLE, 3 STREAMING, 4 LINK_FAILSAFE. A stale setpoint is an event, not a state |
| `SETPOINT.flags` | Reserved in v0: sent as 0, ignored |
| Key that tags `HELLO_ACK` | The PSK (§1, ADR-002 entry of 2026-11-05) |
| `seq` | One counter per sender, per session, per transport, from 1. `HELLO` and `HELLO_ACK` use 1. Only `seq > last seen` on that transport is accepted (§4 rule 3, ADR-003 entry of 2026-11-05) |
| Range limits (REQ-COM-002) | §4 clamps are the v0 range check; `mode` other than 0 or 1 rejected (`bad_mode`) before the sequence check, so it changes no state; max tilt stays in Betaflight (`angle_limit`) (§4 rule 4) |
| Repeated malformed TCP input (REQ-COM-019) | Bad tag: drop and count. Bad magic, version or length: close the TCP connection. Never triggers the failsafe on its own (§4 rule 5) |
| Wi-Fi re-association (REQ-COM-023) | To be settled with the gateway firmware (#101). Still open, tracked in [[Open Questions]] |
| `telemetry_rate_hz` default | 25 Hz, not 20 (§7.3) |

Still open, found while writing the C++ library (#99), tracked in GitHub #149 and [[Open Questions]]: whether `STATE.rx_rejected` (u16) saturates or wraps and whether a frame with no session open counts in it, and how the per-reason and ADR-003 counters reach the ground.

## 8. Changelog

| Date | Version | Change |
|---|---|---|
| 2026-10-12 | 0.1 | Draft. |
| 2026-11-03 | 1.0 | Design review (Guilherme, Bruno, Paulo; #97). Accepted: ports (§7.1), `T_fresh` = 150 ms and "no command is not no link" (§7.2), configuration keys (§7.3), test vector list (§6). Session key formula copied from ADR-002 (§1). Open items listed in §7.5. |
| 2026-11-05 | 1.1 | Open items decided on GitHub #142 (§7.5): `CONFIG_ACK` (0x22), code lists (§3.1), header and `SETPOINT` flags reserved, `HELLO_ACK` tagged with the PSK, `seq` per transport, range check with `bad_mode` checked before the sequence window, `CONFIG_SET` state check before the per-key checks, and malformed TCP input (§4 rules 3–5), `telemetry_rate_hz` default 25 Hz. Payload sizes per type, `bad_type` and `bad_payload_size` (§3), `HELLO_ACK` header `session_id` (§1), `CONFIG_ACK.value` and `CONFIG_GET.value` (§3). Test vector groups 7 and 8 added, group 3 extended (§6). Wi-Fi re-association left to #101. |
| 2026-11-05 | 1.1.1 | From the C++ library (#99). New receive check: `reject:wrong_transport` (§2), found by the #99 review, so a frame cannot be replayed on the other transport. Clarifications, no wire change: length rule (§4 rule 1), `seq` 0 never accepted (§4 rule 3, §6 group 5), check order and TCP partial frames (§4 rules 1 and 5), mapping rounding (§4 rule 4), library scope (§5), vectors README (§6). Counter reporting left open in #149 (§7.5). |
| 2026-11-05 | 1.2 | One key per direction (#150, decided by the team; [[ADR-002 Message Integrity\|ADR-002]] entry): in-session tags use the `"g2e"` / `"e2g"` keys (§1), so a reflected frame is refused. Pre-session rule (§1): the ESP accepts only `HELLO` and the ground only `HELLO_ACK`; **not enforced in code yet** (#152, before #101/#102). **Wire change:** the tag of every in-session frame changes; no firmware had shipped. Group 2: `setpoint_signed_with_session_key` replaced by one vector per direction and one reflected frame per direction (§6). |
| 2026-11-05 | 1.3 | The rules of v1.2 are enforced in code (#152): each side accepts only the types the other side sends (`reject:wrong_direction`, §2); the pre-session checks of `HELLO` / `HELLO_ACK` (§1: `wrong_direction`, `wrong_session`, `bad_seq`). The C++ library derives only the direction keys (`derive_keys`, the session key is wiped inside). Vector group 9 and one more group 8 vector (§6). Also stated: `HELLO` / `HELLO_ACK` are checked only from TCP, the nonces and `session_id` come from a hardware or OS random source and never repeat (§1), and a reflected `BYE` is the one exception to the direction check (§2). |

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
| **Result / reason code** | Number in a reply (`result`, `reason`, `code`) that says why something happened, so the ground can act on it and log it (§3.1). Never reused. |
| **Configuration key** | One-byte number in `CONFIG_GET` / `CONFIG_SET` that names one setting on the ESP (§7.3). |
| **Test vector** | One fixed example (fields, exact bytes, expected result) that both the C++ and the Python implementation must pass (§6). |
| **SITL** | Software In The Loop: the flight controller firmware running as a program on the PC (Betaflight SITL uses TCP 5761+). |
| **SoftAP** | The Wi-Fi access point run by the ESP8266 itself ([[ADR-006 Network\|ADR-006]]). |
| **Saturate / wrap** | What a counter does at its maximum: stay there, or restart from 0. Open for `STATE.rx_rejected` (§7.5, #149). |
| **Session key** | `HMAC-SHA256(PSK, "sess" \|\| ground_nonce \|\| esp_nonce)`, from which the two direction keys are derived. It tags nothing and is wiped after that (§1, [[ADR-002 Message Integrity\|ADR-002]]). |
| **Direction key** | The key that tags frames going one way: ground→ESP (`"g2e"`) or ESP→ground (`"e2g"`) (§1, #150). |
| **Reflection** | Sending a node one of its own frames back. Refused because each direction has its own key (§1), and each side accepts only what the other sends (§2). |
| **Role** | Which side a receiver belongs to, ESP or ground. It decides which message types are accepted (§2). |
| **AETR** | Raw receiver channel order: roll (A), pitch (E), throttle (T), yaw (R), then AUX. |
