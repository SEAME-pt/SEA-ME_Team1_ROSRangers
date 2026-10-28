#!/usr/bin/env python3
"""S1-10 watchdog test with the esp_diag_wdt firmware (UDP 5763). Props off.

Stop every MSP stream first: the bridge forwards whatever the PC sends, so a
running stream would look like control output after the reboot.

  python3 scripts/esp_wdt.py reason                       # reset reason and uptime
  python3 scripts/esp_wdt.py cycle -n 5 --csv s1-10.csv   # 5 x hang, reboot, reason
  python3 scripts/esp_wdt.py cycle -n 2 --hw --csv s1-10_hw.csv   # hardware watchdog
"""

import argparse
import csv
import pathlib
import socket
import time

HOST = "192.168.4.1"
PORT = 5763
RESULTS_DIR = pathlib.Path(__file__).resolve().parents[1] / "results"
FIELDS = (
    "cycle",
    "command",
    "uptime_before_ms",
    "down_s",
    "reason",
    "uptime_after_ms",
    "ok",
)


def resolve_csv_path(path):
    csv_path = pathlib.Path(path)
    if not csv_path.is_absolute() and csv_path.parent == pathlib.Path("."):
        csv_path = RESULTS_DIR / csv_path
    return csv_path


def ask(host, port, cmd, timeout=0.5):
    """Send one command, return the reply text or None."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        try:
            s.sendto(cmd.encode(), (host, port))
            return s.recvfrom(256)[0].decode("ascii", "replace")
        except OSError:  # timeout, or no route while the PC rejoins the network
            return None


def parse_reason(reply):
    """'REASON,Software Watchdog,1234' -> ('Software Watchdog', 1234)."""
    parts = (reply or "").split(",")
    if len(parts) != 3 or parts[0] != "REASON":
        return None, None
    return parts[1], int(parts[2])


def wait_reason(host, port, timeout, poll=0.5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        reason, uptime = parse_reason(ask(host, port, "REASON?"))
        if reason is not None:
            return reason, uptime
        time.sleep(poll)
    return None, None


def run_cycle(host, port, n, hw, timeout, pause):
    cmd = "HANG_HW" if hw else "HANG"
    expected = "Hardware Watchdog" if hw else "Software Watchdog"
    rows = []
    for i in range(1, n + 1):
        _, before = wait_reason(host, port, timeout)
        if before is None:
            print(
                f"cycle {i}: the ESP does not answer REASON? (is esp_diag_wdt flashed?)"
            )
            break
        t0 = time.monotonic()
        if not (ask(host, port, cmd, timeout=1.0) or "").startswith("HANGING"):
            print(f"cycle {i}: no HANGING reply")
            break
        time.sleep(1.0)  # the ESP is stuck now; do not count the hang as answered
        reason, after = wait_reason(host, port, timeout)
        down = round(time.monotonic() - t0, 1)
        ok = reason == expected and after is not None and after < down * 1000
        rows.append(dict(zip(FIELDS, (i, cmd, before, down, reason, after, int(ok)))))
        print(
            f"cycle {i}: {cmd} -> back after {down} s, reason {reason!r}, uptime {after} ms {'OK' if ok else 'CHECK'}"
        )
        time.sleep(pause)
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--host", default=HOST)
    p.add_argument("--port", type=int, default=PORT)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("reason")
    c = sub.add_parser("cycle")
    c.add_argument("-n", type=int, default=5)
    c.add_argument("--hw", action="store_true", help="hardware watchdog (soft one off)")
    c.add_argument(
        "--timeout", type=float, default=60, help="s to wait for the ESP (default 60)"
    )
    c.add_argument(
        "--pause", type=float, default=5, help="s between cycles (default 5)"
    )
    c.add_argument("--csv", help="save one row per cycle (bare name: results/)")
    args = p.parse_args()

    if args.cmd == "reason":
        reason, uptime = wait_reason(args.host, args.port, 3)
        print(f"reason {reason!r}, uptime {uptime} ms" if reason else "no answer")
        return
    rows = run_cycle(args.host, args.port, args.n, args.hw, args.timeout, args.pause)
    if args.csv and rows:
        out = resolve_csv_path(args.csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        print(f"Saved {out}")
    print(
        f"{sum(r['ok'] for r in rows)}/{args.n} cycles recovered with the expected watchdog reason"
    )


if __name__ == "__main__":
    main()
