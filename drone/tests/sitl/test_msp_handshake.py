"""MSP integration tests against Betaflight 2026.6.2 SITL (Testing Strategy level 2).

Handshake with MSP_API_VERSION over v1 and v2, then malformed frames: SITL
must ignore them and still answer the next valid frame.
"""

from msp_frames import (
    FROM_FC,
    MSP_API_VERSION,
    encode_v1,
    encode_v2,
)

# Silence window after a frame SITL must ignore
IGNORE_WINDOW_S = 0.5


def assert_api_version_reply(frame, version):
    assert frame is not None, "no reply to MSP_API_VERSION"
    assert frame.version == version
    assert frame.direction == FROM_FC
    assert frame.cmd == MSP_API_VERSION
    # protocol version, API major, API minor
    assert len(frame.payload) == 3, frame.payload
    _protocol, major, minor = frame.payload
    assert major == 1, f"unexpected API {major}.{minor}"


def test_api_version_v1(msp):
    msp.send(encode_v1(MSP_API_VERSION))
    assert_api_version_reply(msp.read_frame(), version=1)


def test_api_version_v2(msp):
    msp.send(encode_v2(MSP_API_VERSION))
    assert_api_version_reply(msp.read_frame(), version=2)


def test_v2_bad_crc_is_ignored(msp):
    bad = bytearray(encode_v2(MSP_API_VERSION))
    bad[-1] ^= 0xFF
    msp.send(bytes(bad))
    assert msp.read_frame(timeout=IGNORE_WINDOW_S) is None, "SITL answered a frame with a bad CRC"

    msp.send(encode_v2(MSP_API_VERSION))
    assert_api_version_reply(msp.read_frame(), version=2)


def test_v1_bad_checksum_is_ignored(msp):
    bad = bytearray(encode_v1(MSP_API_VERSION))
    bad[-1] ^= 0xFF
    msp.send(bytes(bad))
    assert msp.read_frame(timeout=IGNORE_WINDOW_S) is None, "SITL answered a frame with a bad checksum"

    msp.send(encode_v1(MSP_API_VERSION))
    assert_api_version_reply(msp.read_frame(), version=1)


def test_line_noise_is_ignored(msp):
    # No '#' (enters the CLI) and no 'R' (reboot_character) in the noise
    msp.send(b"\x00\xff noise without a frame start \x55\xaa")
    assert msp.read_frame(timeout=IGNORE_WINDOW_S) is None, "SITL answered line noise"

    msp.send(encode_v2(MSP_API_VERSION))
    assert_api_version_reply(msp.read_frame(), version=2)
