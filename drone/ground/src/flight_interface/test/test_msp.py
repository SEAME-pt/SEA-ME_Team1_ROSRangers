"""Unit tests for the MSP v1 client (msp.py). No ROS and no drone needed.

Run with:  python3 -m pytest test -v                      (from the package folder)
or:        colcon test --packages-select flight_interface  (uses pytest)
"""

import struct

import pytest

from flight_interface import msp


def test_api_version_request_matches_known_bytes():
    assert msp.encode_request(msp.MSP_API_VERSION) == bytes.fromhex("244d3c000101")



def test_request_rejects_out_of_range():
    with pytest.raises(ValueError):
        msp.encode_request(256)
    with pytest.raises(ValueError):
        msp.encode_request(1, bytes(256))



def test_parser_roundtrip_and_split_feed():
    frame = msp.encode_response(msp.MSP_API_VERSION, bytes([0, 1, 46]))
    p = msp.Parser()
    assert p.feed(frame[:4]) == []
    (f,) = p.feed(frame[4:])
    assert (f.direction, f.cmd, f.payload) == (
        ">",
        msp.MSP_API_VERSION,
        bytes([0, 1, 46]),
    )



def test_parser_skips_noise_and_counts_bad_checksum():
    good = msp.encode_response(msp.MSP_FC_VARIANT, b"BTFL")
    bad = bytearray(good)
    bad[-1] ^= 0xFF
    p = msp.Parser()
    frames = p.feed(b"\x00garbage$" + bytes(bad) + good)
    assert [f.payload for f in frames] == [b"BTFL"]
    assert p.bad_checksums == 1



def test_parser_error_frame():
    (f,) = msp.Parser().feed(msp.encode_response(200, error=True))
    assert f.direction == "!" and f.cmd == 200



def test_decoders():
    assert msp.decode(msp.MSP_API_VERSION, bytes([0, 1, 46])) == {
        "protocol": 0,
        "api": "1.46",
    }
    att = msp.decode(msp.MSP_ATTITUDE, struct.pack("<hhh", -123, 45, 270))
    assert att == {"roll_deg": -12.3, "pitch_deg": 4.5, "yaw_deg": 270}
    st = msp.decode(msp.MSP_STATUS, struct.pack("<HHHI", 125, 0, 0x23, 1))
    assert st["armed"] is True and st["cycle_time_us"] == 125



def test_client_refuses_write_commands():
    class Dummy:
        def send(self, data):
            raise AssertionError("must not send")

        def recv(self):
            return b""

        def close(self):
            pass

    with pytest.raises(ValueError):
        msp.Client(Dummy()).request(200)  # MSP_SET_RAW_RC



def test_client_trace_is_disabled_by_default():
    class Dummy:
        def send(self, data):
            pass

        def recv(self):
            return msp.encode_response(msp.MSP_API_VERSION, bytes([0, 1, 46]))

        def close(self):
            pass

    frame, _ = msp.Client(Dummy()).request(msp.MSP_API_VERSION)
    assert frame.payload == bytes([0, 1, 46])



def test_decode_status_reads_betaflight_extended_fields():
    payload = (
        struct.pack("<HHHI", 125, 2, 0x23, 1)
        + bytes([0])  # PID profile
        + struct.pack("<H", 17)  # CPU load
        + struct.pack("<H", 0)  # gyro cycle time
        + bytes([1, 0xAA])  # one extra flight-mode byte
        + bytes([26])  # arming disable flag count
        + struct.pack("<I", 0x40000)
        + bytes([1])  # reboot required
    )
    st = msp.decode_status(payload)
    assert st["armed"] and st["i2c_errors"] == 2 and st["cpu_load"] == 17
    assert st["arming_disable_flags"] == 0x40000
    assert st["reboot_required"] is True



def test_attitude_reply_bytes_decode_to_degrees():
    # Raw MSP_ATTITUDE replies as the FC sends them: roll and pitch in 0.1 deg,
    # yaw in whole degrees. The first matches the reading of 2026-10-23.
    parser = msp.Parser()
    frames = parser.feed(
        bytes.fromhex("244d3e066cb1065c005100d0")
        + bytes.fromhex("244d3e066c3bfe81ff6701b7")
    )
    assert [f.cmd for f in frames] == [msp.MSP_ATTITUDE, msp.MSP_ATTITUDE]
    assert msp.decode(frames[0].cmd, frames[0].payload) == {
        "roll_deg": 171.3,
        "pitch_deg": 9.2,
        "yaw_deg": 81,
    }
    assert msp.decode(frames[1].cmd, frames[1].payload) == {
        "roll_deg": -45.3,
        "pitch_deg": -12.7,
        "yaw_deg": 359,
    }
    assert parser.bad_checksums == 0



def test_rc_payload_roundtrip_and_analog_rssi():
    channels = [1500, 1000, 2000, 1234]
    assert msp.decode(msp.MSP_RC, msp.encode_rc(channels))["channels"] == channels
    analog = bytes([168]) + struct.pack("<HHh", 0, 512, 0)
    assert msp.decode(msp.MSP_ANALOG, analog) == {"vbat_v": 16.8, "rssi": 512}
