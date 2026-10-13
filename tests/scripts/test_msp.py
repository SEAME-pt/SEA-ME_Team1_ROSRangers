import pathlib
import struct
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))

import msp  # noqa: E402
from fc_cli import is_read_only  # noqa: E402


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


@pytest.mark.parametrize(
    "line,expected",
    [
        ("version", True),
        ("get failsafe", True),
        ("diff all", True),
        ("serial", True),
        ("serial 3 1 115200 57600 0 115200", False),
        ("set vtx_power = 1", False),
        ("save", False),
        ("feature -3D", False),
        ("feature", True),
        ("defaults", False),
    ],
)
def test_fc_cli_read_only_filter(line, expected):
    assert is_read_only(line) is expected
