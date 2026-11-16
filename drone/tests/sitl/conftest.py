"""Connection to Betaflight 2026.6.2 SITL, UART1 on TCP 5761 (ADR-014, ADR-019).

The fixture waits for the port to open and for MSP to answer, up to
SITL_TIMEOUT_S (60 s with SITL_REQUIRED=1, else 5 s). Without SITL the tests that need it are skipped, unless
SITL_REQUIRED=1 (set in ci.yaml) turns the skip into a failure.
"""

import os
import socket
import time
from collections import Counter

import pytest
from msp_frames import MSP_API_VERSION, Frame, decode, encode_v1

HOST = os.environ.get("SITL_HOST", "127.0.0.1")
PORT = int(os.environ.get("SITL_MSP_PORT", "5761"))
# Short wait for a local run without SITL, long one when SITL must be there
TIMEOUT_S = float(
    os.environ.get(
        "SITL_TIMEOUT_S", "60" if os.environ.get("SITL_REQUIRED") == "1" else "5"
    )
)
REPLY_TIMEOUT_S = 1.0


class MspLink:
    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.buf = b""

    def send(self, data: bytes) -> None:
        self.sock.sendall(data)

    def drain(self) -> None:
        """Drop whatever is still queued from an earlier test."""
        self.read_frame(timeout=0.2)
        self.buf = b""

    def read_frame(self, timeout: float = REPLY_TIMEOUT_S) -> Frame | None:
        """Next valid frame, or None if none arrives within timeout."""
        deadline = time.monotonic() + timeout
        while True:
            frame, used = decode(self.buf)
            self.buf = self.buf[used:]
            if frame is not None:
                return frame
            if used:
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self.sock.settimeout(remaining)
            try:
                chunk = self.sock.recv(4096)
            except TimeoutError:
                return None
            if not chunk:
                raise ConnectionError("SITL closed the MSP connection")
            self.buf += chunk


def skip_or_fail(reason: str):
    if os.environ.get("SITL_REQUIRED") == "1":
        pytest.fail(f"{reason} and SITL_REQUIRED=1")
    pytest.skip(reason)


def connect_when_ready(
    tries: Counter, port: int = PORT, timeout_s: float = TIMEOUT_S
) -> MspLink | None:
    """Retry the TCP connect and an MSP_API_VERSION request until SITL answers.
    Counts each failed try by what happened, so a timeout says why (#181)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            sock = socket.create_connection((HOST, port), timeout=1.0)
        except OSError as e:
            tries[f"connect: {type(e).__name__}"] += 1
            time.sleep(0.5)
            continue
        link = MspLink(sock)
        try:
            link.send(encode_v1(MSP_API_VERSION))
            if link.read_frame() is not None:
                link.drain()
                return link
            tries["connected, no MSP answer within 1 s"] += 1
        except OSError as e:
            tries[f"connected, then {type(e).__name__}"] += 1
        sock.close()
        time.sleep(0.5)
    return None


@pytest.fixture(scope="session")
def sitl_session():
    tries = Counter()
    link = connect_when_ready(tries)
    if link is None:
        skip_or_fail(
            f"no MSP answer from SITL on {HOST}:{PORT} within {TIMEOUT_S:.0f} s"
            f" (tries: {dict(tries)})"
        )
    yield link
    link.sock.close()


@pytest.fixture
def msp(sitl_session):
    sitl_session.drain()
    return sitl_session
