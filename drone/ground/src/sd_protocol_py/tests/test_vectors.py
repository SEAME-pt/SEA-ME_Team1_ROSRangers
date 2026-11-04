"""Golden vectors in the format of drone/tests/vectors/README.md (Protocol Spec v0 §6).

README_EXAMPLES are the worked examples of that README, copied as they are. Any
*.json file added to drone/tests/vectors/ (one vector or a list) runs too.
"""

import json
from pathlib import Path

import pytest

from sd_protocol import (
    Header,
    MsgType,
    Receiver,
    RxError,
    Setpoint,
    decode_frame,
    decode_setpoint,
    encode_frame,
    encode_setpoint,
    is_accepted,
    setpoint_to_rc,
)

VECTOR_DIR = Path(__file__).resolve().parents[4] / "tests" / "vectors"
KEY = "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"
SESSION = 305419896  # 0x12345678

README_EXAMPLES = [
    {
        "name": "setpoint_attitude_basic",
        "key": KEY,
        "fields": {"type": "SETPOINT", "flags": 0, "session_id": SESSION, "seq": 7, "t_send_us": 1000,
                   "mode": 1, "roll": 100, "pitch": -200, "yaw_rate": 0, "throttle": 500, "sp_flags": 0},
        "bytes": "5344016000000a007856341207000000e803000001640038ff0000f40100fb703d824f5b589e",
        "expect": "accept",
    },
    {
        "name": "setpoint_payload_bit_flipped",
        "key": KEY,
        "bytes": "5344016000000a007856341207000000e803000001650038ff0000f40100fb703d824f5b589e",
        "expect": "reject:bad_tag",
    },
    {
        "name": "wrong_magic",
        "key": KEY,
        "bytes": "5345016000000a007856341207000000e803000001640038ff0000f40100de307f06c4dd38a3",
        "expect": "reject:bad_magic",
    },
    {
        "name": "wrong_session_id",
        "key": KEY,
        "state": {"current_session": SESSION},
        "bytes": "5344016000000a007956341207000000e803000001640038ff0000f40100b6785492183eae61",
        "expect": "reject:wrong_session",
    },
    {
        "name": "seq_duplicate",
        "key": KEY,
        "state": {"current_session": SESSION, "last_ctrl_seq": 0},
        "steps": [
            {"bytes": "5344016000000a007856341201000000204e0000010000000000000000006c870020e780aa97", "expect": "accept"},
            {"bytes": "5344016000000a007856341202000000409c000001000000000000000000712720248f2ea86c", "expect": "accept"},
            {"bytes": "5344016000000a007856341202000000409c000001000000000000000000712720248f2ea86c", "expect": "reject:duplicate_seq"},
            {"bytes": "5344016000000a00785634120300000060ea00000100000000000000000074f74adcb71e36f6", "expect": "accept"},
        ],
        "final": {"last_ctrl_seq": 3, "rx_rejected": 1},
    },
    {
        "name": "clamp_over",
        "key": KEY,
        "fields": {"type": "SETPOINT", "session_id": SESSION, "seq": 1, "t_send_us": 0,
                   "mode": 1, "roll": 1500, "pitch": 0, "yaw_rate": 0, "throttle": 500, "sp_flags": 0},
        "bytes": "5344016000000a0078563412010000000000000001dc0500000000f40100aef509cd5dc0dca2",
        "expect": "accept",
        "channels": [2000, 1500, 1500, 1500, 1500, 1500, 1500, 1500],
    },
]  # fmt: skip

_HEADER_FIELDS = ("flags", "session_id", "seq", "t_send_us")
_SETPOINT_FIELDS = ("mode", "roll", "pitch", "yaw_rate", "throttle")


def load_vectors() -> list[dict]:
    vectors = list(README_EXAMPLES)
    for path in sorted(VECTOR_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        vectors.extend(data if isinstance(data, list) else [data])
    return vectors


VECTORS = load_vectors()


def payload_from_fields(fields: dict) -> bytes:
    if fields["type"] != "SETPOINT":
        pytest.skip(f"no Python codec for {fields['type']} yet (#100 part 4)")
    return encode_setpoint(
        Setpoint(*(fields[k] for k in _SETPOINT_FIELDS), fields.get("sp_flags", 0))
    )


def result_name(key: bytes, buf: bytes, rx: Receiver | None) -> str:
    """'accept' or the README reject reason, which matches the enum names."""
    if rx is None:
        error, _ = decode_frame(buf, key)
        return "accept" if error is RxError.OK else f"reject:{error.name.lower()}"
    result, _ = rx.receive(buf)
    return "accept" if is_accepted(result) else f"reject:{result.name.lower()}"


def receiver_for(vector: dict, key: bytes) -> Receiver | None:
    state = vector.get("state")
    if state is None:
        return None
    rx = Receiver()
    assert rx.start_session(state["current_session"], key)
    assert state.get("last_ctrl_seq", 0) == 0, "vectors start from a fresh session"
    return rx


def test_names_are_unique():
    names = [v["name"] for v in VECTORS]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("vector", VECTORS, ids=lambda v: v["name"])
def test_vector(vector):
    key = bytes.fromhex(vector["key"])
    rx = receiver_for(vector, key)

    if "steps" in vector:
        for i, step in enumerate(vector["steps"]):
            buf = bytes.fromhex(step["bytes"])
            assert result_name(key, buf, rx) == step["expect"], f"step {i}"
        final = vector.get("final", {})
        if "last_ctrl_seq" in final:
            assert rx.last_seq == final["last_ctrl_seq"]
        if "rx_rejected" in final:
            assert rx.counters.rejected == final["rx_rejected"]
        return

    buf = bytes.fromhex(vector["bytes"])
    assert result_name(key, buf, rx) == vector["expect"]

    fields = vector.get("fields")
    if fields is None:
        return
    # Encode: fields -> exactly bytes.
    payload = payload_from_fields(fields)
    header = Header(
        MsgType[fields["type"]], **{k: fields[k] for k in _HEADER_FIELDS if k in fields}
    )
    assert encode_frame(header, payload, key) == buf

    # Decode: bytes -> exactly fields.
    _, frame = decode_frame(buf, key)
    assert frame.header.type == MsgType[fields["type"]]
    for k in _HEADER_FIELDS:
        if k in fields:
            assert getattr(frame.header, k) == fields[k], k
    sp = decode_setpoint(frame.payload)
    for k in _SETPOINT_FIELDS:
        assert getattr(sp, k) == fields[k], k
    assert sp.flags == fields.get("sp_flags", 0)

    if "channels" in vector:
        assert setpoint_to_rc(sp) == vector["channels"]
