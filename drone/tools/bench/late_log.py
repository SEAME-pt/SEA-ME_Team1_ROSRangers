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
# The drone's and the HIL spare's flash chip: Zbit ZB25VQ32, rated 104 MHz (#218).
KNOWN_CHIP_ID = "0016405e"


def chip_line(info):
    """The F line for the operator. bus is the clock the build set, not the
    chip's rating. A chip other than the known one gets a warning: its rated
    clock must be checked before it runs at 80 MHz (ADR-006, #218)."""
    line = (
        f"flash chip: id {info['chip_id']}, vendor {info['vendor']}, "
        f"{info['real_size']} bytes, bus {info['bus_hz']} Hz (the clock this build set)"
    )
    if info["chip_id"] != KNOWN_CHIP_ID:
        line += (
            f"\nWARNING: not the known chip {KNOWN_CHIP_ID} (Zbit ZB25VQ32, rated "
            "104 MHz). Check this chip's datasheet before it runs at 80 MHz (#218)."
        )
    return line


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
        elif parts[0] == "F" and len(parts) in (6, 7):
            chip = dict(zip(CHIP_FIELDS, parts[1:5]))
            chip["dropped"] = int(parts[5]) if parts[5].isdigit() else None
            chip["next_batch"] = None
            if len(parts) == 7 and parts[6].isdigit():
                chip["next_batch"] = int(parts[6])
    return records, chip, batch


# A batch number this far below the highest one of the current ESP boot
# means the ESP restarted (its counter starts at 0 again), not a packet that
# arrived out of order: UDP reorders by a few packets at most.
RESTART_GAP = 20


def loss_summary(events):
    """Packet loss and ESP drops from the B and F lines, in arrival order.

    events: ("B", batch) for each record packet, ("F", dropped, next_batch)
    for each F line (next_batch is None for an old firmware). The ESP's
    counters restart at 0 when it reboots, so the run is split into boots at
    a big drop in the numbers, and each boot is counted on its own
    (#228 review):
      * lost = numbers missing between the lowest batch received and the
        last one the ESP reported (the highest received, or next_batch - 1
        from an F line, so a lost last packet is seen too). Packets sent
        before `listen` started are not counted.
      * dropped = the highest count seen (it only grows), so a lost or late
        F line can't make it look lower.
    Example: B 1000..1100 with 1050 missing, then a reboot and B 0..50, and
    an F line with next_batch 52: lost 1 + 1 (batch 51) = 2, restarts 1.
    Returns {lost_batches, dropped (None if no F line), restarts,
    batches_received}."""
    boots = []
    boot = None

    def new_boot():
        return {"received": set(), "top": None, "next": None, "dropped": None}

    for event in events:
        if event[0] == "B":
            number = event[1]
        else:
            number = event[2]
        if number is not None:
            top = None if boot is None else boot["top"]
            if boot is None or (top is not None and number + RESTART_GAP < top):
                boot = new_boot()
                boots.append(boot)
        elif boot is None:
            boot = new_boot()
            boots.append(boot)
        if event[0] == "B":
            boot["received"].add(event[1])
            boot["top"] = max(boot["top"] or 0, event[1])
        else:
            dropped, next_batch = event[1], event[2]
            if dropped is not None:
                boot["dropped"] = max(boot["dropped"] or 0, dropped)
            if next_batch is not None:
                boot["next"] = max(boot["next"] or 0, next_batch)
                boot["top"] = max(boot["top"] or 0, next_batch - 1)
    lost = 0
    dropped = None
    for b in boots:
        if b["received"]:
            low = min(b["received"])
            high = max(b["received"])
            if b["next"] is not None:
                high = max(high, b["next"] - 1)
            lost += (high - low + 1) - len(b["received"])
        if b["dropped"] is not None:
            dropped = (dropped or 0) + b["dropped"]
    return {
        "lost_batches": lost,
        "dropped": dropped,
        "restarts": max(len(boots) - 1, 0),
        "batches_received": sum(len(b["received"]) for b in boots),
    }


def read_csv(path):
    with open(path, newline="") as fh:
        return [
            {k: (float(v) if k == "pc_time_s" else int(v)) for k, v in row.items()}
            for row in csv.DictReader(fh)
        ]


def info_path(csv_path) -> pathlib.Path:
    return pathlib.Path(f"{csv_path}.info.json")


def read_info(csv_path):
    """The run's info file, or the older <csv>.chip.json (dropped count only)
    from before #228. None if there is neither."""
    for path in (info_path(csv_path), pathlib.Path(f"{csv_path}.chip.json")):
        if path.exists():
            return json.loads(path.read_text())
    return None


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


def analyze(records, info=None):
    """info: the run's info file (read_info), or None if there is none."""
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
        "has_info": info is not None,
        "dropped": None if info is None else info.get("dropped"),
        "lost_batches": None if info is None else info.get("lost_batches"),
        "restarts": None if info is None else info.get("restarts"),
        "p50_ms": late_ms[n // 2] if n else 0.0,
        "p99_ms": late_ms[int(0.99 * (n - 1))] if n else 0.0,
        "max_ms": late_ms[-1] if n else 0.0,
        "silences_off": off["silences"],
        "firmware_late": firmware_late,
        "bounds": rows,
    }


def report(stats) -> str:
    lines = []
    if not stats["has_info"]:
        lines.append(
            "WARNING: no info file, so dropped records and lost packets are unknown"
        )
    elif stats["dropped"] is None:
        lines.append("WARNING: dropped count unknown (no F line received)")
    elif stats["dropped"] > 0:
        lines.append(
            f"WARNING: the ESP dropped {stats['dropped']} records (its buffer was full): "
            "the counts below are incomplete"
        )
    if stats["has_info"] and stats["lost_batches"] is None:
        lines.append(
            "WARNING: lost record packets unknown (an info file from before #228)"
        )
    elif stats["has_info"] and stats["lost_batches"] > 0:
        lines.append(
            f"WARNING: {stats['lost_batches']} record packets were lost on the way: "
            "their frames are missing, which can look like silences"
        )
    if stats["restarts"]:
        lines.append(
            f"WARNING: the ESP restarted {stats['restarts']} time(s) during the run"
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
    events = []  # B and F lines in arrival order, for loss_summary()
    print(
        f"Listening on UDP {args.port}. Start ground_link.py now. When the run "
        "ends: `q` in ground_link.py, wait at least 2 s (the ESP sends its last "
        "batch and the final counts), then Ctrl+C here."
    )
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
                events.append(("B", batch))
            if info is None:
                continue
            events.append(("F", info["dropped"], info["next_batch"]))
            same_chip = chip is not None and all(
                chip[k] == info[k] for k in CHIP_FIELDS
            )
            if not same_chip:
                print(chip_line(info), flush=True)
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
    info = {k: chip[k] for k in CHIP_FIELDS} if chip else {}
    info.update(loss_summary(events))
    info_path(out).write_text(json.dumps(info, indent=2) + "\n")
    dropped = info["dropped"]
    if dropped is None:
        dropped = "unknown (no F line received)"
    print(
        f"\nSaved {out}: {len(rows)} SETPOINT records; dropped by the ESP: {dropped}; "
        f"record packets lost on the way: {info['lost_batches']}; "
        f"ESP restarts: {info['restarts']}"
    )
    if not rows:
        print("no data: is env esp_late_log(_ota) on the ESP and a session streaming?")


def cmd_analyze(args):
    path = resolve_csv_path(args.csv)
    rows = read_csv(path)
    if not rows:
        raise SystemExit(f"No records in {args.csv}")
    print(report(analyze(rows, read_info(path))))


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
