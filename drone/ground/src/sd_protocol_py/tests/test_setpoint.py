from sd_protocol import (
    RC_CHANNELS,
    SETPOINT_SIZE,
    Setpoint,
    decode_setpoint,
    encode_setpoint,
    setpoint_to_rc,
)

# Same cases as drone/lib/protocol/tests/test_setpoint.cpp.


def rc(roll: int, pitch: int, yaw: int, throttle: int) -> list[int]:
    return setpoint_to_rc(Setpoint(1, roll, pitch, yaw, throttle))


def test_round_trip():
    sp = Setpoint(1, -1000, 1000, -1, 1000, 0)
    payload = encode_setpoint(sp)
    assert len(payload) == SETPOINT_SIZE
    assert decode_setpoint(payload) == sp


def test_reference_payload_bytes():
    # Payload of the reference frame in drone/tests/vectors/README.md §2.
    sp = Setpoint(1, 100, -200, 0, 500)
    assert encode_setpoint(sp) == bytes.fromhex("01640038ff0000f40100")


def test_wrong_sizes_refused():
    payload = bytes(SETPOINT_SIZE + 1)
    assert decode_setpoint(payload[:-2]) is None
    assert decode_setpoint(payload) is None


# Group 6 (clamps and mapping), spec §4 rule 4 and §5. Order: roll, pitch,
# throttle, yaw, aux1-4.
def test_map_min():
    assert rc(-1000, -1000, -1000, 0)[0] == 1000


def test_map_center():
    assert rc(0, 0, 0, 0)[:4] == [1500, 1500, 1000, 1500]


def test_map_max():
    assert rc(1000, 0, 0, 0)[0] == 2000


def test_clamp_over():
    ch = rc(1500, -32768, 32767, 500)
    assert ch[:4] == [2000, 1000, 1500, 2000]


def test_rounds_toward_the_centre_like_the_cpp():
    assert rc(-1, 1, -3, 0)[:4] == [1500, 1500, 1000, 1499]
    assert rc(3, 0, 0, 0)[0] == 1501


def test_throttle_map():
    assert rc(0, 0, 0, 0)[2] == 1000
    assert rc(0, 0, 0, 1000)[2] == 2000
    assert rc(0, 0, 0, -50)[2] == 1000
    assert rc(0, 0, 0, 1200)[2] == 2000


def test_aux_always_neutral():
    ch = rc(1000, -1000, 1000, 1000)
    assert len(ch) == RC_CHANNELS
    assert ch[4:] == [1500] * 4


def test_axis_order_is_aetr():
    assert rc(1000, -1000, 0, 1000)[:4] == [2000, 1000, 2000, 1500]
