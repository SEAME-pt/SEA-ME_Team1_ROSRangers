#!/usr/bin/env python3
"""Test the MSP path to the flight controller, directly over USB or through the ESP bridge.

Only read-only MSP requests are sent. Run it with the props OFF.

Examples:
  # Baseline: straight to the FC over USB
  python3 scripts/bridge_test.py --serial /dev/ttyACM0 info
  python3 scripts/bridge_test.py --serial /dev/ttyACM0 ping -n 200

  # Through the ESP transparent bridge (PC on the ESP SoftAP with IP 192.168.4.2)
  python3 scripts/bridge_test.py --udp 192.168.4.1:5760 info
  python3 scripts/bridge_test.py --udp 192.168.4.1:5760 ping -n 500 --rate 50
  python3 scripts/bridge_test.py --udp 192.168.4.1:5760 attitude

  # Cycle 1a, no drone: the ESP stays on USB and this PC plays the FC on its serial port
  python3 scripts/bridge_test.py fake-fc --serial /dev/ttyUSB0      # terminal 1
  python3 scripts/bridge_test.py --udp 192.168.4.1:5760 ping       # terminal 2
"""

import argparse
import statistics
import struct
import sys
import time

import msp


def make_client(args):
    if args.serial:
        return msp.Client(msp.SerialTransport(args.serial, args.baud))
    if args.udp:
        host, port = args.udp.rsplit(":", 1)
        return msp.Client(msp.UdpTransport(host, int(port), args.local_port))
    sys.exit("Choose --serial PORT or --udp HOST:PORT")


def cmd_info(args):
    c = make_client(args)
    try:
        for name, cmd in [("API version", msp.MSP_API_VERSION), ("FC variant", msp.MSP_FC_VARIANT),
                          ("FC version", msp.MSP_FC_VERSION), ("Board", msp.MSP_BOARD_INFO),
                          ("Status", msp.MSP_STATUS), ("Attitude", msp.MSP_ATTITUDE),
                          ("Analog", msp.MSP_ANALOG)]:
            f, rtt = c.request(cmd, args.timeout)
            if f is None:
                print(f"{name:12} no answer within {args.timeout * 1000:.0f} ms")
            elif f.direction == "!":
                print(f"{name:12} FC returned an MSP error")
            else:
                print(f"{name:12} {msp.decode(cmd, f.payload)}  ({rtt * 1000:.1f} ms)")
        if c.parser.bad_checksums:
            print(f"Bad checksums: {c.parser.bad_checksums}")
    finally:
        c.close()


def cmd_ping(args):
    c = make_client(args)
    rtts, lost = [], 0
    period = 1.0 / args.rate if args.rate else 0
    try:
        for i in range(args.n):
            t_next = time.perf_counter() + period
            f, rtt = c.request(msp.MSP_API_VERSION, args.timeout)
            if f is None:
                lost += 1
            else:
                rtts.append(rtt * 1000)
            if not args.quiet and (i + 1) % max(1, args.n // 10) == 0:
                print(f"  {i + 1}/{args.n}")
            time.sleep(max(0.0, t_next - time.perf_counter()))
    except KeyboardInterrupt:
        pass
    finally:
        c.close()
    sent = len(rtts) + lost
    print(f"\nRequests: {sent}   answers: {len(rtts)}   lost: {lost} ({100 * lost / max(1, sent):.1f} %)")
    if rtts:
        rtts_sorted = sorted(rtts)
        p95 = rtts_sorted[int(0.95 * (len(rtts_sorted) - 1))]
        print(f"RTT ms   min {min(rtts):.1f}   mean {statistics.mean(rtts):.1f}   "
              f"p95 {p95:.1f}   max {max(rtts):.1f}")
    print(f"Bad checksums: {c.parser.bad_checksums}")
    if args.csv:
        with open(args.csv, "w") as fh:
            fh.write("rtt_ms\n" + "\n".join(f"{r:.3f}" for r in rtts) + "\n")
        print(f"RTTs saved to {args.csv}")


def cmd_attitude(args):
    c = make_client(args)
    print("Ctrl+C to stop. Tilt the drone by hand (props off).")
    try:
        while True:
            f, rtt = c.request(msp.MSP_ATTITUDE, args.timeout)
            if f is None:
                print("no answer", flush=True)
            else:
                a = msp.decode(msp.MSP_ATTITUDE, f.payload)
                print(f"roll {a['roll_deg']:7.1f}  pitch {a['pitch_deg']:7.1f}  "
                      f"yaw {a['yaw_deg']:4d}   ({rtt * 1000:.1f} ms)", flush=True)
            time.sleep(1.0 / args.rate)
    except KeyboardInterrupt:
        pass
    finally:
        c.close()


def cmd_fake_fc(args):
    """Answer MSP requests on a serial port, like a Betaflight FC would."""
    import serial
    answers = {
        msp.MSP_API_VERSION: bytes([0, 1, 46]),
        msp.MSP_FC_VARIANT: b"BTFL",
        msp.MSP_FC_VERSION: bytes([4, 5, 1]),
        msp.MSP_BOARD_INFO: b"FAKE",
        msp.MSP_STATUS: struct.pack("<HHHI", 125, 0, 0x23, 0) + bytes(5),
        msp.MSP_ATTITUDE: struct.pack("<hhh", 0, 0, 0),
        msp.MSP_ANALOG: bytes([0]),
    }
    if not args.serial:
        sys.exit("fake-fc needs --serial PORT (the ESP's USB port)")
    ser = serial.Serial()
    ser.port, ser.baudrate, ser.timeout = args.serial, args.baud, 0.01
    ser.dtr = ser.rts = False  # do not reset the ESP when the port opens
    ser.open()
    parser = msp.Parser()
    count = 0
    print(f"Fake FC on {args.serial} at {args.baud} baud. Ctrl+C to stop.")
    try:
        while True:
            for f in parser.feed(ser.read(512)):
                if f.direction != "<":
                    continue
                if f.cmd in answers:
                    ser.write(msp.encode_response(f.cmd, answers[f.cmd]))
                else:
                    ser.write(msp.encode_response(f.cmd, error=True))
                count += 1
                if count % 50 == 0:
                    print(f"  answered {count} requests", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        ser.close()
    print(f"Answered {count} requests, bad checksums {parser.bad_checksums}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serial", help="serial port (FC USB, or the ESP USB for fake-fc)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--udp", help="ESP address HOST:PORT, e.g. 192.168.4.1:5760")
    ap.add_argument("--local-port", type=int, default=5760, help="UDP port on this PC (default 5760)")
    ap.add_argument("--timeout", type=float, default=0.5, help="seconds to wait for each answer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info", help="read API version, firmware, board, status, attitude, battery")
    p = sub.add_parser("ping", help="measure round-trip time and loss with MSP_API_VERSION")
    p.add_argument("-n", type=int, default=100)
    p.add_argument("--rate", type=float, default=20, help="requests per second (0 = as fast as possible)")
    p.add_argument("--csv", help="save every RTT to this CSV file")
    p.add_argument("--quiet", action="store_true")
    p = sub.add_parser("attitude", help="print roll/pitch/yaw continuously")
    p.add_argument("--rate", type=float, default=10)
    sub.add_parser("fake-fc", help="answer MSP on --serial like a flight controller")
    args = ap.parse_args()
    {"info": cmd_info, "ping": cmd_ping, "attitude": cmd_attitude, "fake-fc": cmd_fake_fc}[args.cmd](args)


if __name__ == "__main__":
    main()
