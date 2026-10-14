#!/usr/bin/env python3
"""MSP checks over USB or through the ESP bridge. Read-only MSP requests only. Props off.

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
"""

import argparse
import statistics
import struct
import sys
import threading
import time

import bridge_config as cfg
import esp_at
import msp
from fc_cli import find_port as find_fc_port

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
    if args.usb:
        return msp.Client(
            msp.SerialTransport(args.fc_port or find_fc_port(), 115200),
            trace=args.trace,
        ), "FC over USB"
    p = cfg.load_profile()
    if not p:
        sys.exit(
            "No scenario configured. Run setup_test.py or setup_production.py first, or use --usb."
        )
    host = args.esp or p["esp_ip"]
    t = msp.UdpTransport(host, p["esp_port"], p["pc_port"])
    return (
        msp.Client(t, trace=args.trace),
        f"bridge {p['scenario'].upper()} via {host}:{p['esp_port']}",
    )


def run_ping(client, n, rate, timeout, quiet=False):
    rtts, lost = [], 0
    period = 1.0 / rate if rate else 0
    try:
        for i in range(n):
            t_next = time.perf_counter() + period
            f, rtt = client.request(msp.MSP_API_VERSION, timeout)
            if f is None:
                lost += 1
            else:
                rtts.append(rtt * 1000)
            if not quiet and (i + 1) % max(1, n // 10) == 0:
                print(f"  {i + 1}/{n}", flush=True)
            time.sleep(max(0.0, t_next - time.perf_counter()))
    except KeyboardInterrupt:
        pass
    return rtts, lost


def print_stats(rtts, lost, bad):
    sent = len(rtts) + lost
    print(
        f"\nRequests: {sent}   answers: {len(rtts)}   lost: {lost} ({100 * lost / max(1, sent):.1f} %)"
    )
    if rtts:
        s = sorted(rtts)
        print(
            f"RTT ms   min {s[0]:.1f}   mean {statistics.mean(s):.1f}   "
            f"p95 {s[int(0.95 * (len(s) - 1))]:.1f}   max {s[-1]:.1f}"
        )
    print(f"Bad checksums: {bad}")


def save_csv(path, rtts):
    if path:
        with open(path, "w") as fh:
            fh.write("rtt_ms\n" + "\n".join(f"{r:.3f}" for r in rtts) + "\n")
        print(f"RTTs saved to {path}")


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


def cmd_ping(args):
    c, label = make_client(args)
    print(f"Target: {label}, {args.n} requests at {args.rate or 'max'} Hz")
    try:
        rtts, lost = run_ping(c, args.n, args.rate, args.timeout, args.quiet)
    finally:
        c.close()
    print_stats(rtts, lost, c.parser.bad_checksums)
    save_csv(args.csv, rtts)


def cmd_attitude(args):
    c, label = make_client(args)
    print(f"Target: {label}. Ctrl+C to stop. Tilt the drone by hand (props off).")
    try:
        while True:
            f, rtt = c.request(msp.MSP_ATTITUDE, args.timeout)
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


def fake_fc(port: str, stop: threading.Event, stats: dict, trace: bool = False):
    """Answer MSP requests on the ESP's USB serial port, like a Betaflight FC would."""
    ser = esp_at.open_port(port)
    ser.timeout = 0.005
    parser = msp.Parser()
    try:
        while not stop.is_set():
            for f in parser.feed(ser.read(512)):
                if f.direction != "<":
                    continue
                response = msp.encode_response(
                    f.cmd,
                    FAKE_ANSWERS.get(f.cmd, b""),
                    error=f.cmd not in FAKE_ANSWERS,
                )
                if trace:
                    print(
                        f"ESP/FC received: {msp.encode_request(f.cmd, f.payload).hex(' ')}",
                        flush=True,
                    )
                    print(f"ESP/FC sent:      {response.hex(' ')}", flush=True)
                ser.write(response)
                stats["answered"] += 1
            stats["bad"] = parser.bad_checksums
    finally:
        stats["bad"] = parser.bad_checksums
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


def cmd_cycle1a(args):
    """ESP on USB in bridge mode. This PC plays the FC on the ESP USB port and pings through Wi-Fi."""
    if not cfg.load_profile():
        sys.exit(
            "No scenario configured. Run setup_test.py (or setup_production.py) first."
        )
    port = args.esp_port_usb or esp_at.find_port()
    stop, stats = threading.Event(), {"answered": 0, "bad": 0}
    th = threading.Thread(
        target=fake_fc, args=(port, stop, stats, args.trace), daemon=True
    )
    th.start()
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
        time.sleep(0.5)
        bad_before = (
            c.parser.bad_checksums + stats["bad"]
        )  # boot noise from the ESP does not count
        rtts, lost = run_ping(c, args.n, args.rate, args.timeout, args.quiet)
    finally:
        c.close()
        stop.set()
        th.join(2)
    bad = c.parser.bad_checksums + stats["bad"] - bad_before
    print_stats(rtts, lost, bad)
    save_csv(args.csv, rtts)
    loss = 100 * lost / max(1, len(rtts) + lost)
    ok = loss <= args.max_loss and bad == 0
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
        p.add_argument("--quiet", action="store_true")
        p.add_argument(
            "--trace",
            action="store_true",
            help="print raw MSP frames sent and received",
        )
        if name == "cycle1a":
            p.add_argument(
                "--max-loss", type=float, default=1.0, help="pass threshold in %%"
            )
            p.add_argument(
                "--wait", type=float, default=20, help="seconds to wait for the bridge"
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
    args = ap.parse_args()
    {
        "info": cmd_info,
        "core": cmd_core,
        "ping": cmd_ping,
        "attitude": cmd_attitude,
        "fake-fc": cmd_fake_fc,
        "cycle1a": cmd_cycle1a,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
