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
    "EVENT": 0x40,
    "SETPOINT": 0x60,
    "STATE": 0x80,
}

# Values that are suggested answers in GitHub #142 (spec §7.5), not frozen.
PENDING_142 = "Code value is a suggested answer in #142 (spec §7.5), not frozen yet."


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


def rejected(name: str, description: str, data: bytes, reason: str, **extra) -> dict:
    return {
        "name": name,
        "description": description,
        "key": TEST_KEY.hex(),
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


def group1() -> list[dict]:
    def msg(t: str, seq: int, session_id: int = SESSION, **values) -> dict:
        return {
            "type": t,
            "flags": 0,
            "session_id": session_id,
            "seq": seq,
            "t_send_us": seq * 1000,
            **values,
        }

    ground_nonce = bytes(range(0xA0, 0xB0)).hex()
    esp_nonce = bytes(range(0xB0, 0xC0)).hex()
    return [
        valid("hello_basic", "HELLO: protocol 1 and the ground nonce. Session 0, tagged with the PSK (here the test key)",
              msg("HELLO", 1, 0, proto=1, ground_nonce=ground_nonce)),
        valid("hello_ack_basic", "HELLO_ACK: the new session ID, the ESP nonce and the firmware version 'v0.1.0' padded with zero bytes to 12",
              msg("HELLO_ACK", 1, 0, new_session_id=SESSION, esp_nonce=esp_nonce, fw_version="v0.1.0"),
              note="Header session_id 0 and the PSK tag are suggested answers in #142 (spec §7.5)."),
        valid("arm_control_request_mask_15", "ARM_CONTROL_REQUEST with mask 15 (roll, pitch, yaw and throttle)",
              msg("ARM_CONTROL_REQUEST", 2, mask=15)),
        valid("arm_control_ack_ok", "ARM_CONTROL_ACK with result 0 (OK)", msg("ARM_CONTROL_ACK", 2, result=0), note=PENDING_142),
        valid("resume_request_basic", "RESUME_REQUEST: no payload, 28-byte frame", msg("RESUME_REQUEST", 3)),
        valid("resume_ack_ok", "RESUME_ACK with result 0 (OK)", msg("RESUME_ACK", 3, result=0), note=PENDING_142),
        valid("bye_normal", "BYE with reason 0 (normal)", msg("BYE", 9, reason=0), note=PENDING_142),
        valid("bye_replaced", "BYE with reason 3 (replaced by a new session): a non-zero code", msg("BYE", 10, reason=3),
              note=PENDING_142),
        valid("resume_ack_wrong_state", "RESUME_ACK with result 1 (wrong state): a non-zero code",
              msg("RESUME_ACK", 4, result=1), note=PENDING_142),
        valid("config_get_t_loss", "CONFIG_GET of key 0x40 t_loss_ms (value field 0)", msg("CONFIG_GET", 4, config_key=0x40, value=0)),
        valid("config_set_telemetry_rate", "CONFIG_SET of key 0x80 telemetry_rate_hz to 30",
              msg("CONFIG_SET", 5, config_key=0x80, value=30)),
        valid("event_failsafe_entered", "EVENT code 0x0102 (link failsafe entered) with a short text",
              msg("EVENT", 6, code=0x0102, text="no setpoint for 250 ms"), note=PENDING_142),
        valid("setpoint_attitude_basic", "The reference SETPOINT: mode 1, roll 100, pitch -200, yaw 0, throttle 500, seq 7",
              REFERENCE),
        valid("state_streaming", "STATE while streaming, every field a different value (negative roll and flow included)",
              msg("STATE", 42, esp_state=3, override_active=1, last_ctrl_seq=41, last_ctrl_t_send_us=820000,
                  roll=-15, pitch=30, heading=359, vbat=1110, flow_x=-300, flow_y=300, flow_quality=200,
                  flow_status=2, rx_rejected=7, msp_errors=5),
              note="esp_state 3 = STREAMING. " + PENDING_142),
    ]  # fmt: skip


def group2() -> list[dict]:
    psk = TEST_KEY
    ground_nonce = bytes([0x11] * 16)
    esp_nonce = bytes([0x22] * 16)
    session_key = hmac.new(
        psk, b"sess" + ground_nonce + esp_nonce, hashlib.sha256
    ).digest()
    good = encode(REFERENCE)
    payload_flip = bytearray(good)
    payload_flip[21] ^= 0x01  # roll 100 -> 101
    tag_flip = bytearray(good)
    tag_flip[-1] ^= 0x80
    return [
        valid("hello_signed_with_psk", "HELLO tagged with the PSK, as every HELLO is (spec §1)",
              {"type": "HELLO", "flags": 0, "session_id": 0, "seq": 1, "t_send_us": 0,
               "proto": 1, "ground_nonce": ground_nonce.hex()}, psk),
        valid("setpoint_signed_with_session_key",
              "SETPOINT tagged with the session key = HMAC-SHA256(PSK, 'sess' || ground_nonce || esp_nonce)",
              setpoint(1, roll=100, throttle=500), session_key,
              psk=psk.hex(), ground_nonce=ground_nonce.hex(), esp_nonce=esp_nonce.hex()),
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
        # Header and tag are good: the type, size and mode checks (§3, REQ-COM-001/002), run before the seq window.
        rejected("unknown_type", "Type 0x99 with a valid tag and session: not a §3 message type",
                 with_tag(header(0x99, 10, **h), sp), "bad_type", state=state),
        rejected("setpoint_payload_9_bytes", "SETPOINT with a 9-byte payload (needs 10), valid tag",
                 with_tag(header(0x60, 9, **h), sp[:9]), "bad_payload_size", state=state),
        rejected("event_text_65", "EVENT with 65 text bytes (2 + 65 = 67 > 66), valid tag",
                 with_tag(header(0x40, 67, **h), struct.pack("<H", 0x0102) + b"x" * 65), "bad_payload_size", state=state),
        rejected("setpoint_mode_2", "SETPOINT with mode 2 (only 0 neutral and 1 attitude exist), valid tag",
                 encode(dict(REFERENCE, mode=2)), "bad_mode", state=state,
                 note="reject:bad_mode is spec §4 rule 4 as decided in #142 (PR #148)."),
        {
            "name": "malformed_frame_keeps_seq",
            "description": "seq 1, then seq 5 with mode 2 (refused), then seq 2: the refused frame did not use up seq 5",
            "key": TEST_KEY.hex(),
            "state": {"current_session": SESSION, "last_ctrl_seq": 0},
            "steps": [
                {"bytes": neutral(1), "expect": "accept"},
                {"bytes": encode(setpoint(5, 100000, mode=2)).hex(), "expect": "reject:bad_mode"},
                {"bytes": neutral(2), "expect": "accept"},
            ],
            "final": {"last_ctrl_seq": 2, "rx_rejected": 1, "gaps": 0},
        },
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


FILES = {
    "1_valid_frames.json": group1,
    "2_hmac.json": group2,
    "3_header.json": group3,
    "4_session.json": group4,
    "5_sequence.json": group5,
    "6_clamps.json": group6,
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
