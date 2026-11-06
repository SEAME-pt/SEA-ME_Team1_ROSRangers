"""Golden vectors of drone/tests/vectors/ (Protocol Spec v0 §6).

Every *.json file in that folder (one vector or a list) runs here. The files
are made by drone/tests/vectors/generate.py; field names follow its README §1a.
"""

import json
from pathlib import Path

import pytest

from sd_protocol import (
    FW_VERSION_SIZE,
    ConfigAck,
    ConfigKv,
    Event,
    HandshakeResult,
    Header,
    Hello,
    HelloAck,
    MsgType,
    Receiver,
    Role,
    RxError,
    Setpoint,
    State,
    Transport,
    decode_config_ack,
    decode_config_kv,
    decode_event,
    decode_frame,
    decode_hello,
    decode_hello_ack,
    decode_setpoint,
    decode_state,
    decode_u8_payload,
    derive_keys,
    encode_config_ack,
    encode_config_kv,
    encode_event,
    encode_frame,
    encode_hello,
    encode_hello_ack,
    encode_setpoint,
    encode_state,
    encode_u8_payload,
    is_accepted,
    receive_handshake,
    receives,
    setpoint_to_rc,
    transport_of,
)

VECTOR_DIR = Path(__file__).resolve().parents[4] / "tests" / "vectors"

_HEADER_FIELDS = ("flags", "session_id", "seq", "t_send_us")
_SETPOINT_FIELDS = ("mode", "roll", "pitch", "yaw_rate", "throttle")
_STATE_FIELDS = tuple(State.__dataclass_fields__)


