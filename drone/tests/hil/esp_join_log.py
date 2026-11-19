"""Hold the spare ESP's port open until the SoftAP join, and log what it sends.

    esp_join_log.py OUT

Opening /dev/hil-esp resets the ESP (FTDI auto-reset, seen on the bench
2026-11-16), but only the first open of the port does: while this process
holds it, the tests' own opens don't. So hil.yaml starts it before the bench
tests, and the Wi-Fi window's join meets the ESP those tests left behind (#207).

It reads nothing until SIGUSR1 (window start), so it never takes bytes from
the tests. SIGTERM closes the port, before the Wi-Fi tests open it, and writes
a summary to OUT.
"""

import signal
import sys
import time

from bench import APP_BAUD, crash_markers, msp, open_esp


def summary(chunks, duration: float | None) -> str:
    """`chunks`: (seconds since logging started, bytes) for each read.
    `duration` None: SIGUSR1 never came (an earlier step failed)."""
    if duration is None:
        return "ESP UART0: SoftAP window not reached, nothing logged"
    data = b"".join(c for _, c in chunks)
    per_s = [0] * (int(duration) + 1)
    for t, c in chunks:
        per_s[min(int(t), len(per_s) - 1)] += len(c)
    frames = msp.Parser().feed(data)
    markers = [m.decode() for m in crash_markers(data)] or "none"
    return "\n".join(
        [
            f"ESP UART0 in the SoftAP window: {len(data)} bytes, {len(frames)} MSP frames in {duration:.1f} s",
            # Second 0 also holds what queued up between the bench tests and
            # the window. A 0 later on: the gateway stopped polling (stall,
            # reboot)
            f"bytes per second: {per_s}",
            f"crash markers: {markers}",
            f"last bytes: {data[-200:]!r}",
        ]
    )


def main():
    out = sys.argv[1]
    port = open_esp(APP_BAUD)  # the one open that resets the ESP
    start = None
    chunks = []

    def on_usr1(*_):
        nonlocal start
        # All handles on a port share its settings: a test may have left
        # another baud (test_uart.py boots at 74880)
        port.baudrate = APP_BAUD
        start = time.monotonic()

    signal.signal(signal.SIGUSR1, on_usr1)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        while True:
            if start is None:
                time.sleep(0.1)
                continue
            data = port.read(port.in_waiting or 1)
            if data:
                chunks.append((time.monotonic() - start, data))
    finally:
        port.close()
        duration = None
        if start is not None:
            duration = time.monotonic() - start
        with open(out, "w") as f:
            f.write(summary(chunks, duration) + "\n")


if __name__ == "__main__":
    main()
