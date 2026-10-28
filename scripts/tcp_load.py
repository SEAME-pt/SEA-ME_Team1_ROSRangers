#!/usr/bin/env python3
"""S1-07 TCP load: keep a TCP connection to the esp_diag_tcp firmware busy.

The ESP echoes everything on TCP 5762. This sends a steady stream, checks the echo,
and reconnects from time to time (a new session, ADR-005), while the UDP latency
test runs in another terminal. Props off.

  python3 scripts/tcp_load.py --duration 600 --csv s1-07_run1_tcp.csv   # terminal 1
  python3 scripts/seame.py msp ping --stream --duration 600 --csv s1-07_run1.csv
"""

import argparse
import csv
import os
import pathlib
import socket
import statistics
import time

HOST = "192.168.4.1"
PORT = 5762
RESULTS_DIR = pathlib.Path(__file__).resolve().parents[1] / "results"


def resolve_csv_path(path):
    csv_path = pathlib.Path(path)
    if not csv_path.is_absolute() and csv_path.parent == pathlib.Path("."):
        csv_path = RESULTS_DIR / csv_path
    return csv_path


def run_load(host, port, duration, kbps, chunk, reconnect_s):
    """Return (stats, echo_rows). echo_rows: one (time_s, echo_ms) per chunk."""
    interval = chunk * 8 / (kbps * 1000)
    stats = {
        "connects": 0,
        "connect_failures": 0,
        "sent": 0,
        "echoed": 0,
        "mismatches": 0,
    }
    rows = []
    start = time.monotonic()
    sock = None
    while time.monotonic() - start < duration:
        try:
            sock = socket.create_connection((host, port), timeout=3)
        except OSError:
            stats["connect_failures"] += 1
            time.sleep(0.5)
            continue
        stats["connects"] += 1
        sock.settimeout(0.005)
        pending = bytearray()  # sent, not echoed yet
        marks = []  # (end offset in pending order, send time)
        sent_here = echoed_here = 0
        session_end = min(start + duration, time.monotonic() + reconnect_s)
        next_send = time.monotonic()
        try:
            while time.monotonic() < session_end:
                now = time.monotonic()
                if now >= next_send:
                    data = os.urandom(chunk)
                    sock.sendall(data)
                    pending += data
                    sent_here += chunk
                    marks.append((sent_here, now))
                    next_send += interval
                try:
                    got = sock.recv(4096)
                except socket.timeout:
                    continue
                if not got:
                    break
                if bytes(pending[: len(got)]) != got:
                    stats["mismatches"] += 1
                del pending[: len(got)]
                echoed_here += len(got)
                while marks and marks[0][0] <= echoed_here:
                    _, t_sent = marks.pop(0)
                    rows.append(
                        (
                            round(t_sent - start, 4),
                            round((time.monotonic() - t_sent) * 1000, 2),
                        )
                    )
        except OSError:
            pass
        finally:
            # let the last echoes in before closing
            sock.settimeout(0.5)
            try:
                while pending:
                    got = sock.recv(4096)
                    if not got:
                        break
                    pending = pending[len(got) :]
                    echoed_here += len(got)
            except OSError:
                pass
            sock.close()
            stats["sent"] += sent_here
            stats["echoed"] += echoed_here
    stats["duration_s"] = round(time.monotonic() - start, 1)
    return stats, rows


def summary(stats, rows):
    lines = [
        f"TCP connects {stats['connects']} (failed {stats['connect_failures']}), "
        f"sent {stats['sent']} B, echoed {stats['echoed']} B, mismatches {stats['mismatches']}, "
        f"{stats['duration_s']} s"
    ]
    echo = sorted(ms for _, ms in rows)
    if echo:
        p99 = echo[min(len(echo) - 1, int(len(echo) * 0.99))]
        lines.append(
            f"TCP echo ms: median {statistics.median(echo):.1f}  p99 {p99:.1f}  max {echo[-1]:.1f}"
        )
    lines.append(
        "S1-07 passes on the UDP side: longest silence of the msp ping run <= 203 ms (S1-05 max)."
    )
    return lines


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--host", default=HOST)
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--duration", type=float, default=600)
    p.add_argument(
        "--kbps", type=float, default=20, help="TCP send rate (default 20 kbit/s)"
    )
    p.add_argument(
        "--chunk", type=int, default=256, help="bytes per send (default 256)"
    )
    p.add_argument(
        "--reconnect",
        type=float,
        default=30,
        help="new connection every N s (default 30)",
    )
    p.add_argument(
        "--csv", help="save the echo time of every chunk (bare name: results/)"
    )
    args = p.parse_args()
    print(
        f"TCP load to {args.host}:{args.port}, {args.kbps:g} kbit/s, {args.duration:g} s. Start the UDP test now."
    )
    stats, rows = run_load(
        args.host, args.port, args.duration, args.kbps, args.chunk, args.reconnect
    )
    if args.csv:
        out = resolve_csv_path(args.csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(("time_s", "echo_ms"))
            writer.writerows(rows)
        print(f"Saved {out}")
    print("\n".join(summary(stats, rows)))


if __name__ == "__main__":
    main()