def load_vectors() -> list[dict]:
    vectors = []
    for path in sorted(VECTOR_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        vectors.extend(data if isinstance(data, list) else [data])
    return vectors


VECTORS = load_vectors()


def _fw_version(text: str) -> bytes:
    return text.encode("ascii").ljust(FW_VERSION_SIZE, b"\0")


def _setpoint(f: dict) -> Setpoint:
    return Setpoint(*(f[k] for k in _SETPOINT_FIELDS), f.get("sp_flags", 0))


def _setpoint_fields(sp: Setpoint) -> dict:
    return {**{k: getattr(sp, k) for k in _SETPOINT_FIELDS}, "sp_flags": sp.flags}


def _u8(name: str):
    return (
        lambda f: encode_u8_payload(f[name]),
        lambda p: {name: decode_u8_payload(p)},
    )


def _config():
    return (
        lambda f: encode_config_kv(ConfigKv(f["config_key"], f["value"])),
        lambda p: (lambda m: {"config_key": m.key, "value": m.value})(
            decode_config_kv(p)
        ),
    )


# Message type -> (fields -> payload, payload -> fields). Names: README §1a.
CODECS = {
    "HELLO": (
        lambda f: encode_hello(Hello(f["proto"], bytes.fromhex(f["ground_nonce"]))),
        lambda p: (lambda m: {"proto": m.proto, "ground_nonce": m.ground_nonce.hex()})(decode_hello(p)),
    ),
    "HELLO_ACK": (
        lambda f: encode_hello_ack(
            HelloAck(f["new_session_id"], bytes.fromhex(f["esp_nonce"]), _fw_version(f["fw_version"]))
        ),
        lambda p: (
            lambda m: {
                "new_session_id": m.session_id,
                "esp_nonce": m.esp_nonce.hex(),
                "fw_version": m.fw_version.rstrip(b"\0").decode("ascii"),
            }
        )(decode_hello_ack(p)),
    ),
    "ARM_CONTROL_REQUEST": _u8("mask"),
    "ARM_CONTROL_ACK": _u8("result"),
    "RESUME_ACK": _u8("result"),
    "BYE": _u8("reason"),
    "RESUME_REQUEST": (lambda f: b"", lambda p: {} if p == b"" else None),
    "CONFIG_GET": _config(),
    "CONFIG_SET": _config(),
    "CONFIG_ACK": (
        lambda f: encode_config_ack(ConfigAck(f["config_key"], f["result"], f["value"])),
        lambda p: (lambda m: {"config_key": m.key, "result": m.result, "value": m.value})(decode_config_ack(p)),
    ),
    "EVENT": (
        lambda f: encode_event(Event(f["code"], f["text"].encode("ascii"))),
        lambda p: (lambda m: {"code": m.code, "text": m.text.decode("ascii")})(decode_event(p)),
    ),
    "SETPOINT": (lambda f: encode_setpoint(_setpoint(f)), lambda p: _setpoint_fields(decode_setpoint(p))),
    "STATE": (
        lambda f: encode_state(State(*(f[k] for k in _STATE_FIELDS))),
        lambda p: (lambda m: {k: getattr(m, k) for k in _STATE_FIELDS})(decode_state(p)),
    ),
}  # fmt: skip


def payload_fields(fields: dict) -> dict:
    """The payload part of a vector's fields (no header, no type)."""
    return {k: v for k, v in fields.items() if k != "type" and k not in _HEADER_FIELDS}


def result_name(key: bytes, buf: bytes, rx: Receiver | None) -> str:
    """'accept' or the README reject reason, which matches the enum names."""
    if rx is None:
        error, _ = decode_frame(buf, key)
        return "accept" if error is RxError.OK else f"reject:{error.name.lower()}"
    result, _ = rx.receive(buf)
    return "accept" if is_accepted(result) else f"reject:{result.name.lower()}"


Receivers = dict[tuple[Role, Transport], Receiver]


def receivers_for(vector: dict, key: bytes) -> Receivers | None:
    """One Receiver per side and transport with the vector's session open (README `state`)."""
    state = vector.get("state")
    if state is None:
        return None
    assert state.get("last_ctrl_seq", 0) == 0, "vectors start from a fresh session"
    assert state.get("last_tcp_seq", 1) == 1, "the TCP window starts after HELLO"
    rxs = {(r, t): Receiver(r, t) for r in Role for t in Transport}
    for rx in rxs.values():
        assert rx.start_session(state["current_session"], key)
    return rxs


def receiver_of(rxs: Receivers | None, buf: bytes) -> Receiver | None:
    """A single frame goes to the Receiver of the side and transport of its type
    (README): the ground for what only the ESP sends, else the ESP (also BYE and
    unknown types); unknown types to UDP."""
    if rxs is None:
        return None
    if len(buf) <= 3:
        return rxs[Role.ESP, Transport.UDP]
    t = buf[3]
    role = (
        Role.GROUND
        if receives(Role.GROUND, t) and not receives(Role.ESP, t)
        else Role.ESP
    )
    return rxs[role, transport_of(t) or Transport.UDP]


def test_names_are_unique():
    names = [v["name"] for v in VECTORS]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("vector", VECTORS, ids=lambda v: v["name"])
def test_vector(vector):
    key = bytes.fromhex(vector["key"])
    rxs = receivers_for(vector, key)

    if "steps" in vector:
        for i, step in enumerate(vector["steps"]):
            buf = bytes.fromhex(step["bytes"])
            role = Role[step.get("role", "esp").upper()]
            transport = Transport[step.get("transport", "udp").upper()]
            assert (
                result_name(key, buf, rxs[role, transport]) == step["expect"]
            ), f"step {i} ({role.name}, {transport.name})"
        final = vector.get("final", {})
        if "last_ctrl_seq" in final:
            assert rxs[Role.ESP, Transport.UDP].last_seq == final["last_ctrl_seq"]
        if "last_tcp_seq" in final:
            assert rxs[Role.ESP, Transport.TCP].last_seq == final["last_tcp_seq"]
        if "rx_rejected" in final:  # totals over every Receiver
            assert (
                sum(rx.counters.rejected for rx in rxs.values()) == final["rx_rejected"]
            )
        if "gaps" in final:
            assert sum(rx.seq_counters.gaps for rx in rxs.values()) == final["gaps"]
        return

    buf = bytes.fromhex(vector["bytes"])
    if "handshake" in vector:  # group 9: one side checks a frame before any session
        result, frame = receive_handshake(Role[vector["handshake"].upper()], buf, key)
        got = (
            "accept"
            if result is HandshakeResult.ACCEPTED
            else f"reject:{result.name.lower()}"
        )
        assert got == vector["expect"]
        assert (frame is None) is (result is not HandshakeResult.ACCEPTED)
    else:
        assert result_name(key, buf, receiver_of(rxs, buf)) == vector["expect"]

    fields = vector.get("fields")
    if fields is None:
        return
    # Encode: fields -> exactly bytes.
    encode, decode = CODECS[fields["type"]]
    header = Header(
        MsgType[fields["type"]], **{k: fields[k] for k in _HEADER_FIELDS if k in fields}
    )
    assert encode_frame(header, encode(fields), key) == buf

    # Decode: bytes -> exactly fields.
    _, frame = decode_frame(buf, key)
    assert frame.header.type == MsgType[fields["type"]]
    for k in _HEADER_FIELDS:
        if k in fields:
            assert getattr(frame.header, k) == fields[k], k
    expected = payload_fields(fields)
    if fields["type"] == "SETPOINT":
        expected.setdefault("sp_flags", 0)
    assert decode(frame.payload) == expected

    if "channels" in vector:
        assert setpoint_to_rc(decode_setpoint(frame.payload)) == vector["channels"]


@pytest.mark.parametrize(
    "vector", [v for v in VECTORS if "psk" in v], ids=lambda v: v["name"]
)
def test_session_key_derivation(vector):
    """Group 2: the vector's key is the direction key ("g2e" or "e2g") that
    derive_keys() gives for the PSK and the nonces (#150, #152)."""
    keys = derive_keys(
        bytes.fromhex(vector["psk"]),
        bytes.fromhex(vector["ground_nonce"]),
        bytes.fromhex(vector["esp_nonce"]),
    )
    key = {"g2e": keys.ground_to_esp, "e2g": keys.esp_to_ground}[vector["direction"]]
    assert key.hex() == vector["key"]


def test_every_message_type_has_a_codec():
    assert set(CODECS) == {t.name for t in MsgType}
