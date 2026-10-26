import pathlib
import socket
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))

import esp_wdt  # noqa: E402
import tcp_load  # noqa: E402


def echo_server():
    """A local stand-in for the esp_diag_tcp echo on a free port."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                while data := conn.recv(4096):
                    conn.sendall(data)

    threading.Thread(target=serve, daemon=True).start()
    return srv


def fake_wdt_esp(hang_s=1.5):
    """Answers like esp_diag_wdt: silent for hang_s after HANG, then a fresh boot."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    state = {"boot": time.monotonic(), "hung_until": 0.0, "reason": "Power On"}

    def serve():
        while True:
            try:
                data, peer = sock.recvfrom(64)
            except OSError:
                return
            now = time.monotonic()
            if now < state["hung_until"]:
                continue
            if state["hung_until"]:
                state.update(
                    boot=state["hung_until"], hung_until=0.0, reason="Software Watchdog"
                )
            if data == b"REASON?":
                uptime = int((now - state["boot"]) * 1000)
                sock.sendto(f"REASON,{state['reason']},{uptime}".encode(), peer)
            elif data == b"HANG":
                sock.sendto(b"HANGING,HANG", peer)
                state["hung_until"] = now + hang_s

    threading.Thread(target=serve, daemon=True).start()
    return sock


def test_tcp_load_echo_and_reconnect():
    srv = echo_server()
    stats, rows = tcp_load.run_load(
        "127.0.0.1", srv.getsockname()[1], 1.2, 64, 128, 0.5
    )
    srv.close()
    assert stats["connects"] >= 2
    assert stats["mismatches"] == 0
    assert stats["sent"] > 0 and stats["echoed"] == stats["sent"]
    assert rows and all(ms >= 0 for _, ms in rows)


def test_parse_reason():
    assert esp_wdt.parse_reason("REASON,Software Watchdog,1234") == (
        "Software Watchdog",
        1234,
    )
    assert esp_wdt.parse_reason("garbage") == (None, None)
    assert esp_wdt.parse_reason(None) == (None, None)


def test_cycle_sees_the_reboot():
    esp = fake_wdt_esp()
    rows = esp_wdt.run_cycle("127.0.0.1", esp.getsockname()[1], 1, False, 10, 0)
    esp.close()
    assert len(rows) == 1
    assert rows[0]["reason"] == "Software Watchdog"
    assert rows[0]["ok"] == 1
    assert rows[0]["down_s"] >= 1.5
