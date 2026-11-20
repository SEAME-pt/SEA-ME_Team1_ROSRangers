#!/usr/bin/env python3
"""Late-check log (#219, ESP-S1-09 re-run): record and analyse it.

The gateway built with env esp_late_log(_ota) sends one record per SETPOINT it
receives to UDP 5770 (src/gateway/main.cpp, WITH_LATE_LOG), plus a flash chip
line every 10 s (#218). Run the control stream as usual with
drone/tools/bench/ground_link.py (its t_send_us is CLOCK_MONOTONIC_RAW, spec §1)
and:

  python3 drone/tools/bench/late_log.py listen --csv s1-09_late_run1.csv   # Ctrl+C to stop
  python3 drone/tools/bench/late_log.py analyze results/s1-09_late_run1.csv

`listen` writes the records to the CSV and the flash chip line, with the ESP's
count of dropped records and the record packets lost on the way (gaps in the
batch numbers), to <csv>.info.json next to it.

`analyze` replays the logged SETPOINTs through the Python twin of the ESP's
Receiver at several bounds (Protocol Spec §4 rule 3a) and prints, per bound,
the frames refused as late and the T_loss silences (250 ms without an
accepted SETPOINT, each one a LINK_FAILSAFE). Pass criteria (#219, BrunoBMFT
on #171): no extra silence compared with the check off, no late refusal in a
run without Wi-Fi stalls, and a frame held back 250 ms or more is refused.

What the replay takes into account (#225 review):
- Arrival order comes from micros() measured from the first record, so the
  71.6 min wrap of the ESP's micros() during a run changes nothing.
- A new session_id (a reconnect, `R` in ground_link.py) starts a new replay
  Receiver: seq starts again at 1, and the ESP seeded a new offset.
- Only frames the ESP judged on lateness or seq are replayed. A frame it
  refused for anything else (bad tag, wrong or no session, ...) is skipped,
  since the replay would wrongly accept it.
- Records the ESP had to drop (its buffer was full), and record packets lost
  in the air (a gap in the batch numbers), are reported: the counts are then
  incomplete, and a lost packet looks like a silence (#225 follow-up).
"""

from __future__ import annotations

import argparse
import csv
import json
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
FIELDS = ("pc_time_s", "session", "seq", "t_send_us", "rx_us", "micros", "result")
CHIP_FIELDS = ("chip_id", "vendor", "real_size", "bus_hz")
BOUNDS_MS = (100, 150, 200, 250)
T_LOSS_US = 250_000
REPLAY_KEY = bytes(32)  # the replay seals its own frames; any key works
# The ESP results the replay can reproduce: decided by the late check or the
# seq window. Anything else (bad tag, wrong or no session, ...) is skipped.
REPLAYED = {
    RxResult.ACCEPTED,
    RxResult.ACCEPTED_GAP,
    RxResult.DUPLICATE_SEQ,
    RxResult.OLD_SEQ,
    RxResult.SEQ_TOO_HIGH,
    RxResult.LATE,
}
REPLAYED_CODES = {int(x) for x in REPLAYED}


def int32(value: int) -> int:
    """A u32 difference read as signed, so a clock that wrapped in between
    still gives the right step. Example: 0x00000010 - 0xFFFFFFF0 is
    0x00000020 as a u32, so +32 us: the clock wrapped 32 us after
    0xFFFFFFF0. And 0xFFFFFFF0 - 0x00000010 is 0xFFFFFFE0, read as -32 us."""
    value &= 0xFFFFFFFF
    if value >= 0x80000000:
        return value - 0x100000000
    return value


def parse_packet(data: bytes, pc_time_s: float):
    """One UDP packet: a B line (its batch number) then R lines (SETPOINT
    records), or an F line (flash chip). Returns (records, chip, batch); bad
    lines are skipped, and batch is None for a packet without a B line."""
    records = []
    chip = None
    batch = None
    for line in data.decode("ascii", "replace").splitlines():
        parts = line.split(",")
        if parts[0] == "B" and len(parts) == 2 and parts[1].isdigit():
            batch = int(parts[1])
        elif parts[0] == "R" and len(parts) == 7:
            try:
                values = [int(v) for v in parts[1:]]
            except ValueError:
                continue
            records.append(dict(zip(FIELDS, (pc_time_s, *values))))
        elif parts[0] == "F" and len(parts) == 6:
            chip = dict(zip(CHIP_FIELDS, parts[1:5]))
            chip["dropped"] = int(parts[5]) if parts[5].isdigit() else None
    return records, chip, batch


