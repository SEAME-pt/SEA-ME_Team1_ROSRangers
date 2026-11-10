"""HIL: the gateway firmware (#101, #102, env esp_gateway) on the FC's UART.

USB only, no Wi-Fi, so no session ever opens: these cover BOOT and
NO_SESSION of the ESP Link State Machine. hil.yaml runs this folder after
flashing esp_gateway; it must not run against the bridge (env esp). The Pi
plays the FC (bench.FakeFc).

The gateway polls MSP_ATTITUDE (25 Hz), MSP_STATUS / MSP_ANALOG (5 Hz) and
MSP_API_VERSION (1 Hz), one request at a time (ADR-009). It sends no RC until
the FC has answered MSP_API_VERSION (BOOT, REQ-SAF-016), then neutral
MSP_SET_RAW_RC at 50 Hz ahead of the polls (NO_SESSION).
"""

import time

import pytest
from bench import (
    APP_BAUD,
    FAKE_ANSWERS,
    FAKE_ARMING_FLAGS,
    FAKE_ROLL,
    FAKE_VBAT_CV,
    MAX_CUT,
    NEUTRAL,
    POLLS,
    RC,
    ROM_BAUD,
    FakeFc,
    Setpoint,
    boot_mode,
    check_frames,
    crash_markers,
    msp,
    open_esp,
    pulse_reset,
    read_for,
    setpoint_to_rc,
    stray_bytes,
)

RECORD_S = 4.0


@pytest.fixture(scope="module")
def esp():
    port = open_esp(ROM_BAUD)
    try:
        # Reset with no fake FC running: the gateway starts in BOOT, and the
        # first test sees it before anything answered MSP_API_VERSION.
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
        time.sleep(1.0)  # let the scheduler settle (timeouts, drain, BOOT exit)
        fc.clear()
        time.sleep(RECORD_S)
        return fc.snapshot()


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
    api = msp.decode(msp.MSP_API_VERSION, FAKE_ANSWERS[msp.MSP_API_VERSION])
    assert api["api"] == "1.46"
    # Neutral is centred sticks and throttle low, in AETR order (ADR-004)
    assert setpoint_to_rc(Setpoint(0, 0, 0, 0, 0))[:4] == [1500, 1500, 1000, 1500]


def test_stray_bytes_counts_only_bytes_between_frames():
    """Host check (no bench)."""
    att, status = (msp.encode_request(c) for c in (msp.MSP_ATTITUDE, msp.MSP_STATUS))
    rc = msp.encode_request(RC, NEUTRAL)
    frames = [(0, f) for f in msp.Parser().feed(att + rc + status)]
    assert stray_bytes(att + rc + status, frames) == 0
    # A recording that starts and ends inside a frame (an RC frame is the longest)
    cut = rc[1:] + att + rc + status + rc[:-1]
    assert stray_bytes(cut, [(0, f) for f in msp.Parser().feed(cut)]) == 0
    # Text between two frames, or more than a cut frame before the first one
    assert stray_bytes(att + b"ok\n" + rc + status, frames) > 0
    assert stray_bytes(b"x" * (MAX_CUT + 3) + att, frames[:1]) == 3


def test_boot_with_a_silent_fc_sends_no_rc(esp):
    """BOOT: nothing answered MSP_API_VERSION since the reset, so no RC at all
    (REQ-SAF-016), only polls."""
    raw, frames, bad = record(esp, answering=False)
    check_frames(raw, frames, bad)
    assert rate(frames, RC) == 0, "RC sent before the FC answered MSP_API_VERSION"
    assert rate(frames, msp.MSP_API_VERSION) > 0, "BOOT must keep asking the FC"
    # Each request waits 50 ms for its reply, then 50 ms of drain
    total = len(frames) / RECORD_S
    assert 5 <= total <= 11, f"{total:.1f} requests/s with a silent FC"


def test_no_session_sends_neutral_rc_at_50_hz(esp):
    """NO_SESSION: once the FC answers, neutral RC at 50 Hz goes ahead of the
    polls, and the polls keep their ADR-009 rates."""
    raw, frames, bad = record(esp, answering=True)
    check_frames(raw, frames, bad)
    # One RC per 20 ms grid slot, never more; a slot can only be late
    assert 45 <= rate(frames, RC) <= 50.5, f"RC at {rate(frames, RC)} Hz"
    assert 15 <= rate(frames, msp.MSP_ATTITUDE) <= 26
    assert 3 <= rate(frames, msp.MSP_STATUS) <= 5.5
    assert 3 <= rate(frames, msp.MSP_ANALOG) <= 5.5
    assert 0.5 <= rate(frames, msp.MSP_API_VERSION) <= 1.25


def test_fc_going_silent_does_not_starve_the_polls(esp):
    """After NO_SESSION the FC stops answering: RC keeps timing out, but a due
    poll goes after each RC timeout (#174), so the FC's return can be seen."""
    raw, frames, bad = record(esp, answering=False)
    check_frames(raw, frames, bad)
    polls = sum(f.cmd in POLLS for _, f in frames) / RECORD_S
    assert polls >= 2, f"polls starved by RC: {polls:.1f}/s"
    total = len(frames) / RECORD_S
    assert total <= 11, f"{total:.1f} requests/s with a silent FC"
