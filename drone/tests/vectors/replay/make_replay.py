"""Builds the late-check replay fixtures (#204) from the ESP-S1-09 cage runs.

Input: results/s1-09_cage_run{1,2,3}_esp.csv (2026-10-23, git-ignored bench
logs: seq and esp_in, the ESP's micros() when each 50 Hz request arrived).
Output: s1-09_cage_run{1,2,3}.csv here, one frame per line: dt_send_us,drx_us,
the steps from the previous frame (the first line: from 0), so the files stay
small. Summed in u32 they give t_send_us and rx_us (the ESP clock).

These runs have no real t_send_us, so t_send_us is the nominal 50 Hz slot
(seq x 20 ms), corrected for the PC sender's slot slips: the lowest lag in a
±30 s window steps by whole 20 ms slots when the sender slipped, and only
those whole-slot steps are added back. Clock drift and Wi-Fi delay stay in
the data. The S1-09 re-run with the real t_send_us replaces this (#204).

Run from the repository root: python3 drone/tests/vectors/replay/make_replay.py
"""

import csv
import pathlib

SLOT_US = 20_000
WINDOW = 1500  # frames each side, 30 s at 50 Hz
HERE = pathlib.Path(__file__).resolve().parent
RESULTS = HERE.parents[3] / "results"


def build(src: pathlib.Path, dst: pathlib.Path) -> int:
    rows = [(int(r["seq"]), int(r["esp_in"])) for r in csv.DictReader(src.open())]
    rx, wrap, prev = [], 0, None
    for _, e in rows:
        if prev is not None and e < prev - 2**31:
            wrap += 2**32
        prev = e
        rx.append(e + wrap)
    seq0, rx0 = rows[0][0], rx[0]
    lag = [(r - rx0) - (s - seq0) * SLOT_US for (s, _), r in zip(rows, rx)]
    n = len(lag)
    floor0 = min(lag[:WINDOW])
    lines, prev_t, prev_r = [], 0, 0
    for i, ((s, _), r) in enumerate(zip(rows, rx)):
        floor = min(lag[max(0, i - WINDOW) : i + WINDOW])
        slips = round((floor - floor0) / SLOT_US)
        t_send = (s - seq0) * SLOT_US + slips * SLOT_US
        lines.append(f"{(t_send - prev_t) & 0xFFFFFFFF},{(r - prev_r) & 0xFFFFFFFF}")
        prev_t, prev_r = t_send, r
    dst.write_text("dt_send_us,drx_us\n" + "\n".join(lines) + "\n")
    return n


def main() -> None:
    for run in (1, 2, 3):
        src = RESULTS / f"s1-09_cage_run{run}_esp.csv"
        dst = HERE / f"s1-09_cage_run{run}.csv"
        print(f"{dst.name}: {build(src, dst)} frames")


if __name__ == "__main__":
    main()
