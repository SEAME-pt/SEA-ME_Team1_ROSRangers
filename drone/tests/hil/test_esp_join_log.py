"""esp_join_log.summary, without the bench. Runs in PR CI (hil-unit)."""

from bench import msp
from esp_join_log import summary

POLL = msp.encode_request(105, b"")  # MSP_RC, the kind of poll the gateway sends


def test_summary_shows_a_stall_and_a_crash():
    text = summary([(0.2, POLL), (2.5, POLL + b"\r\nException (28):")], 3.0)
    assert "2 MSP frames in 3.0 s" in text
    assert f"bytes per second: [{len(POLL)}, 0, {len(POLL) + 17}, 0]" in text
    assert "crash markers: ['Exception (']" in text


def test_summary_of_nothing():
    text = summary([], 0.0)
    assert "0 bytes, 0 MSP frames" in text
    assert "bytes per second: [0]" in text
    assert "crash markers: none" in text


def test_summary_when_the_window_never_came():
    assert "window not reached" in summary([], None)
