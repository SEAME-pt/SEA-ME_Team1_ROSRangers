#!/usr/bin/env python3
"""ESP timestamps per request, from the esp_diag_timing firmware (UDP 5761).

For each request the ESP records four micros() stamps: esp_in (UDP packet read),
uart_out (written to the FC), uart_in (first reply byte from the FC) and esp_out
(reply handed to the Wi-Fi stack). This splits a slow or lost reply into:
Wi-Fi PC -> ESP, inside the ESP, the FC, and Wi-Fi ESP -> PC.

Run it in a second terminal, start it before the test and stop it (Ctrl+C) after:
  python3 scripts/esp_timing.py listen --csv s1-09_run1_esp.csv
  python3 scripts/msp_check.py ...                      # the test itself, as usual
  python3 scripts/esp_timing.py summary results/s1-09_run1_esp.csv --sent 30000 --pc-lost 12
"""

import argparse
import csv
import pathlib
import socket
import statistics
import time

PORT = 5761
RESULTS_DIR = pathlib.Path(__file__).resolve().parents[1] / "results"
STAMP_FIELDS = ("pc_time_s", "seq", "esp_in", "uart_out", "uart_in", "esp_out", "sent")
REPORT_FIELDS = ("pc_time_s", "report", "now_us", "loop_max_us", "dropped")
WRAP = 1 << 32  # micros() wraps after about 71 min


def elapsed_ms(start_us, end_us):
    return ((end_us - start_us) % WRAP) / 1000


def parse_packet(data, pc_time_s):
    """One UDP packet: an R line, then Q lines. Returns (report, stamps)."""
    report, stamps = None, []
    for line in data.decode("ascii", "replace").splitlines():
        parts = line.split(",")
        try:
            values = [int(v) for v in parts[1:]]
        except ValueError:
            continue
        if parts[0] == "R" and len(values) == 4:
            report = dict(zip(REPORT_FIELDS, (pc_time_s, *values)))
        elif parts[0] == "Q" and len(values) == 6:
            stamps.append(dict(zip(STAMP_FIELDS, (pc_time_s, *values))))
    return report, stamps


def stats(values, threshold_ms):
    if not values:
        return "no data"
    values = sorted(values)
    p99 = values[min(len(values) - 1, int(len(values) * 0.99))]
    over = sum(v >= threshold_ms for v in values)
    return (
        f"median {statistics.median(values):7.2f}  p99 {p99:7.2f}  "
        f"max {values[-1]:7.2f} ms  ({over} >= {threshold_ms:g} ms)"
    )