def lost_batches(numbers) -> int:
    """Record packets lost on the way: the numbers missing between the first
    and the last batch received. Example: 4, 5, 7, 9 received -> 6 and 8
    missing -> 2. Duplicates count once."""
    seen = set(numbers)
    if not seen:
        return 0
    return (max(seen) - min(seen) + 1) - len(seen)


def read_csv(path):
    with open(path, newline="") as fh:
        return [
            {k: (float(v) if k == "pc_time_s" else int(v)) for k, v in row.items()}
            for row in csv.DictReader(fh)
        ]


def info_path(csv_path) -> pathlib.Path:
    return pathlib.Path(f"{csv_path}.info.json")


def arrival_order(records):
    """The records in the order they reached the ESP. Each record's time is
    micros() measured from the first record (received order, step by step
    with int32()), so a wrap of the ESP's micros() during the run keeps the
    order. The sort is stable, and for records in the same microsecond keeps
    the received order."""
    elapsed, total, previous = [], 0, None
    for r in records:
        if previous is not None:
            total += int32(r["micros"] - previous)
        elapsed.append(total)
        previous = r["micros"]
    order = sorted(range(len(records)), key=lambda i: elapsed[i])
    return [(elapsed[i], records[i]) for i in order]


def _frame(session: int, seq: int, t_send_us: int) -> bytes:
    header = Header(
        type=MsgType.SETPOINT, session_id=session, seq=seq, t_send_us=t_send_us
    )
    return encode_frame(header, bytes(SETPOINT_SIZE), REPLAY_KEY)


def replay(timed, bound_ms: int):
    """The (elapsed_us, record) list, in arrival order, through an ESP UDP
    Receiver with the late check at bound_ms (0: off). Every new session
    starts a new Receiver, seeded from its first record (the log has no
    HELLO). Returns a dict: late (refused as late), silences (T_loss),
    accepted, other (refused for anything else) and skipped (not replayed)."""
    late = 0
    silences = 0
    skipped = 0
    accepted = 0
    other = 0
    rx = None
    session = None
    last_ok_us = None  # silences run across sessions: a reconnect gap counts
    for elapsed_us, r in timed:
        if r["result"] not in REPLAYED_CODES:
            skipped += 1
            continue
        if r["session"] != session:
            session = r["session"]
            rx = Receiver(Role.ESP, Transport.UDP)
            rx.start_session(session, REPLAY_KEY)
            rx.set_late_bound(bound_ms * 1000)
            rx.seed_lag(r["t_send_us"], r["rx_us"])
        result, _ = rx.receive(_frame(session, r["seq"], r["t_send_us"]), r["rx_us"])
        if result is RxResult.LATE:
            late += 1
        elif is_accepted(result):
            accepted += 1
            if last_ok_us is not None and elapsed_us - last_ok_us >= T_LOSS_US:
                silences += 1
            last_ok_us = elapsed_us
        else:
            other += 1
    return {
        "late": late,
        "silences": silences,
        "accepted": accepted,
        "other": other,
        "skipped": skipped,
    }


def lateness_ms(timed):
    """How far each replayed frame arrived above the fastest one of its
    session (ms), with the finer micros() clock."""
    by_session = {}
    for _, r in timed:
        if r["result"] in REPLAYED_CODES:
            by_session.setdefault(r["session"], []).append(r)
    out = []
    for records in by_session.values():
        first = records[0]["micros"] - records[0]["t_send_us"]
        rel = [int32(r["micros"] - r["t_send_us"] - first) for r in records]
        floor = min(rel)
        out += [(x - floor) / 1000 for x in rel]
    return out


