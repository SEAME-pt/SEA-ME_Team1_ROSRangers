"""HIL smoke test: the spare ESP boots the flashed firmware and stays up.

The spare ESP sits on the Pi's USB (/dev/hil-esp, FTDI auto-reset on
DTR/RTS). The firmware prints nothing on UART0, so after the boot ROM banner
(74880 baud) the line must stay silent at 115200: any bytes there are a crash
dump or a reboot.

Without the board the hardware test is skipped, unless HIL_REQUIRED=1 (set in
hil.yaml) turns the skip into a failure.
"""

from bench import (
    APP_BAUD,
    ROM_BAUD,
    boot_mode,
    crash_markers,
    open_esp,
    pulse_reset,
    read_for,
)

ROM_WINDOW_S = 2.0
SILENT_WINDOW_S = 10.0
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


def test_esp_boots_from_flash_and_stays_up():
    port = open_esp()
    try:
        pulse_reset(port)

        rom = read_for(port, ROM_WINDOW_S)
        assert boot_mode(rom) == 3, f"no normal flash boot in ROM output: {rom[:300]!r}"
        assert not crash_markers(rom), f"crash in ROM output: {rom!r}"

        port.baudrate = APP_BAUD
        port.reset_input_buffer()
        after = read_for(port, SILENT_WINDOW_S)
        assert after == b"", (
            f"UART0 should be silent after boot, got {len(after)} bytes "
            f"(markers {crash_markers(after)}): {after[:300]!r}"
        )
    finally:
        port.close()


def test_boot_bytes_never_look_like_msp():
    """ESP-S1-03: what the FC's UART sees at 115200 from the reset edge.

    The ROM banner at 74880 reads as noise at 115200, and Betaflight's MSP
    parser must never find a frame start in it (ADR-012, REQ-SAF-016). With
    the gateway firmware (#101) the ESP sends MSP_API_VERSION requests at
    boot on purpose: this test is then replaced by a check of the BOOT and
    NO_SESSION output (#106).
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

            port.baudrate = APP_BAUD
            pulse_reset(port)
            boot = read_for(port, BOOT_WINDOW_S)
            assert boot, f"reset {reset}: no boot output at all, did the reset work?"
            assert msp_like(boot) == [], f"reset {reset}: MSP in boot output: {boot!r}"
    finally:
        port.close()
