#!/usr/bin/env python3
"""Late-check log (#219, ESP-S1-09 re-run): record and analyse it.

The gateway built with env esp_late_log(_ota) sends one record per SETPOINT it
receives to UDP 5770 (src/gateway/main.cpp, WITH_LATE_LOG), plus the flash
chip once a second (#218). Run the control stream as usual with
drone/tools/bench/ground_link.py (its t_send_us is CLOCK_MONOTONIC_RAW, spec §1) and:

  python3 drone/tools/bench/late_log.py listen --csv s1-09_late_run1.csv   # Ctrl+C to stop
  python3 drone/tools/bench/late_log.py analyze results/s1-09_late_run1.csv

`analyze` replays every logged SETPOINT through the Python twin of the ESP's
Receiver at several bounds (Protocol Spec §4 rule 3a) and prints, per bound,
the frames refused as late and the T_loss silences (250 ms without an
accepted SETPOINT, each one a LINK_FAILSAFE). Pass criteria (#219, BrunoBMFT
on #171): no extra silence compared with the check off, no late refusal in a
run without Wi-Fi stalls, and a frame held back 250 ms or more is refused.
"""

from __future__ import annotations

import argparse
import csv
import pathlib
import socket
import sys
import time

sys.path.insert(
    0,
    str(
        pathlib.Path(__file__).resolve().parents[3] / "drone/ground/src/sd_protocol_py"
    ),
)

from esp_timing import resolve_csv_path  # noqa: E402

from sd_protocol.frame import Header, MsgType, encode_frame  # noqa: E402
from sd_protocol.messages import Role, Transport  # noqa: E402
from sd_protocol.receiver import Receiver, RxResult, is_accepted  # noqa: E402
from sd_protocol.setpoint import SETPOINT_SIZE  # noqa: E402

PORT = 5770
FIELDS = ("pc_time_s", "seq", "t_send_us", "rx_us", "micros", "result")
BOUNDS_MS = (100, 150, 200, 250)
T_LOSS_US = 250_000
REPLAY_KEY = bytes(32)  # the replay seals its own frames; any key works
REPLAY_SESSION = 0x219


def parse_packet(data: bytes, pc_time_s: float):
    """One UDP packet: R lines (SETPOINT records) and F lines (flash chip).
    Returns (records, chip); bad lines are skipped."""
    records, chip = [], None
    for line in data.decode("ascii", "replace").splitlines():
        parts = line.split(",")
        if parts[0] == "R" and len(parts) == 6:
            try:
                values = [int(v) for v in parts[1:]]
            except ValueError:
                continue
            records.append(dict(zip(FIELDS, (pc_time_s, *values))))
        elif parts[0] == "F" and len(parts) == 6:
            chip = {
                "chip_id": parts[1],
                "vendor": parts[2],
                "real_size": parts[3],
                "speed_hz": parts[4],
                "dropped": parts[5],
            }
    return records, chip


def read_csv(path):
    with open(path, newline="") as fh:
        return [
            {k: (float(v) if k == "pc_time_s" else int(v)) for k, v in row.items()}
            for row in csv.DictReader(fh)
        ]


def _frame(seq: int, t_send_us: int) -> bytes:
    header = Header(
        type=MsgType.SETPOINT, session_id=REPLAY_SESSION, seq=seq, t_send_us=t_send_us
    )
    return encode_frame(header, bytes(SETPOINT_SIZE), REPLAY_KEY)


def replay(records, bound_ms: int):
    """The records, in arrival order, through an ESP UDP Receiver with the late
    check at bound_ms (0: off), seeded from the first record (the log has no
    HELLO). Returns (frames refused as late, T_loss silences)."""
    rx = Receiver(Role.ESP, Transport.UDP)
    rx.start_session(REPLAY_SESSION, REPLAY_KEY)
    rx.set_late_bound(bound_ms * 1000)
    late = silences = 0
    last_ok = None
    for i, r in enumerate(records):
        if i == 0:
            rx.seed_lag(r["t_send_us"], r["rx_us"])
        result, _ = rx.receive(_frame(r["seq"], r["t_send_us"]), r["rx_us"])
        if result is RxResult.LATE:
            late += 1
        if is_accepted(result):
            if (
                last_ok is not None
                and (r["micros"] - last_ok) & 0xFFFFFFFF >= T_LOSS_US
            ):
                silences += 1
            last_ok = r["micros"]
    return late, silences


