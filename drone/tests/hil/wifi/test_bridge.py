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

PORT = os.environ.get("HIL_ESP_PORT", "/dev/hil-esp")
ESP_ADDR = (os.environ.get("HIL_ESP_IP", "192.168.4.1"), 5760)
BAUD = 115200
DEADLINE_S = 15.0  # covers an ESP reset when the serial port opens


@pytest.fixture
def uart():
    serial = pytest.importorskip("serial")
    if not os.path.exists(PORT):
        pytest.fail(f"{PORT} not found: is the spare ESP plugged into the Pi?")
    port = serial.Serial()
    port.port = PORT
    port.baudrate = BAUD
    port.timeout = 0.2
    port.dtr = False  # released DTR and RTS = run mode, as in test_smoke.py
    port.rts = False
    port.open()
    yield port
    port.close()


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
    # The bridge answers the last UDP sender, so register first
    udp.sendto(b"\n", ESP_ADDR)
    probe = b"$HIL-probe-uart-to-udp\n"
    got = b""
    deadline = time.monotonic() + DEADLINE_S
    while probe not in got and time.monotonic() < deadline:
        uart.write(probe)
        try:
            got += udp.recvfrom(512)[0]
        except TimeoutError:
            udp.sendto(b"\n", ESP_ADDR)  # the ESP may have restarted
    assert probe in got, f"probe never came back over UDP, got {got[-200:]!r}"
