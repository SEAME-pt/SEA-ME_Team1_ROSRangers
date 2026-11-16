#!/usr/bin/env python3
"""Real-time profile (REQ-SW-005): Betaflight `tasks` and the ESP loop() jitter, plotted.

Two logs go in, the plots come out (no hand-made plots):
- Betaflight `tasks`: the `# ===== tasks` block of a cli_read file written by
  `fc_cli.py dump` (FC on USB, props off), or a file with only the `tasks` output.
- ESP loop periods: the histogram the gateway built with env esp_loop_timing sends
  once a second to UDP 5770. Record it during a UDP control stream:
    python3 scripts/rt_profile.py listen --csv esp_loop.csv   # Ctrl+C to stop
    python3 scripts/ground_link.py ...                        # the stream, as usual
Then:
  python3 scripts/rt_profile.py plot --tasks drone/config/betaflight/<ver>/cli_read_<date>.txt \
      --esp results/esp_loop.csv --out "docs/PraticalPlan/03 Architecture/rt-profile"
"""

import argparse
import csv
import pathlib
import re
import socket
import time

from esp_timing import resolve_csv_path

PORT = 5770
FIELDS = ("pc_time_s", "window", "now_us", "loops", "max_us", "lo_us", "count")
TASK_ROW = re.compile(r"^\s*\d+\s+-\s+\(\s*([^)]+?)\s*\)\s+(.*)$")


def parse_tasks(text):
    """Rows of the Betaflight `tasks` table as dicts: name, rate_hz, max_us, avg_us,
    maxload, avgload (%). Columns are found by the header names."""
    header, rows = None, []
    for line in text.splitlines():
        if line.startswith("Task list"):
            header = line.split()[2:]
            continue
        m = TASK_ROW.match(line)
        if not header or not m:
            continue
        values = dict(zip(header, m.group(2).split()))
        rows.append(
            {
                "name": m.group(1),
                "rate_hz": int(values["rate/hz"]),
                "max_us": int(values["max/us"]),
                "avg_us": int(values["avg/us"]),
                "maxload": float(values["maxload"].rstrip("%")),
                "avgload": float(values["avgload"].rstrip("%")),
            }
        )
    return rows


def parse_packet(data, pc_time_s):
    """One UDP packet: an L line, then B lines. Returns CSV rows (one per bucket)."""
    head, rows = None, []
    for line in data.decode("ascii", "replace").splitlines():
        parts = line.split(",")
        try:
            values = [int(v) for v in parts[1:]]
        except ValueError:
            continue
        if parts[0] == "L" and len(values) == 4:
            head = dict(zip(FIELDS[:5], (pc_time_s, *values)))
        elif parts[0] == "B" and len(values) == 2 and head:
            rows.append({**head, "lo_us": values[0], "count": values[1]})
    return rows


def merge(rows):
    """Total count per bucket lower edge, and the exact max over all windows."""
    counts = {}
    for r in rows:
        counts[r["lo_us"]] = counts.get(r["lo_us"], 0) + r["count"]
    return dict(sorted(counts.items())), max((r["max_us"] for r in rows), default=0)


def percentile(counts, q):
    """Lower edge of the bucket holding the q-quantile (buckets are < 6.25 % wide)."""
    total = sum(counts.values())
    seen = 0
    for lo, n in counts.items():
        seen += n
        if seen >= q * total:
            return lo
    return None


def esp_stats(rows):
    counts, max_us = merge(rows)
    windows = {r["window"] for r in rows}
    return {
        "loops": sum(counts.values()),
        "windows": len(windows),
        "median_us": percentile(counts, 0.5),
        "p99_us": percentile(counts, 0.99),
        "max_us": max_us,
    }


def read_csv(path):
    with open(path, newline="") as fh:
        return [
            {k: (float(v) if k == "pc_time_s" else int(v)) for k, v in row.items()}
            for row in csv.DictReader(fh)
        ]