def lateness_ms(records):
    """How far each frame arrived above the fastest one (ms), with the finer
    micros() clock: the lag floor is the run's minimum lag."""
    lags = [(r["micros"] - r["t_send_us"]) & 0xFFFFFFFF for r in records]
    # lags are u32 differences of two free-running clocks: centre them on the
    # first one so the wrap of either clock does not matter
    first = lags[0]
    rel = [((lag - first + 2**31) & 0xFFFFFFFF) - 2**31 for lag in lags]
    floor = min(rel)
    return [(x - floor) / 1000 for x in rel]


def analyze(records):
    records = sorted(records, key=lambda r: r["micros"])  # arrival order
    late_ms = sorted(lateness_ms(records))
    n = len(late_ms)
    off_late, off_silences = replay(records, 0)
    rows = []
    for bound in BOUNDS_MS:
        late, silences = replay(records, bound)
        rows.append({"bound_ms": bound, "late": late, "silences": silences})
    firmware_late = sum(r["result"] == int(RxResult.LATE) for r in records)
    return {
        "frames": n,
        "p50_ms": late_ms[n // 2],
        "p99_ms": late_ms[int(0.99 * (n - 1))],
        "max_ms": late_ms[-1],
        "silences_off": off_silences,
        "firmware_late": firmware_late,
        "bounds": rows,
    }


def report(stats) -> str:
    lines = [
        f"{stats['frames']} SETPOINTs; lateness above the fastest frame: "
        f"median {stats['p50_ms']:.1f} ms, p99 {stats['p99_ms']:.1f} ms, "
        f"max {stats['max_ms']:.1f} ms",
        f"Refused as late by the ESP itself (firmware bound): {stats['firmware_late']}",
        f"T_loss silences with the check off: {stats['silences_off']}",
        "",
        "| Bound | Refused as late | T_loss silences | Extra failsafes |",
        "|---|---|---|---|",
    ]
    for row in stats["bounds"]:
        extra = row["silences"] - stats["silences_off"]
        lines.append(
            f"| {row['bound_ms']} ms | {row['late']} | {row['silences']} | {extra} |"
        )
    return "\n".join(lines)


def cmd_listen(args):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", args.port))
    sock.settimeout(0.5)
    rows, chip, start = [], None, time.monotonic()
    print(f"Listening on UDP {args.port}. Start ground_link.py now; Ctrl+C when done.")
    try:
        while args.duration is None or time.monotonic() - start < args.duration:
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                continue
            records, info = parse_packet(data, round(time.monotonic() - start, 3))
            rows += records
            if info is not None and info != chip:
                chip = info
                print(f"flash chip: {chip}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
    out = resolve_csv_path(args.csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nSaved {out}: {len(rows)} SETPOINT records")
    if not rows:
        print("no data: is env esp_late_log(_ota) on the ESP and a session streaming?")


def cmd_analyze(args):
    rows = read_csv(resolve_csv_path(args.csv))
    if not rows:
        raise SystemExit(f"No records in {args.csv}")
    print(report(analyze(rows)))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("listen", help="record the late-check log (UDP 5770)")
    p.add_argument("--csv", required=True, help="output CSV (bare name: results/)")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--duration", type=float, help="stop after this many seconds")
    p.set_defaults(func=cmd_listen)
    p = sub.add_parser("analyze", help="replay the log at several bounds")
    p.add_argument("csv", help="CSV written by listen")
    p.set_defaults(func=cmd_analyze)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
