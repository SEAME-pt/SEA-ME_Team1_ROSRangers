"""HIL: the gateway firmware (#101, env esp_gateway) on the FC's UART.

USB only, no Wi-Fi. hil.yaml runs this folder after flashing esp_gateway; it
must not run against the bridge (env esp). The Pi plays the FC (bench.FakeFc).

What the gateway does today: it polls MSP_ATTITUDE at 25 Hz and MSP_STATUS /
MSP_ANALOG at 5 Hz (ADR-009), one request at a time, and sends nothing else.
Once #102 adds BOOT / NO_SESSION (MSP_API_VERSION, neutral MSP_SET_RAW_RC at
50 Hz), ALLOWED and the rates below change with it.
"""

import time

import pytest
from bench import (
    APP_BAUD,
    FAKE_ANSWERS,
    FAKE_ARMING_FLAGS,
    FAKE_ROLL,
    FAKE_VBAT_CV,
    ROM_BAUD,
    FakeFc,
    boot_mode,
    crash_markers,
    msp,
    open_esp,
    pulse_reset,
    read_for,
    stray_bytes,
)

ALLOWED = {msp.MSP_STATUS, msp.MSP_ATTITUDE, msp.MSP_ANALOG}
RECORD_S = 4.0


@pytest.fixture(scope="module")
def esp():
    port = open_esp(ROM_BAUD)
    try:
        pulse_reset(port)
        rom = read_for(port, 2.0)
        assert boot_mode(rom) == 3, f"no normal flash boot: {rom[:300]!r}"
        assert not crash_markers(rom), f"crash in ROM output: {rom!r}"
        port.baudrate = APP_BAUD
        yield port
    finally:
        port.close()


def record(esp, answering: bool):
    with FakeFc(esp) as fc:
        fc.answering = answering
        time.sleep(1.0)  # let the scheduler settle (timeouts, drain)
        fc.clear()
        time.sleep(RECORD_S)
        return fc.snapshot()


def check_only_allowed_polls(raw, frames, bad):
    assert not crash_markers(raw), f"crash on UART0: {raw[:300]!r}"
    assert bad == 0, f"{bad} frames with a bad checksum"
    assert frames, "the gateway sent nothing: is esp_gateway flashed?"
    # Every byte on the FC's UART is part of an MSP frame: no text, no dump
    stray = stray_bytes(raw, frames)
    assert stray == 0, f"{stray} bytes outside MSP frames: {raw[:300]!r}"
    assert {f.direction for _, f in frames} == {"<"}, "the gateway only sends requests"
    sent = {f.cmd for _, f in frames}
    assert sent <= ALLOWED, f"commands outside the poll list: {sent - ALLOWED}"
    assert all(f.payload == b"" for _, f in frames), "polls carry no payload"


def rate(frames, cmd) -> float:
    return sum(f.cmd == cmd for _, f in frames) / RECORD_S


def test_fake_answers_decode():
    """Host check (no bench): the fake FC's replies parse as Betaflight's."""
    status = msp.decode_status(FAKE_ANSWERS[msp.MSP_STATUS])
    assert status["armed"] is False
    assert status["arming_disable_flags"] == FAKE_ARMING_FLAGS
    attitude = msp.decode(msp.MSP_ATTITUDE, FAKE_ANSWERS[msp.MSP_ATTITUDE])
    assert attitude["roll_deg"] == FAKE_ROLL / 10
    analog = msp.decode(msp.MSP_ANALOG, FAKE_ANSWERS[msp.MSP_ANALOG])
    assert analog["voltage_v"] == FAKE_VBAT_CV / 100


def test_stray_bytes_counts_only_bytes_between_frames():
    """Host check (no bench)."""
    att, status = (msp.encode_request(c) for c in (msp.MSP_ATTITUDE, msp.MSP_STATUS))
    frames = [(0, f) for f in msp.Parser().feed(att + status)]
    assert stray_bytes(att + status, frames) == 0
    # A recording that starts and ends inside a frame
    cut = att[1:] + att + status + att[:5]
    assert stray_bytes(cut, [(0, f) for f in msp.Parser().feed(cut)]) == 0
    # Text between two frames, or before the first one
    assert stray_bytes(att + b"ok\n" + status, frames) > 0
    assert stray_bytes(b"Exception (28)" + att, frames[:1]) == 14 - 5


def test_answered_polls_run_at_their_rates(esp):
    raw, frames, bad = record(esp, answering=True)
    check_only_allowed_polls(raw, frames, bad)
    # ADR-009: 25 Hz and 5 Hz at most. One request in flight at a time (and
    # the Pi's USB round trip) can only delay a poll, never add one, so the
    # upper bounds are tight and the lower ones loose.
    assert 15 <= rate(frames, msp.MSP_ATTITUDE) <= 26
    assert 3 <= rate(frames, msp.MSP_STATUS) <= 5.5
    assert 3 <= rate(frames, msp.MSP_ANALOG) <= 5.5


def test_silent_fc_gets_only_polls(esp):
    raw, frames, bad = record(esp, answering=False)
    check_only_allowed_polls(raw, frames, bad)
    # Each request waits 50 ms for its reply, then 50 ms of drain
    # (sd/fc_link.hpp): at most 10 requests/s, and it keeps trying.
    total = len(frames) / RECORD_S
    assert 5 <= total <= 11, f"{total:.1f} requests/s with a silent FC"
