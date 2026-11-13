"""HIL smoke test: the spare ESP boots the gateway (env esp) from flash, and
the FC's UART never sees MSP in the boot ROM output.

The spare ESP sits on the Pi's USB (/dev/hil-esp, FTDI auto-reset on
DTR/RTS). That it stays up after boot (only allow-listed MSP frames on UART0,
no crash dump, no reboot) is checked in gateway/test_uart.py.

Without the board the hardware test is skipped, unless HIL_REQUIRED=1 (set in
hil.yaml) turns the skip into a failure.
"""

from bench import (
    APP_BAUD,
    ROM_BAUD,
    boot_mode,
    check_frames,
    crash_markers,
    msp,
    open_esp,
    pulse_reset,
    read_for,
)

ROM_WINDOW_S = 2.0
BOOT_RESETS = 3
BOOT_WINDOW_S = 3.0
# MSP v1 and v2 frame starts, in every direction. Every valid MSP frame
# starts with one of these, so no parser is needed to find one
MSP_HEADERS = (b"$M<", b"$M>", b"$M!", b"$X<", b"$X>", b"$X!")


def msp_like(output: bytes) -> list[bytes]:
    """MSP frame starts in `output`: what Betaflight's MSP parser would act on."""
    return [h for h in MSP_HEADERS if h in output]


def test_boot_mode_parses_rom_banner():
    banner = b"\r\n ets Jan  8 2013,rst cause:2, boot mode:(3,6)\r\n\r\nload 0x4010f000"
    assert boot_mode(banner) == 3
    assert boot_mode(b"garbage") is None


def test_crash_markers_found():
    dump = b"\r\nException (28):\r\nepc1=0x40201234\r\n"
    assert crash_markers(dump) == [b"Exception ("]
    assert crash_markers(b"") == []


def test_msp_like_finds_headers_and_frames():
    assert msp_like(b"\r\n ets Jan  8 2013,rst cause:2, boot mode:(3,6)\r\n") == []
    assert msp_like(b"noise$M<\x00\x01\x01noise") == [b"$M<"]  # MSP_API_VERSION
    assert msp_like(b"$X>") == [b"$X>"]


def test_boot_bytes_never_look_like_msp():
    """ESP-S1-03: what the FC's UART sees at 115200 from the reset edge.

    The ROM banner at 74880 reads as noise at 115200, and Betaflight's MSP
    parser must never find a frame start in it (ADR-012, REQ-SAF-016). After
    the banner the gateway polls the FC on purpose (BOOT, nothing answers
    here): those must be allow-listed frames, with nothing between them.
    """
    port = open_esp()
    try:
        for reset in range(BOOT_RESETS):
            # The banner can only be read at 74880, the FC's view only at
            # 115200: so first prove this reset pulse gives a normal flash
            # boot (not the bootloader), then capture the FC's view of the
            # same pulse
            port.baudrate = ROM_BAUD
            pulse_reset(port)
            rom = read_for(port, ROM_WINDOW_S)
            assert boot_mode(rom) == 3, f"reset {reset}: no flash boot: {rom[:300]!r}"
            assert not crash_markers(
                rom
            ), f"reset {reset}: crash in ROM output: {rom!r}"

            port.baudrate = APP_BAUD
            pulse_reset(port)
            boot = read_for(port, BOOT_WINDOW_S)
            assert boot, f"reset {reset}: no boot output at all, did the reset work?"
            parser = msp.Parser()
            frames = [(0, f) for f in parser.feed(boot)]
            stream = b"".join(msp.encode_request(f.cmd, f.payload) for _, f in frames)
            start = boot.find(stream) if frames else len(boot)
            assert start >= 0, f"reset {reset}: bytes between MSP frames: {boot!r}"
            banner = boot[:start]
            assert (
                msp_like(banner) == []
            ), f"reset {reset}: MSP in boot ROM output: {banner!r}"
            check_frames(boot[start:], frames, parser.bad_checksums)
    finally:
        port.close()
