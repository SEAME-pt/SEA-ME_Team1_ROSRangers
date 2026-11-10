"""The SITL wait says why it gave up (#181). They run without SITL."""

import socket
import threading
from collections import Counter

from conftest import connect_when_ready


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_nothing_listening_counts_refused_connects():
    tries = Counter()
    assert connect_when_ready(tries, port=free_port(), timeout_s=1.2) is None
    assert set(tries) == {"connect: ConnectionRefusedError"}


def test_silent_server_counts_connected_without_answer():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    held = []  # keep the connections open: a silent SITL, not a closed one
    threading.Thread(
        target=lambda: [held.append(server.accept()) for _ in range(5)], daemon=True
    ).start()
    tries = Counter()
    try:
        link = connect_when_ready(tries, port=server.getsockname()[1], timeout_s=1.2)
    finally:
        server.close()
    assert link is None
    assert tries["connected, no MSP answer within 1 s"] >= 1
