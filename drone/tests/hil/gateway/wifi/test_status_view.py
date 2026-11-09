"""HIL Wi-Fi: the gateway decodes the FC's replies (bench view, UDP 5770).

Runs only inside softap_window.sh, after hil.yaml flashed esp_gateway. The Pi
plays the FC on USB (bench.FakeFc) and reads the gateway's one-line status
(WITH_STATUS_VIEW, src/gateway/main.cpp): the values it shows must be the
fake FC's, and must be forgotten once the FC goes silent.
"""

import os
import socket
import time
import warnings

import pytest
from bench import (
    APP_BAUD,
    FAKE_ARMING_FLAGS,
    FAKE_HEADING,
    FAKE_PITCH,
    FAKE_ROLL,
    FAKE_VBAT_CV,
    FakeFc,
    open_esp,
)

STATUS_ADDR = (os.environ.get("HIL_ESP_IP", "192.168.4.1"), 5770)
DEADLINE_S = 15.0  # covers an ESP reset when the serial port opens
FORGET_S = 10.0  # kMaxValueAgeMs in sd/fc_link.hpp
STEADY_S = 3.0  # replies (about 35/s) watched for new errors


@pytest.fixture(scope="module")
def fake_fc():
    port = open_esp(APP_BAUD)
    try:
        with FakeFc(port) as fc:
            yield fc
    finally:
        port.close()


@pytest.fixture(scope="module")
def udp():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.3)
    yield sock
    sock.close()


def parse(line: bytes) -> dict:
    """'armed=0 arming_flags=0x00000020 roll=123 ...' -> {'armed': '0', ...}"""
    return dict(f.split("=", 1) for f in line.decode().split())


def status_until(udp, done, deadline_s=DEADLINE_S) -> dict:
    """Ask for the status line until `done(status)`; return the last one."""
    status = {}
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        try:
            udp.sendto(b"?", STATUS_ADDR)
            status = parse(udp.recvfrom(512)[0])
        except OSError:  # not associated yet, or the ESP restarting
            time.sleep(0.2)
            continue
        if done(status):
            break
        time.sleep(0.2)
    return status


def test_status_shows_the_fake_fc_values(fake_fc, udp):
    def all_three(s):
        return all(s.get(k) not in (None, "-1") for k in ("armed", "roll", "vbat_cV"))

    s = status_until(udp, all_three)
    fake_fc.check()
    assert s, "no status line from UDP 5770: is esp_gateway flashed?"
    assert s["armed"] == "0"
    assert int(s["arming_flags"], 16) == FAKE_ARMING_FLAGS
    assert int(s["roll"]) == FAKE_ROLL
    assert int(s["pitch"]) == FAKE_PITCH
    assert int(s["heading"]) == FAKE_HEADING
    assert int(s["vbat_cV"]) == FAKE_VBAT_CV
    assert 0 <= int(s["status_age_ms"]) < 500  # fresh enough for the ADR-024 gate
    # The counters run from the ESP's boot, so they include the serial port
    # being opened and closed between test steps (a bench artifact: the FC's
    # UART is never opened on the drone). Show them, then require no new
    # error while replies keep flowing.
    errors = ("bad_cksum", "too_long", "err")
    if any(s[k] != "0" for k in errors):
        warnings.warn(f"error counters before the check: {s}", stacklevel=1)
    time.sleep(STEADY_S)
    after = status_until(udp, lambda a: int(a.get("rep", 0)) > int(s["rep"]))
    assert int(after["rep"]) > int(s["rep"]), f"no new replies: {s} -> {after}"
    for k in errors:
        assert after[k] == s[k], f"{k} rose with a healthy FC: {s} -> {after}"


def test_values_forgotten_when_the_fc_goes_silent(fake_fc, udp):
    fake_fc.answering = False
    s = status_until(udp, lambda s: s.get("roll") == "-1", deadline_s=FORGET_S + 5)
    # A dead FC never shows numbers that look like readings
    assert s.get("roll") == "-1", f"values kept after {FORGET_S + 5:.0f} s: {s}"
    assert s["armed"] == s["vbat_cV"] == s["arming_flags"] == "-1"
    assert int(s["timeout"]) > 0