def analyze(records, dropped=None, lost=None):
    timed = arrival_order(records)
    late_ms = sorted(lateness_ms(timed))
    n = len(late_ms)
    off = replay(timed, 0)
    rows = []
    for bound in BOUNDS_MS:
        on = replay(timed, bound)
        rows.append({"bound_ms": bound, "late": on["late"], "silences": on["silences"]})
    firmware_late = sum(r["result"] == int(RxResult.LATE) for r in records)
    return {
        "frames": len(records),
        "replayed": n,
        "skipped": off["skipped"],
        # With the check off the replay should accept what the ESP judged on
        # lateness or seq; a refusal here (old seq, wrong session) means the
        # order or the sessions are wrong, and the table can't be trusted.
        "refused_off": off["other"],
        "accepted_off": off["accepted"],
        "sessions": len({r["session"] for r in records}),
        "dropped": dropped,
        "lost_batches": lost,
        "p50_ms": late_ms[n // 2] if n else 0.0,
        "p99_ms": late_ms[int(0.99 * (n - 1))] if n else 0.0,
        "max_ms": late_ms[-1] if n else 0.0,
        "silences_off": off["silences"],
        "firmware_late": firmware_late,
        "bounds": rows,
    }


def report(stats) -> str:
    lines = []
    if stats["dropped"] is None:
        lines.append(
            "WARNING: no info file, so the ESP's dropped-record count is unknown"
        )
    elif stats["dropped"] > 0:
        lines.append(
            f"WARNING: the ESP dropped {stats['dropped']} records (its buffer was full): "
            "the counts below are incomplete"
        )
    if stats["lost_batches"] is None:
        lines.append("WARNING: no info file, so lost record packets are unknown")
    elif stats["lost_batches"] > 0:
        lines.append(
            f"WARNING: {stats['lost_batches']} record packets were lost on the way: "
            "their frames are missing, which can look like silences"
        )
    if stats["refused_off"] > 0:
        lines.append(
            f"WARNING: {stats['refused_off']} frames refused by the replay with the "
            "check off (seq or session): the arrival order or sessions are wrong"
        )
    lines += [
        f"{stats['frames']} SETPOINTs in {stats['sessions']} session(s); "
        f"{stats['replayed']} replayed, {stats['skipped']} skipped (refused by the ESP "
        "for something other than lateness or seq)",
        f"Lateness above the fastest frame: median {stats['p50_ms']:.1f} ms, "
        f"p99 {stats['p99_ms']:.1f} ms, max {stats['max_ms']:.1f} ms",
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
    batches = []
    print(f"Listening on UDP {args.port}. Start ground_link.py now; Ctrl+C when done.")
    try:
        while args.duration is None or time.monotonic() - start < args.duration:
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                continue
            records, info, batch = parse_packet(
                data, round(time.monotonic() - start, 3)
            )
            rows += records
            if batch is not None:
                batches.append(batch)
            if info is None:
                continue
            same_chip = chip is not None and all(
                chip[k] == info[k] for k in CHIP_FIELDS
            )
            if not same_chip:
                print(
                    f"flash chip: id {info['chip_id']}, vendor {info['vendor']}, "
                    f"{info['real_size']} bytes, bus {info['bus_hz']} Hz (the clock "
                    "this build set, not the chip's rating: look the ID up for #218)",
                    flush=True,
                )
            chip = info
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
    info = dict(chip or {})
    info["dropped"] = chip["dropped"] if chip else None
    info["batches_received"] = len(set(batches))
    info["lost_batches"] = lost_batches(batches)
    info_path(out).write_text(json.dumps(info, indent=2) + "\n")
    dropped = info["dropped"] if chip else "unknown (no chip line)"
    print(
        f"\nSaved {out}: {len(rows)} SETPOINT records; dropped by the ESP: {dropped}; "
        f"record packets lost on the way: {info['lost_batches']}"
    )
    print("Wait at least 1 s after `q` before Ctrl+C, so the last drop count arrives.")
    if not rows:
        print("no data: is env esp_late_log(_ota) on the ESP and a session streaming?")


def cmd_analyze(args):
    path = resolve_csv_path(args.csv)
    rows = read_csv(path)
    if not rows:
        raise SystemExit(f"No records in {args.csv}")
    dropped = None
    lost = None
    if info_path(path).exists():
        info = json.loads(info_path(path).read_text())
        dropped = info.get("dropped")
        lost = info.get("lost_batches")
    print(report(analyze(rows, dropped, lost)))


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
