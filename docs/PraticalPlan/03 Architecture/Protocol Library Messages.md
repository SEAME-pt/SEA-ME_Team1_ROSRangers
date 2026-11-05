---
title: Protocol Library Messages
type: architecture
status: draft
date: 2026-11-03
updated: 2026-11-05
project: SEA autonomous drone
tags:
  - architecture
  - protocol
  - communication
  - esp8266
---

# Protocol Library Messages

> [!info] Status: DRAFT, follows Protocol Spec v0 v1.1.1
> Payload codecs of the C++ protocol library (`drone/lib/protocol`, #99, PR #143) for the messages in [[Protocol Spec v0]] §3. The code values in §5 are the ones decided on #142 (spec §3.1, 2026-11-05). For firmware (#101, #102) and for keeping the Python twin (#100) in sync.

---

## 1. Purpose and where it sits

A frame on the wire is **header (20 bytes) + payload + tag (8 bytes)** ([[Protocol Spec v0]] §1). The library splits the work:

| Layer | Files | Does |
|---|---|---|
| Frame | `sd/frame.hpp` | `encode_frame` / `decode_frame`: length, magic, version, HMAC tag (§4 rule 1). Gives back a `Header` and a pointer to the payload |
| Receiver | `sd/receiver.hpp` | Frame checks (§4 rule 1) and session check (rule 2), then known `type`, the type's transport (§2, `wrong_transport`), §3 payload size and `SETPOINT.mode` 0 or 1 (`check_payload()`, `transport_of()`), then the sequence window (rule 3). Counts rejects. Does not read the other payload values |
| Payload codec | `sd/setpoint.hpp`, `sd/messages.hpp` | Turns one payload into a struct and back. Nothing else |

Who calls the codecs: the firmware (link state machine #102) after the Receiver accepts a frame. It switches on `frame.header.type` (`MsgType` in `frame.hpp`) and calls the matching `decode_*` with `frame.payload` and `frame.header.payload_len`. To send, it calls `encode_*` into a buffer, then `encode_frame`. `HELLO` and `HELLO_ACK` are the exception: they arrive before a session exists, so #102 checks them with `decode_frame` and the PSK (spec §1: both are tagged with the PSK, and `HELLO_ACK` carries the new `session_id` in its header), not with the Receiver. Then it opens one Receiver per transport: `start_session(id, key, Transport::kUdp)` and `start_session(id, key, Transport::kTcp)`. Each accepts only its own types (spec §2), and the TCP one starts at seq 1 because `HELLO` and `HELLO_ACK` already used it.

## 2. API contract (all codecs)

Same contract as `encode_setpoint` / `decode_setpoint`.

| Rule | Detail |
|---|---|
| `encode_*(msg, out, out_cap)` | Writes the payload, returns its size. Returns **0** if `out` is null or `out_cap` is too small (and, for `EVENT`, if `text_len > 64`) |
| `decode_*(payload, len, out)` | Returns `false` if `payload` is `nullptr` or `len` is not the payload size. Fixed-size messages need the exact size; `EVENT` accepts 2 to 66 bytes |
| No value checks | Decode never checks values (unknown `mode`, unknown key, out-of-range code). That is the caller's job (§6) |
| Byte order | Little-endian for every integer (spec §1), via `src/le.hpp` |
| ESP8266 | No heap, no exceptions (`-fno-exceptions -fno-rtti`), fixed buffers. Same code on the host and the ESP ([[ADR-007 Portable Protocol Library and Custom ESP Firmware\|ADR-007]]) |
| Code fields | Stored as raw `uint8_t` / `uint16_t` in the structs. Cast to or from the enums in §5 |

## 3. Messages and functions

| Type | Message | Function(s) | Payload size | Notes |
|---|---|---|---|---|
| 0x01 | HELLO | `encode_hello` / `decode_hello` | 17 (`kHelloSize`) | `proto`, `ground_nonce[16]` |
| 0x02 | HELLO_ACK | `encode_hello_ack` / `decode_hello_ack` | 32 (`kHelloAckSize`) | `session_id`, `esp_nonce[16]`, `fw_version[12]` (raw bytes, not NUL-terminated) |
| 0x03 | ARM_CONTROL_REQUEST | `encode_u8_payload` / `decode_u8_payload` | 1 | `mask` |
| 0x04 | ARM_CONTROL_ACK | `encode_u8_payload` / `decode_u8_payload` | 1 | `result` (`AckResult`, §5) |
| 0x05 | RESUME_REQUEST | none | 0 | Empty payload: send `payload_len` 0, nothing to decode |
| 0x06 | RESUME_ACK | `encode_u8_payload` / `decode_u8_payload` | 1 | `result` (`AckResult`, §5) |
| 0x07 | BYE | `encode_u8_payload` / `decode_u8_payload` | 1 | `reason` (`ByeReason`, §5) |
| 0x20 / 0x21 | CONFIG_GET / CONFIG_SET | `encode_config_kv` / `decode_config_kv` | 5 (`kConfigKvSize`) | Same payload (`ConfigKv`: key u8, value i32). Keys in spec §7.3. `value` is ignored in a `CONFIG_GET` and sent as 0 (spec §3) |
| 0x22 | CONFIG_ACK | `encode_config_ack` / `decode_config_ack` | 6 (`kConfigAckSize`) | key u8, result u8 (`ConfigResult`), value i32: the current value, or 0 when there is none (spec §3) |
| 0x40 | EVENT | `encode_event` / `decode_event` | 2–66 | Variable length, see below |
| 0x60 | SETPOINT | `encode_setpoint` / `decode_setpoint` (`setpoint.hpp`) | 10 (`kSetpointSize`) | Plus `setpoint_to_rc` (spec §4–5) |
| 0x80 | STATE | `encode_state` / `decode_state` | 28 (`kStateSize`) | Layout in §4 |

- **One-byte codec**: four messages carry a single `u8`, so they share `encode_u8_payload` / `decode_u8_payload`. The message type in the header says what the byte means.
- **EVENT**: `code` u16 + `text`. There is no length field: `text_len = payload_len − 2`, from 0 to 64 (`kEventTextMax`). The text is **not NUL-terminated**; use `text_len`, never `strlen`.

## 4. STATE layout (28 bytes)

Fields and units are defined in [[Protocol Spec v0]] §3 (STATE). This table only adds the byte offsets, taken from `src/messages.cpp` and checked against the expected bytes in `test_messages.cpp`.

| Offset | Size | Field | Type | Unit |
|---|---|---|---|---|
| 0 | 1 | `esp_state` | u8 | `EspState` (§5) |
| 1 | 1 | `override_active` | u8 | – |
| 2 | 4 | `last_ctrl_seq` | u32 | – |
| 6 | 4 | `last_ctrl_t_send_us` | u32 | µs (sender clock) |
| 10 | 2 | `roll` | i16 | 0.1° |
| 12 | 2 | `pitch` | i16 | 0.1° |
| 14 | 2 | `heading` | u16 | ° |
| 16 | 2 | `vbat` | u16 | 0.01 V |
| 18 | 2 | `flow_x` | i16 | not defined in the spec |
| 20 | 2 | `flow_y` | i16 | not defined in the spec |
| 22 | 1 | `flow_quality` | u8 | – |
| 23 | 1 | `flow_status` | u8 | [[ThoneFlow]] §8.1 codes (numbers open, §6) |
| 24 | 2 | `rx_rejected` | u16 | count |
| 26 | 2 | `msp_errors` | u16 | count |

## 5. Code values

"Codes" are the numbers carried in `result`, `reason`, `esp_state` and `code` fields. They are defined once, in the block at the top of `include/sd/messages.hpp`.

The numbers are defined in [[Protocol Spec v0]] §3.1 (decided on #142, 2026-11-05); the spec wins if the two ever disagree. They are wire format: never renumber or reuse one. The test `Codes.MatchSpecSection31` pins every value.

| Enum | Used in | Values |
|---|---|---|
| `AckResult` (u8) | `ARM_CONTROL_ACK.result`, `RESUME_ACK.result` | 0 `kOk`, 1 `kWrongState`, 2 `kMaskMismatch`, 3 `kResumeConditionNotMet` |
| `ByeReason` (u8) | `BYE.reason` | 0 `kNormal`, 1 `kTimeout`, 2 `kProtocolError`, 3 `kReplaced` (by a new session) |
| `ConfigResult` (u8) | `CONFIG_ACK.result` | 0 `kOk`, 1 `kUnknownKey`, 2 `kReadOnly` (Info key), 3 `kSafetyParameter` (Safety key, firmware only), 4 `kOutOfRange`, 5 `kControlEnabled` (OD-16). A `CONFIG_SET` is checked 5 first, then 1–4 |
| `EspState` (u8) | `STATE.esp_state` | 0 `kBoot`, 1 `kNoSession`, 2 `kSessionIdle`, 3 `kStreaming`, 4 `kLinkFailsafe` ([[ESP Link State Machine]]) |
| `EventCode` (u16) | `EVENT.code` | `0x0101` `kSetpointStale`, `0x0102` `kLinkFailsafeEntered`, `0x0103` `kLinkResumed` |

- `EventCode` ranges: `0x00xx` system, `0x01xx` link, `0x02xx` FC / MSP. Only the three link codes exist so far.
- A stale setpoint is an event (`0x0101`), not an `EspState`.

## 6. Not the codec's job, and not done yet

**Caller's job** (Receiver or link state machine #102):

| Check | Where |
|---|---|
| Frame, session and sequence checks | `Receiver` (spec §4 rules 1–3) |
| Known `type`, payload size, `SETPOINT.mode` 0 or 1 | `Receiver`, via `check_payload()` in `messages.hpp`, before the sequence window (REQ-COM-001, REQ-COM-002 for `mode`) |
| `type` allowed on this transport (TCP vs UDP, spec §2) | `Receiver` (`wrong_transport`); #102 opens one per transport |
| Unknown codes, value ranges | #102 (spec §4 rule 4) |
| Config key exists, access (Info / Safety / Tunable), write range, no write while control is enabled, in the order of spec §3.1 | #102, answers with `CONFIG_ACK` |
| `rx_rejected` / `msp_errors` from 32-bit counters into the u16 STATE fields | #102 (saturate or wrap: open in #149) |
| `SETPOINT.flags` and the header `reserved` byte are not checked | Nobody: reserved, sent as 0, ignored (spec §3; vectors in group 8) |

**Not done yet:**

- No named constants for the config keys of spec §7.3 (`0x01 config_table_version` … `0x81 event_level`). Use the numbers from the spec for now.
- No numbers for `flow_status` (the [[ThoneFlow]] §8.1 names have no wire values yet).

## 7. How to test

```bash
cmake -S drone/lib/protocol -B build
cmake --build build
ctest --test-dir build   # CMake 3.20 or newer; otherwise: cd build && ctest
```

GoogleTest v1.14.0 and nlohmann-json 3.11.3 (the vector reader, tests only) are fetched when not installed; this needs network. Every test is explained in `drone/lib/protocol/tests/README.md`. `python3 scripts/protocol_tests.py` builds, runs and explains them.

## 8. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **Codec** | A pair of functions that turn a message payload into a struct (decode) and back into bytes (encode). |
| **Code value** | A number in a `result`, `reason`, `esp_state` or `code` field. Part of the wire format. |
| **ESP** | The ESP-12F module (ESP8266) on the drone: the Wi-Fi bridge between the ground PC and the FC, wired to FC UART4. |
| **FC** | Flight Controller: on this project the OddityRC F7 AIO (STM32F722, Betaflight target `ODDITYRCF7AIO`) of the 2.5" drone. It stabilises the aircraft and takes MSP from the ESP on UART4. |
| **Frame** | Header + payload + tag: one UDP datagram, or one TCP message cut by `payload_len`. |
| **HMAC** | Hash-based Message Authentication Code: a keyed tag that proves a message came from someone holding the key (ADR-002 uses a truncated HMAC-SHA256). |
| **Little-endian** | Byte order with the least significant byte first. Used for every integer in the protocol. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **NUL-terminated** | A C string that ends with a `0` byte. `fw_version` and `EVENT.text` are not; their length is fixed or comes from `payload_len`. |
| **OD-16** | Decision ID in [[PC_to_ESP]] (accepted): configuration is rejected while control is enabled, with no exception. |
| **Payload** | The bytes between the 20-byte header and the 8-byte tag of a frame ([[Protocol Spec v0]] §1). |
| **PSK** | Pre-shared key: a secret agreed in advance (Wi-Fi password, HMAC key). Never committed. |
| **Receiver** | The library class that checks each frame (tag, session, type, payload size, `SETPOINT.mode`, seq) before any payload is used. |
| **Saturate / wrap** | What a counter does at its maximum: stay there, or restart from 0. Open for `STATE.rx_rejected` (#149). |
| **Info / Safety / Tunable key** | The three configuration key groups: read-only, firmware-only, ground-writable (spec §7.3). |
| **Session** | One run of the protocol between the ground and the ESP, named by `session_id`; opened by `HELLO` / `HELLO_ACK` ([[ADR-005 TCP Session\|ADR-005]]). |
| **Test vector** | One fixed example (fields, exact bytes, expected result) that both the C++ and the Python implementation must pass (spec §6). Also called a "golden vector". |
| **TCP** | Transmission Control Protocol: reliable, ordered, connection-oriented transport. |
| **UDP** | User Datagram Protocol: connectionless transport suited to refreshed, time-sensitive data. |
