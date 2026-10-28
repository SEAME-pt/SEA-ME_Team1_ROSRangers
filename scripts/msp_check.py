#!/usr/bin/env python3
"""MSP checks over USB or through the ESP bridge. Props off, no battery.

Read-only MSP requests only, except `rc-order --write` (MSP_SET_RAW_RC with throttle
low and every AUX channel low, refused when the FC reports it is armed).

By default it talks to the ESP of the active scenario (setup_test.py or setup_production.py).
Use --usb to talk to the FC directly.

Examples:
  python3 scripts/msp_check.py --usb info                 # cycle 0: FC over USB
  python3 scripts/msp_check.py --usb ping -n 500
  python3 scripts/msp_check.py cycle1a                    # ESP on USB, this PC plays the FC
  python3 scripts/msp_check.py info                       # cycle 1b: FC through the bridge
  python3 scripts/msp_check.py ping -n 1000 --rate 50 --csv bridge_fc.csv
  python3 scripts/msp_check.py attitude
  python3 scripts/msp_check.py fake-fc                    # only the fake FC, on the ESP USB port

Bench test plan (vault: ESP Bench Test Plan). The ESP is now soldered to the FC: run
Stage 2 with `seame.py test <id>`. cycle1a, fake-fc, usb-relay and --loopback need the
ESP on its own USB (Stage 1, before soldering):
  cycle1a --stream --sleep none --duration 600 --csv s1-05.csv   # S1-05 (S1-09: same, at the real distance)
  cycle1a --stream --sleep modem --duration 600 --csv s1-06.csv  # S1-06
  --usb status-watch --csv s2-02.csv                      # S2-02, while the ESP is power-cycled
  rc / rc-order --write                                   # S2-04 (ESP_to_FC §11 phase 3)
  link-watch --csv s2-06.csv                              # S2-06, unplug a wire mid-stream
  ping --loopback --stream --duration 600 --csv s1-09.csv # S1-09, ESP TX-RX jumper, in the cage
"""

import argparse
import bisect
import csv
import pathlib
import statistics
import struct
import sys
import threading
import time

import bridge_config as cfg
import esp_at
import msp
import serial
from fc_cli import find_port as find_fc_port

# MSP_SET_RAW_RC input order is the radio's (map AETR1234): roll, pitch, throttle, yaw.
# Index of each input in Betaflight's rcData (roll, pitch, yaw, throttle).
AETR_TO_RC = (0, 1, 3, 2)
RC_DEFAULT = (1500, 1500, 1500, 1000, 1000, 1000, 1000, 1000)
RC_ORDER_SENT = (1200, 1400, 1000, 1600)  # A, E, T, R: distinct, throttle stays low
RC_ORDER_EXPECTED = (
    1200,
    1400,
    1600,
    1000,
)  # MSP_RC readback: roll, pitch, yaw, throttle
RC_AUX_LOW = (1000, 1000, 1000, 1000)  # AUX1-4 low: the ARM switch stays off

FAKE_ANSWERS = {
    msp.MSP_API_VERSION: bytes([0, 1, 46]),
    msp.MSP_FC_VARIANT: b"BTFL",
    msp.MSP_FC_VERSION: bytes([4, 5, 1]),
    msp.MSP_BOARD_INFO: b"FAKE",
    msp.MSP_STATUS: struct.pack("<HHHI", 125, 0, 0x23, 0) + bytes(5),
    msp.MSP_ATTITUDE: struct.pack("<hhh", 0, 0, 0),
    msp.MSP_ANALOG: bytes([0]),
}


def make_client(args):
    allow_write = getattr(args, "write", False)
    loopback = getattr(args, "loopback", False)
    if args.usb:
        return msp.Client(
            msp.SerialTransport(args.fc_port or find_fc_port(), 115200),
            trace=args.trace,
            allow_write=allow_write,
        ), "FC over USB"
    p = cfg.load_profile()
    if not p:
        sys.exit(
            "No scenario configured. Run setup_test.py or setup_production.py first, or use --usb."
        )
    host = args.esp or p["esp_ip"]
    t = msp.UdpTransport(host, p["esp_port"], p["pc_port"])
    return (
        msp.Client(t, trace=args.trace, allow_write=allow_write, loopback=loopback),
        f"bridge {p['scenario'].upper()} via {host}:{p['esp_port']}",
    )


# --stream rotates these so an answer can be matched to its request (MSP v1 has no IDs).
# All read-only, with answers of 3-7 bytes so the UART time is the same for each.
STREAM_COMMANDS = (
    msp.MSP_API_VERSION,
    msp.MSP_FC_VARIANT,
    msp.MSP_FC_VERSION,
    msp.MSP_ATTITUDE,
    msp.MSP_ANALOG,
)
T_LOSS_MS = 250  # initial loss timeout, ADR-004 / OD-07 in PC_to_ESP


