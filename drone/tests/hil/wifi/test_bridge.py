"""HIL Wi-Fi test: bytes sent over the SoftAP come out of the spare ESP's UART.

Runs only inside softap_window.sh (the Pi on the ESP's SoftAP), so hil.yaml
keeps this folder out of the normal pytest run. The firmware is the
transparent bridge for now: UDP 5760 -> UART0, and UART0 -> UDP back to the
last sender. On the bench, UART0 is the Pi's USB serial (/dev/hil-esp).
When the allow-listed gateway (#101) replaces the bridge, this becomes the
passthrough test: allowed MSP goes through, the rest is dropped.
"""

import os
import socket
import time

import pytest
from bench import APP_BAUD, open_esp

ESP_ADDR = (os.environ.get("HIL_ESP_IP", "192.168.4.1"), 5760)
DEADLINE_S = 15.0  # covers an ESP reset when the serial port opens


# Module scope: opening the FTDI port may reset the ESP (and drop the SoftAP),
# so open it once per window, not once per test
@pytest.fixture(scope="module")
def uart():
    port = open_esp(APP_BAUD)  # HIL_REQUIRED=1 in the window: no skip
    yield port
    port.close()


@pytest.fixture(autouse=True)
def fresh_uart(uart):
    # The port is shared by the module: drop what an earlier test left in it
    # (stray probes, the "\n" sent to register a UDP peer)
    uart.reset_input_buffer()
    uart.reset_output_buffer()


@pytest.fixture
def udp():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.2)
    yield sock
    sock.close()


def test_udp_to_uart(uart, udp):
    probe = b"$HIL-probe-udp-to-uart\n"
    seen = b""
    deadline = time.monotonic() + DEADLINE_S
    while probe not in seen and time.monotonic() < deadline:
        try:
            udp.sendto(probe, ESP_ADDR)
        except OSError:
            pass  # not associated yet, or the ESP is restarting: try again
        seen += uart.read(256)
    assert probe in seen, f"probe never reached UART0, got {seen[-200:]!r}"


def test_uart_to_udp(uart, udp):
    probe = b"$HIL-probe-uart-to-udp\n"
    got = b""
    deadline = time.monotonic() + DEADLINE_S
    while probe not in got and time.monotonic() < deadline:
        try:
            # The bridge answers the last UDP sender: (re-)register every
            # round, in case the ESP restarted
            udp.sendto(b"\n", ESP_ADDR)
        except OSError:
            pass  # not associated yet: try again
        uart.write(probe)
        try:
            got += udp.recvfrom(512)[0]
        except OSError:  # timeout, or the SoftAP dropped while the ESP resets
            pass
    assert probe in got, f"probe never came back over UDP, got {got[-200:]!r}"
