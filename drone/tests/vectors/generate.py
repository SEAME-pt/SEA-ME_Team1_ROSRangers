#!/usr/bin/env python3
"""Writes the golden vector files of Protocol Spec v0 §6 (format: README.md).

Independent on purpose: Python standard library only, written from the spec,
never importing the team's C++ or Python protocol code (README §4 rule 4).

    python3 drone/tests/vectors/generate.py           # rewrite the *.json files
    python3 drone/tests/vectors/generate.py --check   # fail if a file is out of date

Once committed, the JSON files are the truth. Changing a vector that exists
changes the protocol (README §4 rule 2); adding one is free.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

TEST_KEY = bytes(range(32))  # 00 01 .. 1f: tests only, never the real PSK
SESSION = 0x12345678

# Spec §3 message types.
TYPES = {
    "HELLO": 0x01,
    "HELLO_ACK": 0x02,
    "ARM_CONTROL_REQUEST": 0x03,
    "ARM_CONTROL_ACK": 0x04,
    "RESUME_REQUEST": 0x05,
    "RESUME_ACK": 0x06,
    "BYE": 0x07,
    "CONFIG_GET": 0x20,
    "CONFIG_SET": 0x21,
    "CONFIG_ACK": 0x22,
    "EVENT": 0x40,
    "SETPOINT": 0x60,
    "STATE": 0x80,
    "FC_HEALTH": 0x81,  # spec v1.4 (#179)
}


# ---------------------------------------------------------------- wire format


def tag(key: bytes, data: bytes) -> bytes:
    """Truncated HMAC-SHA256: first 8 bytes (ADR-002)."""
    return hmac.new(key, data, hashlib.sha256).digest()[:8]


def header(
    msg_type: int,
    payload_len: int,
    session_id: int,
    seq: int,
    t_send_us: int,
    flags: int = 0,
    magic: bytes = b"SD",
    version: int = 1,
) -> bytes:
    """Spec §1: magic, version u8, type u8, flags u8, reserved u8,
    payload_len u16, session_id u32, seq u32, t_send_us u32 (little-endian)."""
    return magic + struct.pack(
        "<BBBBHIII",
        version,
        msg_type,
        flags,
        0,
        payload_len,
        session_id,
        seq,
        t_send_us,
    )


def derive_keys(
    psk: bytes, ground_nonce: bytes, esp_nonce: bytes
) -> tuple[bytes, bytes, bytes]:
    """Spec §1 key schedule: (session key, ground-to-ESP key, ESP-to-ground key).
    One key per direction since 2026-11-05 (ADR-002 entry, #150)."""
    session = hmac.new(psk, b"sess" + ground_nonce + esp_nonce, hashlib.sha256).digest()
    g2e = hmac.new(session, b"g2e", hashlib.sha256).digest()
    e2g = hmac.new(session, b"e2g", hashlib.sha256).digest()
    return session, g2e, e2g


def frame(key: bytes, msg_type: int, payload: bytes, **h) -> bytes:
    head = header(msg_type, len(payload), **h)
    return head + payload + tag(key, head + payload)


def payload(fields: dict) -> bytes:
    """Spec §3 payload for fields["type"]."""
    t = fields["type"]
    if t == "HELLO":
        return struct.pack("<B", fields["proto"]) + bytes.fromhex(
            fields["ground_nonce"]
        )
    if t == "HELLO_ACK":
        fw = fields["fw_version"].encode("ascii").ljust(12, b"\0")
        return (
            struct.pack("<I", fields["new_session_id"])
            + bytes.fromhex(fields["esp_nonce"])
            + fw
        )
    if t == "ARM_CONTROL_REQUEST":
        return struct.pack("<B", fields["mask"])
    if t in ("ARM_CONTROL_ACK", "RESUME_ACK"):
        return struct.pack("<B", fields["result"])
    if t == "RESUME_REQUEST":
        return b""
    if t == "BYE":
        return struct.pack("<B", fields["reason"])
    if t in ("CONFIG_GET", "CONFIG_SET"):
        return struct.pack("<Bi", fields["config_key"], fields["value"])
    if t == "CONFIG_ACK":
        return struct.pack(
            "<BBi", fields["config_key"], fields["result"], fields["value"]
        )
    if t == "EVENT":
        return struct.pack("<H", fields["code"]) + fields["text"].encode("ascii")
    if t == "SETPOINT":
        return struct.pack(
            "<BhhhhB",
            fields["mode"],
            fields["roll"],
            fields["pitch"],
            fields["yaw_rate"],
            fields["throttle"],
            fields.get("sp_flags", 0),
        )
    if t == "STATE":
        return struct.pack(
            "<BBIIhhHHhhBBHH",
            *(
                fields[k]
                for k in (
                    "esp_state",
                    "override_active",
                    "last_ctrl_seq",
                    "last_ctrl_t_send_us",
                    "roll",
                    "pitch",
                    "heading",
                    "vbat",
                    "flow_x",
                    "flow_y",
                    "flow_quality",
                    "flow_status",
                    "rx_rejected",
                    "msp_errors",
                )
            ),
        )
    if t == "FC_HEALTH":  # spec v1.4 §3, 42 bytes
        return struct.pack(
            "<BBIHHHHIIIHHHHII",
            *(
                fields[k]
                for k in (
                    "fc_alive",
                    "armed",
                    "arming_disable_flags",
                    "status_age_ms",
                    "attitude_age_ms",
                    "analog_age_ms",
                    "reply_age_ms",
                    "requests",
                    "replies",
                    "timeouts",
                    "bad_checksum",
                    "too_long",
                    "error_replies",
                    "unexpected",
                    "rc_sent",
                    "rc_replaced",
                )
            ),
        )
    raise ValueError(t)


def encode(fields: dict, key: bytes = TEST_KEY) -> bytes:
    return frame(
        key,
        TYPES[fields["type"]],
        payload(fields),
        session_id=fields["session_id"],
        seq=fields["seq"],
        t_send_us=fields["t_send_us"],
        flags=fields.get("flags", 0),
    )


def rc_channels(sp: dict) -> list[int]:
    """Spec §4 rule 4 and §5: clamp, then 1500 + v/2 rounding toward 1500;
    throttle 1000 + t; AETR order; aux1..4 always 1500."""

    def axis(v: int) -> int:
        v = max(-1000, min(1000, v))
        return 1500 + int(v / 2)  # int() truncates toward zero = toward 1500

    throttle = 1000 + max(0, min(1000, sp["throttle"]))
    return [axis(sp["roll"]), axis(sp["pitch"]), throttle, axis(sp["yaw_rate"])] + [
        1500
    ] * 4


# ---------------------------------------------------------------- helpers


def setpoint(seq: int, t_send_us: int = 0, session_id: int = SESSION, **values) -> dict:
    sp = {"mode": 1, "roll": 0, "pitch": 0, "yaw_rate": 0, "throttle": 0, "sp_flags": 0}
    sp.update(values)
    return {
        "type": "SETPOINT",
        "flags": 0,
        "session_id": session_id,
        "seq": seq,
        "t_send_us": t_send_us,
        **sp,
    }


STATE_ZERO = {k: 0 for k in ("esp_state", "override_active", "last_ctrl_seq", "last_ctrl_t_send_us", "roll", "pitch",
                             "heading", "vbat", "flow_x", "flow_y", "flow_quality", "flow_status", "rx_rejected",
                             "msp_errors")}  # fmt: skip

REFERENCE = setpoint(7, 1000, roll=100, pitch=-200, throttle=500)


def neutral(seq: int) -> str:
    """The neutral setpoints of README group 5: mode 1, all zero, t_send = seq * 20 ms."""
    return encode(setpoint(seq, (seq * 20000) % 2**32)).hex()


def valid(
    name: str, description: str, fields: dict, key: bytes = TEST_KEY, **extra
) -> dict:
    return {
        "name": name,
        "description": description,
        "key": key.hex(),
        "fields": fields,
        "bytes": encode(fields, key).hex(),
        "expect": "accept",
        **extra,
    }


def rejected(
    name: str,
    description: str,
    data: bytes,
    reason: str,
    key: bytes = TEST_KEY,
    **extra,
) -> dict:
    return {
        "name": name,
        "description": description,
        "key": key.hex(),
        **extra,
        "bytes": data.hex(),
        "expect": f"reject:{reason}",
    }


def sequence(
    name: str, description: str, seqs: list[int], expects: list[str], final: dict
) -> dict:
    return {
        "name": name,
        "description": description,
        "key": TEST_KEY.hex(),
        "state": {"current_session": SESSION, "last_ctrl_seq": 0},
        "steps": [{"bytes": neutral(s), "expect": e} for s, e in zip(seqs, expects)],
        "final": final,
    }


def channels(name: str, description: str, **values) -> dict:
    fields = setpoint(1, **values)
    return valid(name, description, fields, channels=rc_channels(fields))


# ---------------------------------------------------------------- groups


def msg(
    t: str, seq: int, session_id: int = SESSION, t_send_us: int | None = None, **values
) -> dict:
    """Fields of one message: the header, then the payload fields of spec §3."""
    return {
        "type": t,
        "flags": 0,
        "session_id": session_id,
        "seq": seq,
        "t_send_us": seq * 1000 if t_send_us is None else t_send_us,
        **values,
    }


def group1() -> list[dict]:
    ground_nonce = bytes(range(0xA0, 0xB0)).hex()
    esp_nonce = bytes(range(0xB0, 0xC0)).hex()
    return [
        valid("hello_basic", "HELLO: protocol 1 and the ground nonce. Session 0, tagged with the PSK (here the test key)",
              msg("HELLO", 1, 0, proto=1, ground_nonce=ground_nonce)),
        valid("hello_ack_basic", "HELLO_ACK: the new session ID (in the header and the payload), the ESP nonce and the firmware version 'v0.1.0' padded with zero bytes to 12. Tagged with the PSK",
              msg("HELLO_ACK", 1, new_session_id=SESSION, esp_nonce=esp_nonce, fw_version="v0.1.0")),
        valid("arm_control_request_mask_15", "ARM_CONTROL_REQUEST with mask 15 (roll, pitch, yaw and throttle)",
              msg("ARM_CONTROL_REQUEST", 2, mask=15)),
        valid("arm_control_ack_ok", "ARM_CONTROL_ACK with result 0 (OK)", msg("ARM_CONTROL_ACK", 2, result=0)),
        valid("resume_request_basic", "RESUME_REQUEST: no payload, 28-byte frame", msg("RESUME_REQUEST", 3)),
        valid("resume_ack_ok", "RESUME_ACK with result 0 (OK)", msg("RESUME_ACK", 3, result=0)),
        valid("bye_normal", "BYE with reason 0 (normal)", msg("BYE", 9, reason=0)),
        valid("bye_replaced", "BYE with reason 3 (replaced by a new session): a non-zero code", msg("BYE", 10, reason=3)),
        valid("resume_ack_wrong_state", "RESUME_ACK with result 1 (wrong state): a non-zero code",
              msg("RESUME_ACK", 4, result=1)),
        valid("config_get_t_loss", "CONFIG_GET of key 0x40 t_loss_ms (value field 0)", msg("CONFIG_GET", 4, config_key=0x40, value=0)),
        valid("config_ack_ok", "CONFIG_ACK for key 0x80 telemetry_rate_hz: result 0 (OK), value 25 (the default)",
              msg("CONFIG_ACK", 5, config_key=0x80, result=0, value=25)),
        valid("config_set_telemetry_rate", "CONFIG_SET of key 0x80 telemetry_rate_hz to 30",
              msg("CONFIG_SET", 5, config_key=0x80, value=30)),
        valid("event_failsafe_entered", "EVENT code 0x0102 (link failsafe entered) with a short text",
              msg("EVENT", 6, code=0x0102, text="no setpoint for 250 ms")),
        valid("setpoint_attitude_basic", "The reference SETPOINT: mode 1, roll 100, pitch -200, yaw 0, throttle 500, seq 7",
              REFERENCE),
        valid("state_streaming", "STATE while streaming, every field a different value (negative roll and flow included)",
              msg("STATE", 42, esp_state=3, override_active=1, last_ctrl_seq=41, last_ctrl_t_send_us=820000,
                  roll=-15, pitch=30, heading=359, vbat=1110, flow_x=-300, flow_y=300, flow_quality=200,
                  flow_status=2, rx_rejected=7, msp_errors=5),
              note="esp_state 3 = STREAMING (spec §3.1)."),
        valid("fc_health_fc_lost", "FC_HEALTH after the FC went silent: not alive, armed unknown, ages saturated or unknown",
              msg("FC_HEALTH", 43, fc_alive=0, armed=0xFF, arming_disable_flags=0, status_age_ms=0xFFFF,
                  attitude_age_ms=0xFFFE, analog_age_ms=9876, reply_age_ms=260, requests=120000, replies=119990,
                  timeouts=10, bad_checksum=1, too_long=0, error_replies=2, unexpected=3, rc_sent=60000,
                  rc_replaced=17),
              note="Spec v1.4 (#179): ages in ms, 0xFFFE saturated, 0xFFFF never received or expired; armed 0xFF = unknown."),
    ]  # fmt: skip


def group2() -> list[dict]:
    psk = TEST_KEY
    ground_nonce = bytes([0x11] * 16)
    esp_nonce = bytes([0x22] * 16)
    _, g2e, e2g = derive_keys(psk, ground_nonce, esp_nonce)
    nonces = {
        "psk": psk.hex(),
        "ground_nonce": ground_nonce.hex(),
        "esp_nonce": esp_nonce.hex(),
    }
    state = msg("STATE", 1, t_send_us=0, esp_state=3, override_active=1, last_ctrl_seq=1,
                last_ctrl_t_send_us=0, roll=0, pitch=0, heading=90, vbat=1110, flow_x=0, flow_y=0,
                flow_quality=200, flow_status=0, rx_rejected=0, msp_errors=0)  # fmt: skip
    sp = setpoint(1, roll=100, throttle=500)
    good = encode(REFERENCE)
    payload_flip = bytearray(good)
    payload_flip[21] ^= 0x01  # roll 100 -> 101
    tag_flip = bytearray(good)
    tag_flip[-1] ^= 0x80
    return [
        valid("hello_signed_with_psk", "HELLO tagged with the PSK, as every HELLO is (spec §1)",
              {"type": "HELLO", "flags": 0, "session_id": 0, "seq": 1, "t_send_us": 0,
               "proto": 1, "ground_nonce": ground_nonce.hex()}, psk),
        valid("setpoint_signed_with_ground_to_esp_key",
              "SETPOINT tagged with the ground-to-ESP key = HMAC-SHA256(session key, 'g2e'), "
              "session key = HMAC-SHA256(PSK, 'sess' || ground_nonce || esp_nonce)",
              sp, g2e, direction="g2e", **nonces),
        valid("state_signed_with_esp_to_ground_key",
              "STATE tagged with the ESP-to-ground key = HMAC-SHA256(session key, 'e2g')",
              state, e2g, direction="e2g", **nonces),
        rejected("reflected_state_rejected",
                 "The ESP's own STATE (ESP-to-ground key) sent back to the ESP, which checks "
                 "with the ground-to-ESP key: refused (#150)",
                 encode(state, e2g), "bad_tag", key=g2e, direction="g2e", **nonces),
        rejected("reflected_setpoint_rejected",
                 "The ground's own SETPOINT (ground-to-ESP key) sent back to the ground, which checks "
                 "with the ESP-to-ground key: refused, so a reflection never reaches the ground (#150)",
                 encode(sp, g2e), "bad_tag", key=e2g, direction="e2g", **nonces),
        rejected("setpoint_payload_bit_flipped", "The reference frame with roll 100 -> 101 (one bit), old tag",
                 bytes(payload_flip), "bad_tag"),
        rejected("setpoint_tag_bit_flipped", "The reference frame with the top bit of the last tag byte flipped",
                 bytes(tag_flip), "bad_tag"),
    ]  # fmt: skip


def group3() -> list[dict]:
    sp = payload(REFERENCE)
    h = {"session_id": SESSION, "seq": 7, "t_send_us": 1000}

    def with_tag(head: bytes, body: bytes) -> bytes:
        return head + body + tag(TEST_KEY, head + body)

    good = encode(REFERENCE)
    state = {"current_session": SESSION}
    return [
        rejected("wrong_magic", "Magic 'SE' instead of 'SD'; the tag is recomputed so the magic is the only fault",
                 with_tag(header(0x60, 10, magic=b"SE", **h), sp), "bad_magic"),
        rejected("wrong_version", "Version 2 instead of 1; the tag is recomputed so the version is the only fault",
                 with_tag(header(0x60, 10, version=2, **h), sp), "bad_version"),
        rejected("payload_len_over_256", "SETPOINT with payload_len 257, 257 payload bytes and a valid tag: only the 256 limit is broken",
                 with_tag(header(0x60, 257, **h), bytes(257)), "bad_length"),
        rejected("payload_len_mismatch", "The reference frame with one extra byte after the tag", good + b"\0", "bad_length"),
        rejected("truncated_frame", "The reference frame without its last byte", good[:-1], "truncated"),
        rejected("truncated_header", "Only the 20-byte header: shorter than header + tag (28 bytes)", good[:20], "truncated"),
        # Header, tag and session are good: the type and size checks (§3, REQ-COM-001), run before the seq window.
        rejected("unknown_type", "Type 0x99 with a valid tag and session: not a §3 message type",
                 with_tag(header(0x99, 10, **h), sp), "bad_type", state=state),
        rejected("setpoint_payload_9_bytes", "SETPOINT with a 9-byte payload (needs 10), valid tag",
                 with_tag(header(0x60, 9, **h), sp[:9]), "bad_payload_size", state=state),
        rejected("event_text_65", "EVENT with 65 text bytes (2 + 65 = 67 > 66), valid tag",
                 with_tag(header(0x40, 67, **h), struct.pack("<H", 0x0102) + b"x" * 65), "bad_payload_size", state=state),
    ]  # fmt: skip


def group4() -> list[dict]:
    state = {"current_session": SESSION}
    return [
        rejected("wrong_session_id", "The reference frame for session 0x12345679 with a valid tag; the ESP is in 0x12345678",
                 encode({**REFERENCE, "session_id": SESSION + 1}), "wrong_session", state=state),
        rejected("session_id_zero_outside_hello", "A SETPOINT with session_id 0 (reserved for HELLO) and a valid tag",
                 encode({**REFERENCE, "session_id": 0}), "wrong_session", state=state),
    ]  # fmt: skip


def group5() -> list[dict]:
    a = "accept"
    return [
        sequence("seq_in_order", "Neutral setpoints 1, 2, 3: all accepted", [1, 2, 3], [a, a, a],
                 {"last_ctrl_seq": 3, "rx_rejected": 0, "gaps": 0}),
        sequence("seq_duplicate", "1, 2, 2, 3: the exact replay of 2 is refused", [1, 2, 2, 3],
                 [a, a, "reject:duplicate_seq", a], {"last_ctrl_seq": 3, "rx_rejected": 1, "gaps": 0}),
        sequence("seq_older", "1, 5, 3: 3 arrives late and is refused", [1, 5, 3], [a, a, "reject:old_seq"],
                 {"last_ctrl_seq": 5, "rx_rejected": 1, "gaps": 1}),
        sequence("seq_gap", "1, 2, 10: 10 is accepted although 3-9 never arrived", [1, 2, 10], [a, a, a],
                 {"last_ctrl_seq": 10, "rx_rejected": 0, "gaps": 1}),
        sequence("seq_zero", "seq 0 is never accepted, even as the first frame", [0, 1], ["reject:old_seq", a],
                 {"last_ctrl_seq": 1, "rx_rejected": 1, "gaps": 0}),
        sequence("seq_too_high", "1, then 2^31: the sender must open a new session", [1, 2**31],
                 [a, "reject:seq_too_high"], {"last_ctrl_seq": 1, "rx_rejected": 1, "gaps": 0}),
    ]  # fmt: skip


def group6() -> list[dict]:
    return [
        {
            "name": "mode_2_then_same_seq",
            "description": "SETPOINT seq 1 with mode 2 is refused, then a valid SETPOINT with the same seq 1 is accepted: the refused frame used up nothing",
            "key": TEST_KEY.hex(),
            "state": {"current_session": SESSION, "last_ctrl_seq": 0},
            "steps": [
                {"bytes": encode(setpoint(1, 20000, mode=2)).hex(), "expect": "reject:bad_mode"},
                {"bytes": neutral(1), "expect": "accept"},
            ],
            "final": {"last_ctrl_seq": 1, "rx_rejected": 1, "gaps": 0},
            "note": "Spec §4 rule 4 and §6 group 6 as decided in #142 (PR #148).",
        },
        channels("map_min", "Roll, pitch and yaw -1000, throttle 0: every stick channel at 1000 us",
                 roll=-1000, pitch=-1000, yaw_rate=-1000, throttle=0),
        channels("map_center", "All sticks 0, throttle 500: 1500 us everywhere", throttle=500),
        channels("map_max", "Roll, pitch and yaw +1000, throttle 1000: every stick channel at 2000 us",
                 roll=1000, pitch=1000, yaw_rate=1000, throttle=1000),
        channels("clamp_over", "Roll +1500 (out of range) is clamped to 2000 us", roll=1500, throttle=500),
        channels("clamp_under", "Pitch -32768 and throttle -50 (out of range) are clamped to 1000 us",
                 pitch=-32768, throttle=-50),
        channels("throttle_map", "Throttle 250 -> 1250 us (1000 + t)", throttle=250),
        channels("odd_values_round_toward_center", "Roll 1 and pitch -1 -> 1500 us, yaw 999 -> 1999 us (1500 + v/2, toward 1500)",
                 roll=1, pitch=-1, yaw_rate=999, throttle=500),
        channels("aux_always_neutral", "Every stick at its extreme: aux1-4 stay at 1500 us",
                 roll=1000, pitch=-1000, yaw_rate=1000, throttle=1000),
    ]  # fmt: skip


def group7() -> list[dict]:
    def ack(
        name: str, description: str, seq: int, key: int, result: int, value: int
    ) -> dict:
        return valid(
            name,
            description,
            {
                "type": "CONFIG_ACK",
                "flags": 0,
                "session_id": SESSION,
                "seq": seq,
                "t_send_us": seq * 1000,
                "config_key": key,
                "result": result,
                "value": value,
            },
        )

    return [
        ack("config_ack_result_0_ok", "Reply to CONFIG_GET of 0x80 telemetry_rate_hz: OK, value 25", 2, 0x80, 0, 25),
        ack("config_ack_result_1_unknown_key", "CONFIG_SET of key 0xC0 (reserved range): unknown key, value 0 (no value)",
            3, 0xC0, 1, 0),
        ack("config_ack_result_2_read_only", "CONFIG_SET of Info key 0x01 config_table_version: read-only, value 1 unchanged",
            4, 0x01, 2, 1),
        ack("config_ack_result_3_safety_parameter", "CONFIG_SET of Safety key 0x40 t_loss_ms: firmware only, value 250 unchanged",
            5, 0x40, 3, 250),
        ack("config_ack_result_4_out_of_range", "CONFIG_SET of 0x80 telemetry_rate_hz to 60 (range 1-50): out of range, value 25 unchanged",
            6, 0x80, 4, 25),
        ack("config_ack_result_5_control_enabled", "CONFIG_SET of 0x81 event_level while STREAMING: refused, value 1 unchanged",
            7, 0x81, 5, 1),
    ]  # fmt: skip


def group8() -> list[dict]:
    ground_nonce = bytes([0x11] * 16)
    esp_nonce = bytes([0x22] * 16)
    session_key, _, _ = derive_keys(TEST_KEY, ground_nonce, esp_nonce)
    hello_ack = {
        "type": "HELLO_ACK",
        "flags": 0,
        "session_id": SESSION,
        "seq": 1,
        "t_send_us": 0,
        "new_session_id": SESSION,
        "esp_nonce": esp_nonce.hex(),
        "fw_version": "v0.1.0",
    }
    state = {"current_session": SESSION}

    def tcp(msg_type: str, seq: int) -> str:
        fields = {
            "type": msg_type,
            "flags": 0,
            "session_id": SESSION,
            "seq": seq,
            "t_send_us": 0,
        }
        if msg_type == "ARM_CONTROL_REQUEST":
            fields["mask"] = 15
        return encode(fields).hex()

    return [
        valid("header_flags_bit0_ignored", "SETPOINT with header flags bit 0 set (was 'ack requested'): accepted, the bit is ignored",
              dict(REFERENCE, flags=1), state=state),
        valid("setpoint_flags_nonzero_ignored", "SETPOINT with SETPOINT.flags 0xFF: accepted, the flags are ignored",
              dict(REFERENCE, sp_flags=0xFF), state=state),
        valid("hello_ack_signed_with_psk", "HELLO_ACK is tagged with the PSK (here the test key), not the session key",
              hello_ack),
        rejected("hello_ack_signed_with_session_key", "The same HELLO_ACK tagged with the session key: refused when checked with the PSK",
                 frame(session_key, 0x02, payload(hello_ack), session_id=SESSION, seq=1, t_send_us=0), "bad_tag"),
        {
            "name": "tcp_and_udp_seq_apart",
            "description": "UDP and TCP count seq on their own. TCP starts at 1 (HELLO used seq 1). "
                           "UDP 1, TCP 2, UDP 2, TCP 3 are all accepted; then TCP 2 again is old",
            "key": TEST_KEY.hex(),
            "state": {"current_session": SESSION, "last_ctrl_seq": 0, "last_tcp_seq": 1},
            "steps": [
                {"transport": "udp", "bytes": neutral(1), "expect": "accept"},
                {"transport": "tcp", "bytes": tcp("ARM_CONTROL_REQUEST", 2), "expect": "accept"},
                {"transport": "udp", "bytes": neutral(2), "expect": "accept"},
                {"transport": "tcp", "bytes": tcp("RESUME_REQUEST", 3), "expect": "accept"},
                {"transport": "tcp", "bytes": tcp("ARM_CONTROL_REQUEST", 2), "expect": "reject:old_seq"},
            ],
            "final": {"last_ctrl_seq": 2, "last_tcp_seq": 3, "rx_rejected": 1, "gaps": 0},
        },
        {
            "name": "frame_on_the_wrong_transport",
            "description": "Both transports share the direction key, so each accepts only its own types (spec §2): "
                           "a TCP ARM_CONTROL_REQUEST replayed on UDP and a SETPOINT sent on TCP are refused and move no window",
            "key": TEST_KEY.hex(),
            "state": {"current_session": SESSION, "last_ctrl_seq": 0, "last_tcp_seq": 1},
            "steps": [
                {"transport": "tcp", "bytes": tcp("ARM_CONTROL_REQUEST", 2), "expect": "accept"},
                {"transport": "udp", "bytes": tcp("ARM_CONTROL_REQUEST", 2), "expect": "reject:wrong_transport"},
                {"transport": "tcp", "bytes": neutral(50), "expect": "reject:wrong_transport"},
                {"transport": "udp", "bytes": neutral(1), "expect": "accept"},
            ],
            "final": {"last_ctrl_seq": 1, "last_tcp_seq": 2, "rx_rejected": 2, "gaps": 0},
        },
        {
            "name": "frame_from_the_wrong_side",
            "description": "Each side accepts only what the other side sends (spec §2, #152), even with the same key: "
                           "the ESP refuses a STATE and an ARM_CONTROL_ACK, the ground a SETPOINT; BYE goes both ways",
            "key": TEST_KEY.hex(),
            "state": {"current_session": SESSION, "last_ctrl_seq": 0, "last_tcp_seq": 1},
            "steps": [
                {"role": "esp", "transport": "udp", "bytes": encode(msg("STATE", 5, t_send_us=0, **STATE_ZERO)).hex(),
                 "expect": "reject:wrong_direction"},
                {"role": "esp", "transport": "tcp", "bytes": encode(msg("ARM_CONTROL_ACK", 2, result=0)).hex(),
                 "expect": "reject:wrong_direction"},
                {"role": "ground", "transport": "udp", "bytes": neutral(5), "expect": "reject:wrong_direction"},
                {"role": "esp", "transport": "udp", "bytes": neutral(1), "expect": "accept"},
                {"role": "esp", "transport": "tcp", "bytes": encode(msg("BYE", 2, reason=0)).hex(), "expect": "accept"},
                {"role": "ground", "transport": "tcp", "bytes": encode(msg("BYE", 2, reason=0)).hex(), "expect": "accept"},
            ],
            "final": {"last_ctrl_seq": 1, "last_tcp_seq": 2, "rx_rejected": 3, "gaps": 0},
        },
    ]  # fmt: skip


def group9() -> list[dict]:
    """Before a session (spec §1, #152): the ESP accepts only HELLO (session 0,
    seq 1, 17 bytes), the ground only HELLO_ACK (seq 1, header session_id =
    payload session_id, not 0). Both tagged with the PSK (here the test key)."""
    ground_nonce = bytes(range(0xA0, 0xB0)).hex()
    esp_nonce = bytes(range(0xB0, 0xC0)).hex()
    hello = msg("HELLO", 1, 0, t_send_us=0, proto=1, ground_nonce=ground_nonce)
    ack = msg(
        "HELLO_ACK",
        1,
        t_send_us=0,
        new_session_id=SESSION,
        esp_nonce=esp_nonce,
        fw_version="v0.1.0",
    )

    def accepted(name: str, description: str, fields: dict, side: str) -> dict:
        return valid(name, description, fields, handshake=side)

    def refused(
        name: str, description: str, fields: dict, side: str, reason: str
    ) -> dict:
        return rejected(name, description, encode(fields), reason, handshake=side)

    return [
        accepted("hello_accepted_by_esp", "The ESP accepts a HELLO: session 0, seq 1, tagged with the PSK", hello, "esp"),
        accepted("hello_ack_accepted_by_ground", "The ground accepts a HELLO_ACK: the new session_id in the header and the payload, seq 1",
                 ack, "ground"),
        refused("hello_at_ground_rejected", "A HELLO sent back to the ground (reflected): the ground only accepts HELLO_ACK",
                hello, "ground", "wrong_direction"),
        refused("hello_ack_at_esp_rejected", "A HELLO_ACK sent back to the ESP (reflected): the ESP only accepts HELLO",
                ack, "esp", "wrong_direction"),
        refused("setpoint_before_session_rejected", "A SETPOINT tagged with the PSK before any session: not a handshake frame",
                setpoint(1, session_id=0), "esp", "wrong_direction"),
        refused("hello_with_session_id_rejected", "A HELLO with session_id 5: HELLO always has session_id 0",
                dict(hello, session_id=5), "esp", "wrong_session"),
        refused("hello_ack_session_id_mismatch_rejected", "A HELLO_ACK whose header session_id differs from the payload's",
                dict(ack, session_id=SESSION + 1), "ground", "wrong_session"),
        refused("hello_seq_2_rejected", "A HELLO with seq 2: HELLO and HELLO_ACK use seq 1",
                dict(hello, seq=2), "esp", "bad_seq"),
    ]  # fmt: skip


FILES = {
    "1_valid_frames.json": group1,
    "2_hmac.json": group2,
    "3_header.json": group3,
    "4_session.json": group4,
    "5_sequence.json": group5,
    "6_clamps.json": group6,
    "7_config.json": group7,
    "8_reserved_and_transports.json": group8,
    "9_handshake.json": group9,
}


def render(vectors: list[dict]) -> str:
    return json.dumps(vectors, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--check", action="store_true", help="only check that the files are up to date"
    )
    args = parser.parse_args(argv)

    groups = {file_name: make() for file_name, make in FILES.items()}
    names = [v["name"] for vectors in groups.values() for v in vectors]
    if len(names) != len(set(names)):
        raise SystemExit("duplicate vector names")
    stale = []
    for file_name, vectors in groups.items():
        path, text = HERE / file_name, render(vectors)
        if args.check:
            if not path.exists() or path.read_text() != text:
                stale.append(file_name)
        else:
            path.write_text(text)
            print(f"wrote {file_name}: {len(vectors)} vectors")
    if stale:
        print(
            f"Out of date: {', '.join(stale)}. Run drone/tests/vectors/generate.py.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