def run_ping(client, n, rate, timeout, quiet=False, answer_times=None):
    rtts, lost, records = [], 0, []
    period = 1.0 / rate if rate else 0
    try:
        for i in range(n):
            t_next = time.perf_counter() + period
            f, rtt = client.request(msp.MSP_API_VERSION, timeout)
            if f is None:
                lost += 1
                records.append({"request": i + 1, "status": "timeout", "rtt_ms": ""})
            else:
                rtt_ms = rtt * 1000
                rtts.append(rtt_ms)
                records.append(
                    {"request": i + 1, "status": "ok", "rtt_ms": f"{rtt_ms:.3f}"}
                )
                if answer_times is not None:
                    answer_times.append(time.perf_counter())
            if not quiet and (i + 1) % max(1, n // 10) == 0:
                print(f"  {i + 1}/{n}", flush=True)
            time.sleep(max(0.0, t_next - time.perf_counter()))
    except KeyboardInterrupt:
        pass
    return rtts, lost, records


def run_stream(
    client, n, rate, timeout, quiet=False, answer_times=None, clock=time.perf_counter
):
    """Send on a fixed schedule (request i at start + i/rate) without waiting for answers.

    Sends and answers are logged during the run and matched afterwards (match_stream).
    Returns (rtts, lost, records) like run_ping.
    """
    period = 1.0 / rate
    sends, answers = [], []
    start = clock()
    try:
        while len(sends) < n or clock() <= sends[-1][1] + timeout:
            if len(sends) < n and clock() >= start + len(sends) * period:
                cmd = STREAM_COMMANDS[len(sends) % len(STREAM_COMMANDS)]
                client.t.send(msp.encode_request(cmd))
                sends.append((cmd, clock()))
                if not quiet and len(sends) % max(1, n // 10) == 0:
                    print(f"  {len(sends)}/{n}", flush=True)
            data = client.t.recv()
            now = clock()
            for f in client.parser.feed(data):
                if client.is_answer(f):
                    answers.append((f.cmd, now))
    except KeyboardInterrupt:
        pass
    if answer_times is not None:
        answer_times.extend(t for _, t in answers)
    matched = match_stream(sends, answers, timeout)
    rtts, records = [], []
    for i in range(len(sends)):
        if i in matched:
            rtts.append(matched[i] * 1000)
            records.append(
                {"request": i + 1, "status": "ok", "rtt_ms": f"{rtts[-1]:.3f}"}
            )
        else:
            records.append({"request": i + 1, "status": "timeout", "rtt_ms": ""})
    return rtts, len(sends) - len(matched), records


def match_stream(sends, answers, timeout):
    """Match answers to requests. Returns {request index: RTT in seconds}.

    sends: [(cmd, time sent)], answers: [(cmd, time received)], both in time order.
    One ESP and one UDP flow keep the order, so the answers are a subsequence of the
    requests. Among the matchings that fit (same command, 0 < RTT <= timeout, order
    kept) this takes the one with the smallest total RTT: after a burst of drops the
    answers belong to the newest requests, during a latency spike to the oldest.
    An answer that fits nowhere is left out.
    """
    times_by_cmd = {}
    for i, (cmd, t) in enumerate(sends):
        idx, times = times_by_cmd.setdefault(cmd, ([], []))
        idx.append(i)
        times.append(t)
    send_times = [t for _, t in sends]
    # leaving an answer out must cost more than any RTT saving it could buy
    skip_cost = 1000.0 * timeout
    states = {-1: (0.0, None)}  # last matched request -> (total cost, matched path)
    for cmd, t_ans in answers:
        # requests sent before t_ans - timeout can never be matched again: states that
        # differ only below that horizon have the same future, keep the cheapest
        horizon = bisect.bisect_left(send_times, t_ans - timeout) - 1
        merged = {}
        for last, state in states.items():
            key = max(last, horizon)
            if key not in merged or state[0] < merged[key][0]:
                merged[key] = state
        states = merged
        idx, times = times_by_cmd.get(cmd, ([], []))
        lo = bisect.bisect_left(times, t_ans - timeout)
        hi = bisect.bisect_left(times, t_ans)
        cands = idx[lo:hi]
        new = {}
        for last, (cost, path) in states.items():
            options = [(last, cost + skip_cost, path)]
            options += [
                (i, cost + t_ans - sends[i][1], (i, t_ans, path))
                for i in cands
                if i > last
            ]
            for key, c, pth in options:
                if key not in new or c < new[key][0]:
                    new[key] = (c, pth)
        # a lower last index with a lower cost is at least as good: drop the others
        states, best = {}, float("inf")
        for last in sorted(new):
            if new[last][0] < best:
                states[last] = new[last]
                best = new[last][0]
    _, path = min(states.values(), key=lambda s: s[0])
    matched = {}
    while path:
        i, t_ans, path = path
        matched[i] = t_ans - sends[i][1]
    return matched


def timing_metrics(answer_times, duration, sent, gap_limit_ms=T_LOSS_MS):
    """Real duration and rate, and the silences between answers (what T_loss must cover)."""
    gaps = [1000 * (b - a) for a, b in zip(answer_times, answer_times[1:])]
    return {
        "duration_s": round(duration, 3),
        "effective_rate_hz": round(sent / duration, 2) if duration else "",
        "max_gap_ms": round(max(gaps), 3) if gaps else "",
        "p99_gap_ms": round(percentile(gaps, 0.99), 3) if gaps else "",
        f"gaps_over_{gap_limit_ms:g}ms": sum(g > gap_limit_ms for g in gaps),
    }


def run_test(client, args):
    """Run ping or --stream and return (rtts, lost, records, timing metrics)."""
    answer_times = []
    t0 = time.perf_counter()
    run = run_stream if args.stream else run_ping
    rtts, lost, records = run(
        client, args.n, args.rate, args.timeout, args.quiet, answer_times=answer_times
    )
    extra = timing_metrics(
        answer_times, time.perf_counter() - t0, len(records), args.gap_limit
    )
    return rtts, lost, records, extra


def print_timing(extra):
    gaps_key = next(k for k in extra if k.startswith("gaps_over_"))
    print(
        f"Ran {extra['duration_s']:.0f} s at {extra['effective_rate_hz']} Hz effective   "
        f"longest silence {extra['max_gap_ms'] or 0:.0f} ms   "
        f"silences over {gaps_key[10:-2]} ms: {extra[gaps_key]}"
    )


def print_stats(rtts, lost, bad):
    sent = len(rtts) + lost
    print(
        f"\nRequests: {sent}   answers: {len(rtts)}   lost: {lost} ({100 * lost / max(1, sent):.1f} %)"
    )
    if rtts:
        s = sorted(rtts)
        print(
            f"RTT ms   min {s[0]:.1f}   mean {statistics.mean(s):.1f}   "
            f"median {statistics.median(s):.1f}   p95 {percentile(s, 0.95):.1f}   "
            f"p99 {percentile(s, 0.99):.1f}   max {s[-1]:.1f}"
        )
    print(f"Bad checksums: {bad}")


def percentile(values, fraction):
    if not values:
        return ""
    values = sorted(values)
    return values[int(fraction * (len(values) - 1))]


def resolve_csv_path(path):
    csv_path = pathlib.Path(path)
    if not csv_path.is_absolute() and csv_path.parent == pathlib.Path("."):
        csv_path = esp_at.RESULTS_DIR / csv_path
    return csv_path


def write_rows(path, rows, fields):
    """Save rows to a CSV (bare file names go into results/). Returns the path."""
    out = resolve_csv_path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved {out}")
    return out


def save_csv(path, records, lost, bad, extra=None):
    if not path:
        return
    raw_path = resolve_csv_path(path)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    with raw_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=("request", "status", "rtt_ms"))
        writer.writeheader()
        writer.writerows(records)

    rtts = [float(row["rtt_ms"]) for row in records if row["rtt_ms"]]
    sent = len(records)
    analytics_path = raw_path.with_name(
        f"{raw_path.stem}_analytics{raw_path.suffix or '.csv'}"
    )
    metrics = {
        "requests": sent,
        "answers": len(rtts),
        "lost": lost,
        "loss_percent": 100 * lost / max(1, sent),
        "bad_checksums": bad,
        "min_rtt_ms": min(rtts) if rtts else "",
        "mean_rtt_ms": statistics.mean(rtts) if rtts else "",
        "median_rtt_ms": statistics.median(rtts) if rtts else "",
        "p95_rtt_ms": percentile(rtts, 0.95),
        "p99_rtt_ms": percentile(rtts, 0.99),
        "max_rtt_ms": max(rtts) if rtts else "",
        "stdev_rtt_ms": statistics.pstdev(rtts) if rtts else "",
        **(extra or {}),
    }
    with analytics_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(("metric", "value"))
        writer.writerows(metrics.items())
    print(f"Raw request data saved to {raw_path}")
    print(f"Analytics saved to {analytics_path}")


def cmd_info(args):
    c, label = make_client(args)
    print(f"Target: {label}")
    try:
        read_commands(
            c,
            args.timeout,
            [
                ("API version", msp.MSP_API_VERSION),
                ("FC variant", msp.MSP_FC_VARIANT),
                ("FC version", msp.MSP_FC_VERSION),
                ("Board", msp.MSP_BOARD_INFO),
                ("Status", msp.MSP_STATUS),
                ("Attitude", msp.MSP_ATTITUDE),
                ("Analog", msp.MSP_ANALOG),
            ],
        )
    finally:
        c.close()


def read_commands(client, timeout, commands):
    for name, cmd in commands:
        f, rtt = client.request(cmd, timeout)
        if f is None:
            print(f"{name:12} no answer within {timeout * 1000:.0f} ms")
        elif f.direction == "!":
            print(f"{name:12} FC returned an MSP error")
        else:
            print(f"{name:12} {msp.decode(cmd, f.payload)}  ({rtt * 1000:.1f} ms)")


def cmd_core(args):
    if not args.usb:
        sys.exit("The core command requires --usb to read the FC directly.")
    c, label = make_client(args)
    print(f"Target: {label}")
    try:
        read_commands(
            c,
            args.timeout,
            [
                ("API version", msp.MSP_API_VERSION),
                ("Status", msp.MSP_STATUS),
                ("Attitude", msp.MSP_ATTITUDE),
            ],
        )
    finally:
        c.close()


def apply_duration(args):
    """--duration SECONDS sets -n from the rate (S1-05 asks for 10 minutes at 50 Hz)."""
    if (args.duration or getattr(args, "stream", False)) and not args.rate:
        sys.exit("--duration and --stream need a fixed --rate.")
    if args.duration:
        args.n = int(args.duration * args.rate)


def cmd_ping(args):
    apply_duration(args)
    c, label = make_client(args)
    mode = "streamed" if args.stream else "one at a time"
    print(f"Target: {label}, {args.n} requests at {args.rate or 'max'} Hz ({mode})")
    try:
        rtts, lost, records, extra = run_test(c, args)
    finally:
        c.close()
    print_stats(rtts, lost, c.parser.bad_checksums)
    print_timing(extra)
    send_errors = getattr(c.t, "send_errors", 0)
    if send_errors:
        print(
            f"Could not send {send_errors} requests (Wi-Fi down?): counted as lost. "
            "Check `journalctl -u NetworkManager` for the drops."
        )
    save_csv(args.csv, records, lost, c.parser.bad_checksums, extra)


def cmd_attitude(args):
    c, label = make_client(args)
    print(f"Target: {label}. Ctrl+C to stop. Tilt the drone by hand (props off).")
    try:
        while True:
            try:
                f, rtt = c.request(msp.MSP_ATTITUDE, args.timeout)
            except serial.SerialException as exc:
                sys.exit(
                    f"ERROR: FC serial connection was lost: {exc}. "
                    "Check the USB cable and FC power, then run the command again."
                )
            if f is None:
                print("no answer", flush=True)
            else:
                a = msp.decode(msp.MSP_ATTITUDE, f.payload)
                print(
                    f"roll {a['roll_deg']:7.1f}  pitch {a['pitch_deg']:7.1f}  "
                    f"yaw {a['yaw_deg']:4d}   ({rtt * 1000:.1f} ms)",
                    flush=True,
                )
            time.sleep(1.0 / args.rate)
    except KeyboardInterrupt:
        pass
    finally:
        c.close()


def cmd_rc(args):
    c, label = make_client(args)
    print(f"Target: {label}. Ctrl+C to stop. Move the sticks and switches (props off).")
    try:
        while True:
            f, rtt = c.request(msp.MSP_RC, args.timeout)
            if f is None:
                print("no answer", flush=True)
            else:
                print(
                    format_rc(msp.decode(msp.MSP_RC, f.payload)["channels"]), flush=True
                )
            time.sleep(1.0 / args.rate)
    except KeyboardInterrupt:
        pass
    finally:
        c.close()


def format_rc(channels):
    names = [n[:3] for n in msp.RC_NAMES] + [
        f"aux{i}" for i in range(1, len(channels) - 3)
    ]
    return "  ".join(f"{n} {v:4d}" for n, v in zip(names, channels))


def check_rc_order(readback):
    """Compare an MSP_RC readback with RC_ORDER_EXPECTED. Returns (rows, all_ok)."""
    sent_as = {v: name for v, name in zip(RC_ORDER_SENT, ("A", "E", "T", "R"))}
    rows = []
    for name, want, got in zip(msp.RC_NAMES, RC_ORDER_EXPECTED, readback):
        rows.append((name, want, got, sent_as.get(got, "?"), got == want))
    return rows, len(rows) == 4 and all(r[4] for r in rows)


def cmd_rc_order(args):
    if not args.write:
        sys.exit(
            "rc-order sends MSP_SET_RAW_RC (throttle and AUX low). Props off, no battery.\n"
            "Add --write if you mean it."
        )
    c, label = make_client(args)
    print(f"Target: {label}")
    try:
        f, _ = c.request(msp.MSP_STATUS, args.timeout)
        if f is None or f.direction == "!":
            sys.exit("No MSP_STATUS answer: not sending anything.")
        if msp.decode_status(f.payload)["armed"]:
            sys.exit("The FC reports ARMED. Refusing to send RC values.")
        f, _ = c.request(msp.MSP_RC, args.timeout)
        before = msp.decode(msp.MSP_RC, f.payload)["channels"] if f else []
        print(f"Before: {format_rc(before)}" if before else "Before: no MSP_RC answer")
        frame = msp.encode_rc(RC_ORDER_SENT + RC_AUX_LOW)
        acks, t0 = 0, time.time()
        while time.time() - t0 < args.seconds:
            ack, _ = c.write(msp.MSP_SET_RAW_RC, frame, args.timeout)
            acks += ack is not None and ack.direction == ">"
            time.sleep(0.02)  # 50 Hz, like a receiver
        f, _ = c.request(msp.MSP_RC, args.timeout)
    finally:
        c.close()
    if f is None:
        sys.exit("No MSP_RC answer after sending.")
    after = msp.decode(msp.MSP_RC, f.payload)["channels"]
    print(f"Sent A,E,T,R = {RC_ORDER_SENT}, AUX1-4 = {RC_AUX_LOW}; acknowledged {acks}")
    print(f"After:  {format_rc(after)}\n")
    rows, ok = check_rc_order(after)
    for name, want, got, sent, good in rows:
        print(
            f"  {name:8} expected {want}  got {got} (input {sent})  {'OK' if good else 'WRONG'}"
        )
    if not ok and before[:4] == after[:4]:
        print(
            "\nThe FC did not apply the values: the receiver is not MSP and MSP Override "
            "is off. Expected until override is configured (ESP_to_FC §11 phase 3)."
        )
    print(f"\nS2-04 {'PASS' if ok else 'FAIL'}  (channel order AETR1234)")


def run_link_watch(
    client,
    rate,
    lost_after,
    timeout,
    duration=None,
    clock=time.perf_counter,
    sleep=time.sleep,
):
    """Poll the FC and log when it stops answering (S2-06). Returns (events, polls, answers).

    'lost' ms = time since the last answer when the loss was declared.
    'back' ms = whole outage, from the last answer before the loss.
    """
    events, polls, answers = [], 0, 0
    period = 1.0 / rate
    start = last_ok = clock()
    lost = False
    try:
        while duration is None or clock() - start < duration:
            t_next = clock() + period
            try:
                f, _ = client.request(msp.MSP_API_VERSION, timeout)
            except serial.SerialException:
                f, lost_port = None, True
            else:
                lost_port = False
            now = clock()
            polls += 1
            if f is not None:
                answers += 1
                if lost:
                    events.append(
                        {
                            "event": "back",
                            "time_s": now - start,
                            "ms": 1000 * (now - last_ok),
                        }
                    )
                    print(
                        f"{now - start:8.2f} s  FC back after {events[-1]['ms']:.0f} ms"
                    )
                    lost = False
                last_ok = now
            elif not lost and (lost_port or now - last_ok >= lost_after):
                lost = True
                events.append(
                    {
                        "event": "lost",
                        "time_s": now - start,
                        "ms": 1000 * (now - last_ok),
                    }
                )
                print(
                    f"{now - start:8.2f} s  FC LOST, detected {events[-1]['ms']:.0f} ms after the last answer"
                )
            if lost_port:
                print("The serial port is gone: stopping.")
                break
            sleep(max(0.0, t_next - clock()))
    except KeyboardInterrupt:
        pass
    return events, polls, answers


def cmd_link_watch(args):
    c, label = make_client(args)
    print(
        f"Target: {label}, polling at {args.rate} Hz, lost after {args.lost_after * 1000:.0f} ms.\n"
        "Now unplug a jumper wire (or stop fake-fc / the USB relay), then put it back. "
        "Ctrl+C to finish."
    )
    try:
        events, polls, answers = run_link_watch(
            c, args.rate, args.lost_after, args.poll_timeout, args.duration
        )
    finally:
        c.close()
    losses = [e for e in events if e["event"] == "lost"]
    print(f"\nPolls {polls}, answers {answers}, losses detected {len(losses)}")
    if args.csv:
        write_rows(
            args.csv,
            [
                {
                    "event": e["event"],
                    "time_s": f"{e['time_s']:.3f}",
                    "ms": f"{e['ms']:.0f}",
                }
                for e in events
            ],
            ("event", "time_s", "ms"),
        )
    if not losses:
        print("S2-06 INCONCLUSIVE: no loss happened during the run.")
        return
    worst = max(e["ms"] for e in losses)
    ok = worst <= args.max_detect_ms
    print(
        f"Slowest detection {worst:.0f} ms\n"
        f"S2-06 {'PASS' if ok else 'FAIL'}  (every loss detected within {args.max_detect_ms:.0f} ms)"
    )


def status_changes(prev, cur):
    """Human-readable differences between two decoded MSP_STATUS answers."""
    msgs = []
    if cur["armed"] != prev["armed"]:
        msgs.append("ARMED" if cur["armed"] else "disarmed")
    if cur["i2c_errors"] != prev["i2c_errors"]:
        msgs.append(f"I2C errors {prev['i2c_errors']} → {cur['i2c_errors']}")
    for key in ("arming_disable_flags", "reboot_required"):
        if key in cur and cur.get(key) != prev.get(key):
            old, new = prev.get(key), cur[key]
            if key == "arming_disable_flags":
                old = "?" if old is None else f"0x{old:08x}"
                new = f"0x{new:08x}"
            msgs.append(f"{key} {old} → {new}")
    return msgs


def cmd_status_watch(args):
    c, label = make_client(args)
    print(
        f"Target: {label}. Watching MSP_STATUS at {args.rate} Hz.\n"
        "Power-cycle the ESP now (20×). Ctrl+C to finish."
    )
    rows, first, prev, timeouts, port_lost = [], None, None, 0, ""
    t0 = time.time()
    try:
        while args.duration is None or time.time() - t0 < args.duration:
            t = time.time() - t0
            try:
                f, _ = c.request(msp.MSP_STATUS, args.timeout)
            except serial.SerialException as exc:
                port_lost = str(exc)
                print(f"{t:8.1f} s  FC connection lost: {exc}")
                break
            if f is None or f.direction == "!" or len(f.payload) < 10:
                timeouts += 1
                print(f"{t:8.1f} s  no answer")
                rows.append({"time_s": f"{t:.2f}", "status": "timeout"})
                continue
            st = msp.decode_status(f.payload)
            if prev is None:
                first = st
                print(f"{t:8.1f} s  baseline {st}")
            else:
                for msg in status_changes(prev, st):
                    print(f"{t:8.1f} s  {msg}")
            rows.append(
                {
                    "time_s": f"{t:.2f}",
                    "status": "ok",
                    **{k: st.get(k, "") for k in STATUS_FIELDS},
                }
            )
            prev = st
            time.sleep(1.0 / args.rate)
    except KeyboardInterrupt:
        pass
    finally:
        c.close()
    if args.csv:
        write_rows(args.csv, rows, ("time_s", "status", *STATUS_FIELDS))
    problems = []
    if port_lost:
        problems.append("the FC connection was lost (the FC may have rebooted)")
    if timeouts:
        problems.append(f"{timeouts} requests without an answer")
    if any(r.get("armed") is True for r in rows):
        problems.append("the FC armed")
    if first and prev and prev["i2c_errors"] > first["i2c_errors"]:
        problems.append(f"I2C errors rose {first['i2c_errors']} → {prev['i2c_errors']}")
    if any(r.get("reboot_required") is True for r in rows):
        problems.append("the FC asked for a reboot")
    print(f"\nPolls {len(rows)}")
    for p in problems:
        print(f"  - {p}")
    print(
        f"S2-02 {'FAIL' if problems else 'PASS'}  (FC side; also compare `fc dump` "
        "before and after with `fc compare-diffs`)"
    )


STATUS_FIELDS = (
    "cycle_time_us",
    "cpu_load",
    "i2c_errors",
    "armed",
    "arming_disable_flags",
    "reboot_required",
)


def fake_answer(cmd, payload, rc):
    """The fake FC's answer to one request: (payload, error). `rc` is its rcData list,
    changed by MSP_SET_RAW_RC exactly as Betaflight does with map AETR1234."""
    if cmd == msp.MSP_SET_RAW_RC:
        channels = struct.unpack_from(f"<{len(payload) // 2}H", payload)
        for value, index in zip(channels, AETR_TO_RC):
            rc[index] = value
        for i, value in enumerate(channels[4 : len(rc)], start=4):
            rc[i] = value
        return b"", False
    if cmd == msp.MSP_RC:
        return msp.encode_rc(rc), False
    if cmd in FAKE_ANSWERS:
        return FAKE_ANSWERS[cmd], False
    return b"", True


def fake_fc(
    port: str, stop: threading.Event, stats: dict, trace: bool = False, ser=None
):
    """Answer MSP requests on the ESP's USB serial port, like a Betaflight FC would.
    Pass an open `ser` to keep the port (and the ESP, which resets when it opens) alive."""
    parser = msp.Parser()
    rc = list(RC_DEFAULT)
    own_port = ser is None
    recovery_deadline = time.monotonic() + 10 if not own_port else None
    try:
        if own_port:
            ser = esp_at.open_port(port)
        ser.timeout = 0.005
        while not stop.is_set():
            try:
                received = ser.read(512)
            except serial.SerialException as exc:
                if stop.is_set():
                    break
                if own_port or recovery_deadline is None:
                    raise
                if time.monotonic() >= recovery_deadline:
                    raise serial.SerialException(
                        f"{exc} (serial port did not recover after the ESP reset)"
                    ) from exc
                time.sleep(0.1)
                continue
            for f in parser.feed(received):
                if f.direction != "<":
                    continue
                answer, error = fake_answer(f.cmd, f.payload, rc)
                response = msp.encode_response(f.cmd, answer, error=error)
                if trace:
                    print(
                        f"ESP/FC received: {msp.encode_request(f.cmd, f.payload).hex(' ')}",
                        flush=True,
                    )
                    print(f"ESP/FC sent:      {response.hex(' ')}", flush=True)
                try:
                    ser.write(response)
                except serial.SerialException as exc:
                    if stop.is_set():
                        break
                    if own_port or recovery_deadline is None:
                        raise
                    if time.monotonic() >= recovery_deadline:
                        raise serial.SerialException(
                            f"{exc} (serial port did not recover after the ESP reset)"
                        ) from exc
                    time.sleep(0.1)
                    break
                stats["answered"] += 1
            stats["bad"] = parser.bad_checksums
    except serial.SerialException as exc:
        stats["error"] = str(exc)
        print(f"Fake FC error on {port}: {exc}", file=sys.stderr, flush=True)
    finally:
        stats["bad"] = parser.bad_checksums
        if own_port and ser is not None:
            ser.close()


def cmd_fake_fc(args):
    port = args.esp_port_usb or esp_at.find_port()
    stop, stats = threading.Event(), {"answered": 0, "bad": 0}
    print(f"Fake FC on {port}. Ctrl+C to stop.")
    try:
        fake_fc(port, stop, stats, trace=args.trace)
    except KeyboardInterrupt:
        pass
    print(f"Answered {stats['answered']} requests, bad checksums {stats['bad']}")


def cmd_usb_relay(args):
    """Relay MSP bytes between the ESP USB serial port and the FC USB port."""
    esp_port = args.esp_port_usb or esp_at.find_port()
    fc_port = args.fc_port or find_fc_port()
    esp_ser = esp_at.open_port(esp_port)
    fc_ser = serial.Serial(fc_port, 115200, timeout=0.01)
    if args.sleep:
        print(f"Waiting {args.settle:.0f} s for the ESP to boot into the bridge...")
        time.sleep(args.settle)
        try:
            set_sleep(esp_ser, args.sleep)
        except (esp_at.EspError, serial.SerialException) as exc:
            esp_ser.close()
            fc_ser.close()
            sys.exit(f"ERROR: {esp_error_text(exc)}")
    stop = threading.Event()
    errors = []

    def forward(source, target, label):
        try:
            while not stop.is_set():
                data = source.read(512)
                if data:
                    target.write(data)
        except serial.SerialException as exc:
            errors.append(f"{label}: {exc}")
            stop.set()

    threads = [
        threading.Thread(
            target=forward, args=(esp_ser, fc_ser, "ESP → FC"), daemon=True
        ),
        threading.Thread(
            target=forward, args=(fc_ser, esp_ser, "FC → ESP"), daemon=True
        ),
    ]
    print(f"USB relay active: ESP {esp_port} ↔ FC {fc_port}. " "Press Ctrl+C to stop.")
    for thread in threads:
        thread.start()
    try:
        while not stop.wait(0.2):
            pass
    except KeyboardInterrupt:
        print("\nStopping USB relay.")
    finally:
        stop.set()
        for thread in threads:
            thread.join(1)
        esp_ser.close()
        fc_ser.close()
    if errors:
        sys.exit(f"USB relay stopped: {errors[0]}")


def esp_error_text(exc):
    if isinstance(exc, serial.SerialException):
        return (
            f"lost the ESP USB serial port ({exc}).\n"
            "The USB adapter probably dropped off the bus: check `journalctl -k | grep -i usb` "
            "for 'USB disconnect', try another cable or port (no unpowered hub), then run again."
        )
    return str(exc)


def set_sleep(esp_ser, mode):
    if cfg.load_profile().get("scenario") == "production":
        print(
            "NOTE: the ESP is a SoftAP in PRODUCTION, and AT+SLEEP only affects station mode."
        )
    print(f"Setting Wi-Fi sleep to '{mode}' (AT+SLEEP={esp_at.SLEEP_MODES[mode]})")
    before = esp_at.set_sleep_in_bridge(esp_ser, mode)
    print(f"  sleep was {before}, now {esp_at.SLEEP_MODES[mode]}; bridge running again")


def cmd_cycle1a(args):
    """ESP on USB in bridge mode. This PC plays the FC on the ESP USB port and pings through Wi-Fi."""
    if not cfg.load_profile():
        sys.exit(
            "No scenario configured. Run setup_test.py (or setup_production.py) first."
        )
    apply_duration(args)
    port = args.esp_port_usb or esp_at.find_port()
    esp_ser = esp_at.open_port(port)
    stop, stats = threading.Event(), {"answered": 0, "bad": 0}

    def start_fake_fc():
        th = threading.Thread(
            target=fake_fc,
            args=(port, stop, stats, args.trace),
            kwargs={"ser": esp_ser},
            daemon=True,
        )
        th.start()
        return th

    th = start_fake_fc()
    c, label = make_client(args)
    print(
        f"Cycle 1a · {label} · fake FC on {port} · {args.n} requests at {args.rate} Hz"
    )
    # Opening the ESP USB port resets the ESP (DTR/RTS): wait until it is back on Wi-Fi.
    print(
        f"Waiting for the bridge (the ESP reboots when its USB port opens), up to {args.wait:.0f} s..."
    )
    t0 = time.time()
    try:
        while True:
            f, _ = c.request(msp.MSP_API_VERSION, 0.5)
            if f is not None:
                break
            if time.time() - t0 > args.wait:
                print(
                    "\nFAIL: no answer through the bridge. Check `setup_test.py status` "
                    "(or `setup_production.py connect`)."
                )
                return
        print(f"Bridge ready after {time.time() - t0:.1f} s")
        if args.sleep:
            stop.set()
            th.join(2)
            stop.clear()
            set_sleep(esp_ser, args.sleep)
            th = start_fake_fc()
        time.sleep(0.5)
        bad_before = (
            c.parser.bad_checksums + stats["bad"]
        )  # boot noise from the ESP does not count
        rtts, lost, records, extra = run_test(c, args)
    except (esp_at.EspError, serial.SerialException) as exc:
        print(f"\nFAIL: {esp_error_text(exc)}")
        return
    finally:
        c.close()
        stop.set()
        th.join(2)
        esp_ser.close()
    bad = c.parser.bad_checksums + stats["bad"] - bad_before
    fake_fc_error = stats.get("error")
    if fake_fc_error:
        print(f"Fake FC stopped unexpectedly: {fake_fc_error}", file=sys.stderr)
    print_stats(rtts, lost, bad)
    print_timing(extra)
    save_csv(args.csv, records, lost, bad, extra)
    loss = 100 * lost / max(1, len(rtts) + lost)
    ok = loss <= args.max_loss and bad == 0 and not fake_fc_error
    print(
        f"\nCycle 1a {'PASS' if ok else 'FAIL'}  (criteria: loss ≤ {args.max_loss} %, no bad checksums)"
    )


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--usb", action="store_true", help="talk to the FC directly over USB"
    )
    ap.add_argument("--fc-port", help="FC USB serial port (default: auto-detect)")
    ap.add_argument("--esp", help="override the ESP IP from the saved scenario")
    ap.add_argument(
        "--esp-port-usb", help="ESP USB serial port for fake-fc/cycle1a (default: auto)"
    )
    ap.add_argument(
        "--timeout", type=float, default=0.5, help="seconds to wait for each answer"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser(
        "info", help="API version, firmware, board, status, attitude, battery"
    )
    p.add_argument(
        "--trace", action="store_true", help="print raw MSP frames sent and received"
    )
    p = sub.add_parser(
        "core",
        help="read API version, status, and attitude (use with --usb)",
    )
    p.add_argument(
        "--trace", action="store_true", help="print raw MSP frames sent and received"
    )
    for name, n, help_ in (
        ("ping", 100, "round-trip time and loss with MSP_API_VERSION"),
        ("cycle1a", 500, "bridge test without the drone (ESP on USB, fake FC)"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("-n", type=int, default=n)
        p.add_argument(
            "--rate", type=float, default=50, help="requests per second (0 = max)"
        )
        p.add_argument("--csv", help="save every RTT to this CSV file")
        p.add_argument(
            "--duration", type=float, help="run for this many seconds (sets -n)"
        )
        p.add_argument(
            "--stream",
            action="store_true",
            help="send on a fixed schedule without waiting for each answer (true --rate)",
        )
        p.add_argument(
            "--gap-limit",
            type=float,
            default=T_LOSS_MS,
            help="count silences between answers longer than this, in ms (T_loss)",
        )
        p.add_argument("--quiet", action="store_true")
        p.add_argument(
            "--trace",
            action="store_true",
            help="print raw MSP frames sent and received",
        )
        if name == "ping":
            p.add_argument(
                "--loopback",
                action="store_true",
                help="the ESP has TX wired to RX: count the echoed request as the answer",
            )
        if name == "cycle1a":
            p.add_argument(
                "--max-loss", type=float, default=1.0, help="pass threshold in %%"
            )
            p.add_argument(
                "--wait", type=float, default=20, help="seconds to wait for the bridge"
            )
            p.add_argument(
                "--sleep",
                choices=esp_at.SLEEP_MODES,
                help="set the ESP Wi-Fi sleep mode once the bridge is up (S1-06)",
            )
    p = sub.add_parser("attitude", help="print roll/pitch/yaw continuously")
    p.add_argument("--rate", type=float, default=10)
    p.add_argument(
        "--trace", action="store_true", help="print raw MSP frames sent and received"
    )
    p = sub.add_parser("fake-fc", help="only answer MSP on the ESP USB port")
    p.add_argument(
        "--trace", action="store_true", help="print raw MSP frames received and sent"
    )
    p = sub.add_parser(
        "usb-relay",
        help="relay MSP bytes between the ESP USB port and the FC USB port",
    )
    p.add_argument(
        "--sleep", choices=esp_at.SLEEP_MODES, help="set the ESP Wi-Fi sleep mode first"
    )
    p.add_argument(
        "--settle",
        type=float,
        default=8,
        help="seconds for the ESP to reach the bridge before --sleep",
    )
    p = sub.add_parser("rc", help="print the RC channels the FC sees (MSP_RC)")
    p.add_argument("--rate", type=float, default=5)
    p.add_argument("--trace", action="store_true", help="print raw MSP frames")
    p = sub.add_parser(
        "rc-order", help="S2-04: send distinct AETR values and read them back (--write)"
    )
    p.add_argument(
        "--write", action="store_true", help="allow MSP_SET_RAW_RC (props off)"
    )
    p.add_argument(
        "--seconds", type=float, default=1, help="how long to send the values"
    )
    p.add_argument("--trace", action="store_true", help="print raw MSP frames")
    p = sub.add_parser(
        "link-watch", help="S2-06: report when the FC stops answering, and how fast"
    )
    p.add_argument("--rate", type=float, default=50, help="polls per second")
    p.add_argument(
        "--lost-after",
        type=float,
        default=0.3,
        help="seconds without an answer before the FC is declared lost",
    )
    p.add_argument(
        "--poll-timeout",
        type=float,
        default=0.1,
        help="seconds to wait for each answer",
    )
    p.add_argument("--max-detect-ms", type=float, default=500, help="pass threshold")
    p.add_argument("--duration", type=float, help="stop after this many seconds")
    p.add_argument("--csv", help="save the loss events to this CSV file")
    p.add_argument("--trace", action="store_true", help="print raw MSP frames")
    p = sub.add_parser(
        "status-watch", help="S2-02: log FC status changes (use with --usb)"
    )
    p.add_argument("--rate", type=float, default=10, help="polls per second")
    p.add_argument("--duration", type=float, help="stop after this many seconds")
    p.add_argument("--csv", help="save every poll to this CSV file")
    p.add_argument("--trace", action="store_true", help="print raw MSP frames")
    args = ap.parse_args()
    {
        "info": cmd_info,
        "core": cmd_core,
        "ping": cmd_ping,
        "attitude": cmd_attitude,
        "fake-fc": cmd_fake_fc,
        "usb-relay": cmd_usb_relay,
        "cycle1a": cmd_cycle1a,
        "rc": cmd_rc,
        "rc-order": cmd_rc_order,
        "link-watch": cmd_link_watch,
        "status-watch": cmd_status_watch,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