def summarize(stamps, reports, sent=None, pc_lost=None, threshold_ms=300, top=5):
    """Return the summary as text lines (also used by the tests)."""
    by_seq = {s["seq"]: s for s in stamps}
    seqs = sorted(by_seq)
    if not seqs:
        return ["No ESP records. Is the esp_diag_timing firmware on the ESP?"]
    rows = [by_seq[q] for q in seqs]
    span = seqs[-1] - seqs[0] + 1
    answered = [r for r in rows if r["uart_in"]]
    no_reply = len(rows) - len(answered)
    send_fail = sum(not r["sent"] for r in answered)

    lines = [
        f"ESP requests {span} (seq {seqs[0]}..{seqs[-1]}), records received {len(rows)}"
        f" ({span - len(rows)} records lost on the way to this PC, not requests)",
        f"FC answered {len(answered)}, no FC reply {no_reply}, ESP send failed {send_fail}",
        "",
        "Time per stage (ms):",
        "  esp_in -> uart_out   (ESP, before the FC) "
        + stats([elapsed_ms(r["esp_in"], r["uart_out"]) for r in rows], threshold_ms),
        "  uart_out -> uart_in  (FC + UART)          "
        + stats(
            [elapsed_ms(r["uart_out"], r["uart_in"]) for r in answered], threshold_ms
        ),
        "  uart_in -> esp_out   (ESP, after the FC)  "
        + stats(
            [elapsed_ms(r["uart_in"], r["esp_out"]) for r in answered], threshold_ms
        ),
        "  esp_in -> esp_out    (whole ESP + FC)     "
        + stats(
            [elapsed_ms(r["esp_in"], r["esp_out"]) for r in answered], threshold_ms
        ),
    ]

    gaps = [
        (elapsed_ms(a["esp_in"], b["esp_in"]), b)
        for a, b in zip(rows, rows[1:])
        if b["seq"] == a["seq"] + 1
    ]
    lines += [
        "",
        "Silence between requests arriving at the ESP (Wi-Fi PC -> ESP):",
        "  " + stats([g for g, _ in gaps], threshold_ms),
    ]
    t0 = rows[0]["esp_in"]
    for gap, row in sorted(gaps, key=lambda g: -g[0])[:top]:
        lines.append(
            f"    {gap:7.1f} ms before seq {row['seq']} "
            f"(at {elapsed_ms(t0, row['esp_in']) / 1000:7.2f} s)"
        )

    if reports:
        reports = sorted(reports, key=lambda r: r["report"])
        loop_max = [r["loop_max_us"] / 1000 for r in reports]
        lost_reports = reports[-1]["report"] - reports[0]["report"] + 1 - len(reports)
        lines += [
            "",
            f"ESP loop stall (longest per 100 ms): {stats(loop_max, threshold_ms)}",
            f"Reports lost {lost_reports}, records dropped on the ESP {reports[-1]['dropped']}",
        ]

    if sent is not None:
        before = sent - span
        lines += ["", f"Lost before the ESP (Wi-Fi PC -> ESP): {before} of {sent}"]
        if pc_lost is not None:
            after = pc_lost - before - no_reply - send_fail
            lines.append(
                f"Lost after the ESP (Wi-Fi ESP -> PC): {after} "
                f"(PC lost {pc_lost} = {before} + FC {no_reply} + ESP {send_fail} + {after})"
            )
    return lines


def resolve_csv_path(path):
    csv_path = pathlib.Path(path)
    if not csv_path.is_absolute() and csv_path.parent == pathlib.Path("."):
        csv_path = RESULTS_DIR / csv_path
    return csv_path


def reports_path(csv_path):
    return csv_path.with_name(f"{csv_path.stem}_reports{csv_path.suffix or '.csv'}")


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path):
    if not path.exists():
        return []
    with path.open(newline="") as fh:
        return [
            {k: (float(v) if k == "pc_time_s" else int(v)) for k, v in row.items()}
            for row in csv.DictReader(fh)
        ]


def cmd_listen(args):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", args.port))
    sock.settimeout(0.5)
    stamps, reports = [], []
    start = time.monotonic()
    print(f"Listening on UDP {args.port}. Start the test now; Ctrl+C when it ends.")
    try:
        while args.duration is None or time.monotonic() - start < args.duration:
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                continue
            report, new = parse_packet(data, round(time.monotonic() - start, 4))
            if report:
                reports.append(report)
            stamps += new
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
    print()
    if args.csv:
        out = resolve_csv_path(args.csv)
        write_csv(out, stamps, STAMP_FIELDS)
        write_csv(reports_path(out), reports, REPORT_FIELDS)
        print(f"Saved {out} and {reports_path(out).name}")
    print(
        "\n".join(summarize(stamps, reports, args.sent, args.pc_lost, args.threshold))
    )


def cmd_summary(args):
    path = resolve_csv_path(args.csv)
    stamps, reports = read_csv(path), read_csv(reports_path(path))
    print(
        "\n".join(summarize(stamps, reports, args.sent, args.pc_lost, args.threshold))
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, func in (("listen", cmd_listen), ("summary", cmd_summary)):
        p = sub.add_parser(name)
        p.set_defaults(func=func)
        if name == "listen":
            p.add_argument(
                "--csv", help="save the records to this CSV (bare name: results/)"
            )
            p.add_argument("--port", type=int, default=PORT)
            p.add_argument(
                "--duration", type=float, help="stop after this many seconds"
            )
        else:
            p.add_argument("csv", help="CSV written by listen")
        p.add_argument(
            "--sent", type=int, help="requests the PC sent (splits the loss)"
        )
        p.add_argument("--pc-lost", type=int, help="requests the PC counted as lost")
        p.add_argument("--threshold", type=float, default=300, help="ms (default 300)")
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