def plot_tasks(tasks, out):
    import matplotlib.pyplot as plt

    names = [t["name"] for t in tasks]
    fig, (load, tmax) = plt.subplots(1, 2, figsize=(12, 0.3 * len(tasks) + 1.5))
    y = range(len(tasks))
    load.barh([i + 0.2 for i in y], [t["maxload"] for t in tasks], 0.4, label="max")
    load.barh([i - 0.2 for i in y], [t["avgload"] for t in tasks], 0.4, label="avg")
    load.set(yticks=list(y), yticklabels=names, xlabel="CPU load (%)")
    load.invert_yaxis()
    load.legend()
    tmax.barh(list(y), [t["max_us"] for t in tasks], color="tab:red")
    tmax.set(
        yticks=list(y), yticklabels=[], xlabel="max time per run (us)", xscale="log"
    )
    tmax.invert_yaxis()
    fig.suptitle("Betaflight tasks")
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    plt.close(fig)


def plot_esp(rows, out):
    import matplotlib.pyplot as plt

    counts, _ = merge(rows)
    s = esp_stats(rows)
    widths = [max(1, lo // 16) for lo in counts]  # the firmware's bucket width
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.bar(list(counts), list(counts.values()), widths, align="edge")
    for key, color in (
        ("median_us", "tab:green"),
        ("p99_us", "tab:orange"),
        ("max_us", "tab:red"),
    ):
        ax.axvline(s[key], color=color, ls="--", label=f"{key[:-3]} {s[key]} us")
    ax.set(xscale="log", yscale="log", xlabel="loop() period (us)", ylabel="loops")
    ax.set_title(f"ESP gateway loop() period, {s['loops']} loops, {s['windows']} s")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    plt.close(fig)


def cmd_listen(args):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", args.port))
    sock.settimeout(0.5)
    rows, start = [], time.monotonic()
    print(
        f"Listening on UDP {args.port}. Start the control stream now; Ctrl+C when it ends."
    )
    try:
        while args.duration is None or time.monotonic() - start < args.duration:
            try:
                data, _ = sock.recvfrom(4096)
            except socket.timeout:
                continue
            rows += parse_packet(data, round(time.monotonic() - start, 3))
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
    print(
        f"\nSaved {out}: {esp_stats(rows) if rows else 'no data (env esp_loop_timing on the ESP?)'}"
    )


def cmd_plot(args):
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.tasks:
        tasks = parse_tasks(pathlib.Path(args.tasks).read_text())
        if not tasks:
            raise SystemExit(f"No `tasks` table in {args.tasks}")
        plot_tasks(tasks, out / "betaflight_tasks.png")
        print(
            "| Task | rate/Hz | max/us | avg/us | max load | avg load |\n|---|---|---|---|---|---|"
        )
        for t in tasks:
            print(
                f"| {t['name']} | {t['rate_hz']} | {t['max_us']} | {t['avg_us']} | {t['maxload']} % | {t['avgload']} % |"
            )
    if args.esp:
        rows = read_csv(resolve_csv_path(args.esp))
        if not rows:
            raise SystemExit(f"No ESP loop records in {args.esp}")
        plot_esp(rows, out / "esp_loop_period.png")
        print(esp_stats(rows))
    print(f"Plots in {out}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("listen", help="record the ESP loop histogram (UDP 5770)")
    p.add_argument("--csv", required=True, help="output CSV (bare name: results/)")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--duration", type=float, help="stop after this many seconds")
    p.set_defaults(func=cmd_listen)
    p = sub.add_parser("plot", help="plot the logs to PNG")
    p.add_argument("--tasks", help="Betaflight cli_read / tasks log")
    p.add_argument("--esp", help="CSV written by listen")
    p.add_argument("--out", default="results/rt-profile", help="folder for the PNGs")
    p.set_defaults(func=cmd_plot)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
