"""Unit tests for msp_frames. They run without SITL."""

from msp_frames import (
    FROM_FC,
    MSP_API_VERSION,
    crc8_dvb_s2,
    decode,
    encode_v1,
    encode_v2,
)


def test_crc8_dvb_s2_check_value():
    # Standard CRC-8/DVB-S2 check value
    assert crc8_dvb_s2(b"123456789") == 0xBC


def test_encode_v1_api_version_request():
    assert encode_v1(MSP_API_VERSION) == bytes([0x24, 0x4D, 0x3C, 0x00, 0x01, 0x01])


def test_encode_v2_api_version_request():
    assert encode_v2(MSP_API_VERSION) == bytes([0x24, 0x58, 0x3C, 0x00, 0x01, 0x00, 0x00, 0x00, 0x45])


def test_decode_round_trip_v1_and_v2():
    for encode, version in ((encode_v1, 1), (encode_v2, 2)):
        frame, used = decode(encode(MSP_API_VERSION, b"\x00\x01\x2f", FROM_FC))
        assert frame is not None
        assert (frame.version, frame.direction, frame.cmd, frame.payload) == (version, FROM_FC, 1, b"\x00\x01\x2f")
        assert used == len(encode(MSP_API_VERSION, b"\x00\x01\x2f", FROM_FC))


def test_decode_skips_noise_before_frame():
    good = encode_v2(MSP_API_VERSION, b"\x07", FROM_FC)
    buf = b"noise" + good
    frame, used = decode(buf)
    assert frame is not None and frame.payload == b"\x07"
    assert used == len(buf)
    assert decode(b"noise only") == (None, len(b"noise only"))


def test_decode_drops_bad_crc():
    bad = bytearray(encode_v2(MSP_API_VERSION, b"\x07", FROM_FC))
    bad[-1] ^= 0xFF
    assert decode(bytes(bad)) == (None, len(bad))


def test_decode_waits_for_partial_frame():
    good = encode_v1(MSP_API_VERSION, b"\x00\x01\x2f", FROM_FC)
    assert decode(good[:-1]) == (None, 0)
