# Golden test vectors (Protocol Spec v0)

Shared by the C++ library (`drone/lib/protocol`, #99) and the Python twin (`drone/ground/src/sd_protocol_py`, #100). Both must pass every vector in CI.

The list of groups was agreed at the Protocol Spec v0 review on 2026-11-03 (`docs/PraticalPlan/03 Architecture/Protocol Spec v0.md` §6). This page adds the file format and one worked example per group. Where it and the spec disagree, the spec wins.

---

## 1. What a test vector is

A **test vector** is one fixed example: an input plus the result it must give. It is the "answer key" for the protocol.

- The protocol on the wire is **binary**. JSON is never sent between the ground and the ESP.
- The JSON file only **describes** examples, so that the C++ tests and the Python tests read **the same examples**.
- If both pass, the ground (Python) and the drone (C++) agree byte for byte. CI checks this on every PR, without flying.

A valid vector holds both sides of one message (`fields` and `bytes`). A reject vector has only `bytes` and `expect`:

| Field | Meaning |
|---|---|
| `name` | Unique name, never reused |
| `key` | HMAC key used for the tag (hex) |
| `fields` | The decoded message (what a human reads) |
| `bytes` | The exact binary frame (hex): header + payload + tag |
| `expect` | `accept`, or `reject:<reason>` |

What the tests do with one vector:

| Test | Check |
|---|---|
| Encode | `fields` → exactly `bytes` |
| Decode | `bytes` → exactly `fields` |
| Reject | broken `bytes` → dropped for the reason in `expect`, and counted (`rx_rejected`) |

Behaviour vectors (sequence, clamps) use `steps` or `channels` instead of a single `expect` (see groups 5 and 6).

Optional fields:

| Field | Meaning |
|---|---|
| `description` | One sentence in plain words. `scripts/protocol_tests.py` shows it next to the test |
| `note` | A warning, e.g. a code value that waits for #142 |
| `state` | Run through a Receiver with this session open (`current_session`). `last_ctrl_seq` is always 0: vectors start from a fresh session. Without `state`, only the frame checks run |
| `steps`, `final` | Groups 5 and 6: several frames in order, then `last_ctrl_seq`, `rx_rejected` and `gaps` at the end |
| `channels` | Group 6: the 8 RC channels for `MSP_SET_RAW_RC` |
| `psk`, `ground_nonce`, `esp_nonce` | Group 2: `key` is the session key derived from these |

## 1a. The files and how they are made

| File | Group | Vectors |
|---|---|---|
| `1_valid_frames.json` | 1 Valid frames | 14 |
| `2_hmac.json` | 2 HMAC | 4 |
| `3_header.json` | 3 Header | 9 |
| `4_session.json` | 4 Session | 2 |
| `5_sequence.json` | 5 Sequence | 6 |
| `6_clamps.json` | 6 Clamps and mapping | 9 |

- **Made by `generate.py`** in this folder. It uses only the Python standard library and is written from the spec, so it is independent of both implementations (rule 4 below).
- **To add a vector:** add it to `generate.py`, run `python3 drone/tests/vectors/generate.py`, and commit both.
- **CI checks the files are current:** `generate.py --check` runs in `tests/scripts/test_protocol_tests.py`.
- **Who reads them:**
  - C++: `drone/lib/protocol/tests/test_vectors.cpp`, one GoogleTest test per vector (`Golden/Vector.Check/<name>`).
  - Python: `drone/ground/src/sd_protocol_py/tests/test_vectors.py` (#100).

### Field names per message type

Header fields are always `type`, `flags`, `session_id`, `seq`, `t_send_us`. Payload fields follow spec §3. Where a payload name would clash with a header or top-level name, it is renamed:

| Type | Payload fields |
|---|---|
| HELLO | `proto`, `ground_nonce` (hex) |
| HELLO_ACK | `new_session_id`, `esp_nonce` (hex), `fw_version` (ASCII, padded with zero bytes to 12) |
| ARM_CONTROL_REQUEST | `mask` |
| ARM_CONTROL_ACK, RESUME_ACK | `result` |
| RESUME_REQUEST | none |
| BYE | `reason` |
| CONFIG_GET, CONFIG_SET | `config_key`, `value` |
| EVENT | `code`, `text` (ASCII) |
| SETPOINT | `mode`, `roll`, `pitch`, `yaw_rate`, `throttle`, `sp_flags` |
| STATE | `esp_state`, `override_active`, `last_ctrl_seq`, `last_ctrl_t_send_us`, `roll`, `pitch`, `heading`, `vbat`, `flow_x`, `flow_y`, `flow_quality`, `flow_status`, `rx_rejected`, `msp_errors` |

Code values for results, BYE reasons, EVENT codes and `esp_state` are the suggested answers in #142, not frozen yet. Those vectors carry a `note`.

### Common values in all examples

| Value | Used as |
|---|---|
| Test key | `00 01 02 … 1F` (32 bytes). Test only, committed, **never the real PSK** |
| `session_id` | `0x12345678` (the ESP's current session) |
| All integers | Little-endian (spec §1) |

All bytes below were computed with HMAC-SHA256 using the test key, first 8 bytes as the tag (ADR-002).

---

## 2. The reference frame (used by groups 1–4)

A `SETPOINT`: mode 1 (attitude), roll 100, pitch −200, yaw 0, throttle 500, seq 7, `t_send_us` 1000.

```
header   53 44 01 60 00 00 0a 00 78 56 34 12 07 00 00 00 e8 03 00 00
         └SD─┘ ver type flg res len=10 session_id   seq=7      t_send=1000
payload  01 64 00 38 ff 00 00 f4 01 00
         mode roll=100 pitch=-200 yaw=0 thr=500 flags
tag      fb 70 3d 82 4f 5b 58 9e
```

---

## 3. The groups, with one example each

### Group 1: Valid frames (14 vectors)

One per message type, plus `bye_replaced` and `resume_ack_wrong_state` with non-zero codes. All `accept`. Vector names are `<message type>_<what is special>`:

| Message type | Vector name |
|---|---|
| HELLO | `hello_basic` |
| HELLO_ACK | `hello_ack_basic` |
| ARM_CONTROL_REQUEST | `arm_control_request_mask_15` |
| ARM_CONTROL_ACK | `arm_control_ack_ok` |
| RESUME_REQUEST | `resume_request_basic` |
| RESUME_ACK | `resume_ack_ok` |
| BYE | `bye_normal` |
| CONFIG_GET | `config_get_t_loss` |
| CONFIG_SET | `config_set_telemetry_rate` |
| EVENT | `event_failsafe_entered` |
| SETPOINT | `setpoint_attitude_basic` (the example below) |
| STATE | `state_streaming` |

Proves: the byte layout is the same on both sides.

```json
{
  "name": "setpoint_attitude_basic",
  "key": "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f",
  "fields": { "type": "SETPOINT", "flags": 0, "session_id": 305419896, "seq": 7, "t_send_us": 1000,
              "mode": 1, "roll": 100, "pitch": -200, "yaw_rate": 0, "throttle": 500, "sp_flags": 0 },
  "bytes": "5344016000000a007856341207000000e803000001640038ff0000f40100fb703d824f5b589e",
  "expect": "accept"
}
```

### Group 2: HMAC (4 vectors)

`hello_signed_with_psk` (accept), `setpoint_signed_with_session_key` (accept), `setpoint_payload_bit_flipped` (reject), `setpoint_tag_bit_flipped` (reject).

Proves: the tag is computed over the right bytes, and any changed bit is refused.

Example: the reference frame with **one bit flipped in the roll byte** (`64` → `65`), tag unchanged:

```json
{
  "name": "setpoint_payload_bit_flipped",
  "key": "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f",
  "bytes": "5344016000000a007856341207000000e803000001650038ff0000f40100fb703d824f5b589e",
  "expect": "reject:bad_tag"
}
```

### Group 3: Header (9 vectors)

`wrong_magic`, `wrong_version`, `payload_len_over_256`, `payload_len_mismatch`, `truncated_frame`, `truncated_header` (shorter than header + tag). All reject.

Then frames whose header, tag and session are good but whose content is not a valid message (REQ-COM-001): `unknown_type` (0x99, `reject:bad_type`), `setpoint_payload_9_bytes` and `event_text_65` (67 bytes, over the 66 limit), both `reject:bad_payload_size`. They carry `state`, so they go through the receiver. The type and size check runs **before** the seq window, so a refused frame changes no state (REQ-COM-004).

Proves: garbage is refused before anything is read (spec §4 rule 1), and a malformed message is refused before its seq is used.

Example: magic `SE` instead of `SD`. The tag is recomputed, so the **only** fault is the magic:

```json
{
  "name": "wrong_magic",
  "key": "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f",
  "bytes": "5345016000000a007856341207000000e803000001640038ff0000f40100de307f06c4dd38a3",
  "expect": "reject:bad_magic"
}
```

### Group 4: Session (2 vectors)

`wrong_session_id`, `session_id_zero_outside_hello`. Both reject.

Proves: messages from another (old or foreign) session are refused (spec §4 rule 2).

Example: the ESP's session is `0x12345678`, the frame says `0x12345679`, tag valid:

```json
{
  "name": "wrong_session_id",
  "key": "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f",
  "state": { "current_session": 305419896 },
  "bytes": "5344016000000a007956341207000000e803000001640038ff0000f40100b6785492183eae61",
  "expect": "reject:wrong_session"
}
```

### Group 5: Sequence, ADR-003 (6 vectors)

`seq_in_order`, `seq_duplicate`, `seq_older`, `seq_gap` (accepted, gap counted), `seq_zero` (never accepted), `seq_too_high` (`seq ≥ 2^31` → new session).

Proves: old or replayed control messages are refused (anti-replay). These are **behaviour** vectors: several frames in order, with the result after each one.

Example: neutral setpoints with seq 1, 2, 2 (exact replay), 3:

```json
{
  "name": "seq_duplicate",
  "key": "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f",
  "state": { "current_session": 305419896, "last_ctrl_seq": 0 },
  "steps": [
    { "bytes": "5344016000000a007856341201000000204e0000010000000000000000006c870020e780aa97", "expect": "accept" },
    { "bytes": "5344016000000a007856341202000000409c000001000000000000000000712720248f2ea86c", "expect": "accept" },
    { "bytes": "5344016000000a007856341202000000409c000001000000000000000000712720248f2ea86c", "expect": "reject:duplicate_seq" },
    { "bytes": "5344016000000a00785634120300000060ea00000100000000000000000074f74adcb71e36f6", "expect": "accept" }
  ],
  "final": { "last_ctrl_seq": 3, "rx_rejected": 1 }
}
```

### Group 6: Clamps and channel mapping, spec §4–5 (9 vectors)

`map_min` (−1000 and throttle 0 → 1000 µs), `map_center` (0 → 1500 µs), `map_max` (+1000 and throttle 1000 → 2000 µs), `clamp_over` (+1500 → 2000 µs), `clamp_under` (−32768 and throttle −50 → 1000 µs), `throttle_map` (250 → 1250 µs), `odd_values_round_toward_center` (1 and −1 → 1500 µs, 999 → 1999 µs), `aux_always_neutral` (aux1–4 always 1500 µs), and `mode_2_then_same_seq` (steps: seq 1 with mode 2 → `reject:bad_mode`, then a valid seq 1 → accept, so the refused frame used up nothing; spec §4 rule 4 as decided in #142).

Proves: the ESP turns a setpoint into the right RC channels for `MSP_SET_RAW_RC` (8 channels, AETR order: roll, pitch, throttle, yaw, aux1–4), and never sends a value outside the allowed range.

Example: roll **+1500** (out of range; the ground should clamp it too, but the ESP is the last check):

```json
{
  "name": "clamp_over",
  "key": "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f",
  "fields": { "type": "SETPOINT", "flags": 0, "session_id": 305419896, "seq": 1, "t_send_us": 0,
              "mode": 1, "roll": 1500, "pitch": 0, "yaw_rate": 0, "throttle": 500, "sp_flags": 0 },
  "bytes": "5344016000000a0078563412010000000000000001dc0500000000f40100aef509cd5dc0dca2",
  "expect": "accept",
  "channels": [2000, 1500, 1500, 1500, 1500, 1500, 1500, 1500]
}
```

---

## 4. Rules for the vector files

1. **Adding** a vector is free: a new message type or key, a bug found (regression), a new edge case.
2. **Changing or deleting** a vector means the protocol changed: a dated spec entry, maybe a version bump. Never a silent edit.
3. `name` is unique and never reused.
4. Expected bytes come from a small **independent generator** (Python standard library only, not the team's protocol code), checked by hand against the spec once. After that, the committed JSON is the truth.
5. Both implementations must pass **every** vector in CI. A failing vector blocks the merge.
6. Vectors do not test timing, Wi-Fi or the real drone (that is SITL and HIL).

---

## 5. Reject reasons

The `reject:<reason>` names, and the C++ values they map to:

| Reason | Meaning | C++ |
|---|---|---|
| `truncated` | Shorter than header + tag, or than `payload_len` says | `RxError::kTruncated`, `RxResult::kTruncated` |
| `bad_magic` | Not `SD` | `RxError::kBadMagic`, `RxResult::kBadMagic` |
| `bad_version` | Version byte is not 1 | `RxError::kBadVersion`, `RxResult::kBadVersion` |
| `bad_length` | `payload_len` > 256, or bytes after the frame | `RxError::kBadLength`, `RxResult::kBadLength` |
| `bad_tag` | HMAC tag does not match | `RxError::kBadTag`, `RxResult::kBadTag` |
| `wrong_session` | `session_id` is not the current session (§4 rule 2) | `RxResult::kWrongSession` |
| `bad_type` | Not a spec §3 message type | `RxResult::kBadType` |
| `bad_payload_size` | `payload_len` is not the §3 size of the type (EVENT: 2..66) | `RxResult::kBadPayloadSize` |
| `bad_mode` | `SETPOINT.mode` is not 0 or 1 (§4 rule 4, decided in #142) | `RxResult::kBadMode` |
| `duplicate_seq` | Same seq as the last accepted one | `RxResult::kDuplicateSeq` |
| `old_seq` | Older than the last accepted one, or seq 0 | `RxResult::kOldSeq` |
| `seq_too_high` | `seq ≥ 2^31`: a new session is needed | `RxResult::kSeqTooHigh` |
| `no_session` | No session is open (no vector yet) | `RxResult::kNoSession` |

## 6. Still to come

- **Group 7, configuration** (`CONFIG_GET` / `CONFIG_SET` / `CONFIG_ACK` results: OK, unknown key, read-only, out of range, refused while streaming). Waits for the `CONFIG_ACK` decision in #142.

---

## Glossary

| Term | Meaning |
|---|---|
| **Test vector** | One fixed example (input + expected result) shared by both implementations |
| **Golden vector** | Same thing; "golden" means it is the reference everyone must match |
| **HMAC tag** | 8-byte signature at the end of each frame (truncated HMAC-SHA256, ADR-002) |
| **PSK** | Pre-shared key, the real secret. Never in the vectors |
| **Regression vector** | A vector added after a bug, so the bug cannot come back |
| **AETR** | RC channel order: roll, pitch, throttle, yaw, then aux |
| **CI** | Continuous Integration: the GitHub Actions checks on every PR |
