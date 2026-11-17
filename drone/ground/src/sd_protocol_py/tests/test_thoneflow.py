"""Tests for sd_protocol.thoneflow (docs/Communication/ThoneFlow.md §9.1).

The replay tests use bench captures of 2026-11-16 (#107) in data/thoneflow:
one line per frame with the raw bytes and the values decoded on the PC at
capture time."""

import csv
import random
from pathlib import Path

import pytest

from sd_protocol.thoneflow import (
    FRAME_SIZE,
    QUALITY_MIN_FLOOR,
    Status,
    ThoneflowParser,
    checksum,
    decode_thoneflow_frame,
)

DATA = Path(__file__).parent / "data" / "thoneflow"


def make_frame(x: int, y: int, q: int) -> bytes:
    f = bytearray([0xFE, 0x04])
    f += x.to_bytes(2, "little", signed=True)
    f += y.to_bytes(2, "little", signed=True)
    f += bytes([0, q, 0xAA])
    f[6] = checksum(f)
    return bytes(f)


def parser(**kw) -> ThoneflowParser:
    return ThoneflowParser(quality_min=kw.get("quality_min", 100), stale_s=0.05)


# --- valid frames, byte order --------------------------------------------


def test_real_bench_frames_decode():
    # Captured 2026-11-10: 01 00 = +1, fe ff = -2 (low byte first)
    assert (
        decode_thoneflow_frame(bytes.fromhex("fe040100feff00a0aa")) is None
    )  # bad sum
    f = bytes.fromhex("fe040100feff") + bytes([(1 + 0 + 0xFE + 0xFF) & 0xFF])
    f += bytes([0xA0, 0xAA])
    assert decode_thoneflow_frame(f) == (1, -2, 0xA0)


def test_decode_refuses_short_or_wrong_header():
    f = make_frame(1, 2, 150)
    assert decode_thoneflow_frame(f[:8]) is None
    assert decode_thoneflow_frame(b"\x00" + f[1:]) is None


def test_latest_returns_the_fresh_frame():
    p = parser()
    (m,) = p.feed(make_frame(4, 5, 150), 1.0)
    assert p.latest(1.01) == m


@pytest.mark.parametrize("x,y", [(0, 0), (1, -1), (32767, -32768), (-300, 1079)])
def test_round_trip(x, y):
    assert decode_thoneflow_frame(make_frame(x, y, 150)) == (x, y, 150)


def test_status_ok_and_low_quality():
    p = parser(quality_min=100)
    (a,) = p.feed(make_frame(5, 6, 160), 1.0)
    (b,) = p.feed(make_frame(5, 6, 60), 1.01)
    assert (a.status, b.status) == (Status.OK, Status.LOW_QUALITY)
    assert (a.seq, b.seq) == (1, 2)
    assert b.t == 1.01


# --- bad frames ------------------------------------------------------------


@pytest.mark.parametrize(
    "index,value,counter",
    [(1, 0x05, "bad_length"), (8, 0xAB, "bad_footer"), (6, 0x00, "bad_checksum")],
)
def test_bad_frame_is_dropped_and_counted(index, value, counter):
    bad = bytearray(make_frame(10, 20, 150))
    if bad[index] == value:
        value ^= 1
    bad[index] = value
    p = parser()
    out = p.feed(make_frame(5, 5, 150) + bytes(bad) + make_frame(1, 2, 150), 0.0)
    assert [(m.x, m.y) for m in out] == [(5, 5), (1, 2)]
    assert getattr(p.counters, counter) == 1
    assert p.counters.accepted == 2
    assert p.counters.resyncs == 1


def test_bad_header_where_a_frame_was_expected():
    p = parser()
    out = p.feed(make_frame(1, 1, 150) + b"\x00\x13" + make_frame(2, 2, 150), 0.0)
    assert [m.x for m in out] == [1, 2]
    c = p.counters
    assert (c.bad_header, c.skipped_bytes, c.resyncs) == (1, 2, 1)


def test_opening_mid_stream_counts_no_reject():
    # X = -2 is "fe ff": a reader that opens right at that byte sees a false
    # header first. Nothing went wrong on the line, so no reject is counted.
    f = make_frame(-2, 3, 150)
    p = parser()
    out = p.feed(f[2:] + f * 3, 0.0)
    assert len(out) == 3
    c = p.counters
    assert (c.bad_header, c.bad_length, c.bad_footer, c.bad_checksum) == (0, 0, 0, 0)
    assert c.resyncs == 0 and c.skipped_bytes == 7


# --- stream handling (PAR-01) ----------------------------------------------


def test_frame_split_across_reads():
    f = make_frame(-7, 8, 150)
    p = parser()
    assert p.feed(f[:4], 0.0) == []
    (m,) = p.feed(f[4:], 0.01)
    assert (m.x, m.y, m.t) == (-7, 8, 0.01)


def test_several_frames_in_one_read():
    data = b"".join(make_frame(i, -i, 150) for i in range(5))
    assert [m.x for m in parser().feed(data, 0.0)] == [0, 1, 2, 3, 4]


def test_leading_garbage_and_truncated_frame():
    p = parser()
    out = p.feed(b"\x00\x13\xaa" + make_frame(3, 4, 150) + make_frame(9, 9, 150)[:5], 0)
    assert [(m.x, m.y) for m in out] == [(3, 4)]
    assert p.counters.skipped_bytes == 3
    assert p.counters.resyncs == 0  # the first sync is not a resync
    assert p.counters.bad_header == 0


# --- false headers and resync (PAR-03, PAR-04) -------------------------------


def test_false_headers_inside_the_data_do_not_misalign():
    # X = 0xAAFE (LE: fe aa) and quality 0xFE put header/footer bytes inside
    tricky = make_frame(int.from_bytes(b"\xfe\xaa", "little", signed=True), 0xFE, 0xFE)
    p = parser()
    out = p.feed(tricky * 3, 0.0)
    assert len(out) == 3 and p.counters.skipped_bytes == 0


def test_resync_after_corruption_finds_a_frame_hidden_after_a_header():
    # A cut frame (fe 04 01) right before a good one: the parser must step one
    # byte at a time, not skip 9, or it loses the good frame.
    p = parser()
    good = make_frame(11, 12, 150)
    out = p.feed(make_frame(1, 1, 150) + b"\xfe\x04\x01" + good, 0.0)
    assert [(m.x, m.y) for m in out] == [(1, 1), (11, 12)]
    assert p.counters.resyncs == 1


def test_garbage_between_frames_loses_no_valid_frame():
    rng = random.Random(1)
    frames = [
        make_frame(rng.randint(-500, 500), rng.randint(-500, 500), 150)
        for _ in range(300)
    ]
    stream = bytearray()
    for f in frames:
        stream += bytes(rng.randrange(0, 0xFE) for _ in range(rng.randint(0, 6)))
        stream += f
    p = parser()
    out = []
    i = 0
    while i < len(stream):
        n = rng.randint(1, 20)
        out += p.feed(bytes(stream[i : i + n]), i / 1000)
        i += n
    assert [(m.x, m.y) for m in out] == [decode_thoneflow_frame(f)[:2] for f in frames]


def test_fuzz_never_crashes_and_buffer_stays_small():
    rng = random.Random(2)
    p = parser()
    for _ in range(2000):
        p.feed(bytes(rng.randrange(256) for _ in range(rng.randint(0, 40))), 0.0)
        assert len(p._buf) < FRAME_SIZE


# --- status logic (PAR-08, §8) ----------------------------------------------


def test_no_data_before_any_byte_and_after_silence():
    p = parser()
    assert p.status(0.0) is Status.NO_DATA
    p.feed(make_frame(1, 1, 150), 1.0)
    assert p.status(1.04) is Status.OK
    assert p.status(1.06) is Status.NO_DATA
    assert p.latest(1.06) is None  # never a stale value as current, never zero


def test_stale_when_bytes_come_but_no_valid_frame():
    p = parser()
    p.feed(make_frame(1, 1, 150), 1.0)
    p.feed(b"\x00" * 9, 1.04)
    p.feed(b"\x01" * 9, 1.08)
    assert p.status(1.08) is Status.STALE
    assert p.latest(1.08) is None


def test_covered_lens_is_low_quality_not_zero_motion():
    # Lens covered on 2026-11-16: X = Y = 0, quality 5-17
    p = parser(quality_min=100)
    (m,) = p.feed(make_frame(0, 0, 11), 0.0)
    assert m.status is Status.LOW_QUALITY
    assert p.status(0.0) is Status.LOW_QUALITY


def test_reset_forgets_values_but_keeps_counters_and_seq():
    p = parser()
    p.feed(b"\x00" + make_frame(1, 1, 150), 0.0)
    p.reset()
    assert p.status(0.0) is Status.NO_DATA
    assert p.counters.accepted == 1 and p.counters.skipped_bytes == 1
    (m,) = p.feed(b"\x00" + make_frame(2, 2, 150), 1.0)
    assert m.seq == 2  # never repeated
    assert p.counters.resyncs == 0  # a restart is a new first sync


def test_stale_events_counted_once_per_loss():
    p = parser()  # stale_s 0.05
    p.feed(make_frame(1, 1, 150), 1.0)
    assert p.status(1.04) is Status.OK
    assert p.status(1.10) is Status.NO_DATA
    assert p.status(1.20) is Status.NO_DATA
    assert p.counters.stale_events == 1
    p.feed(make_frame(1, 1, 150), 1.30)  # data back: not a new event
    assert p.counters.stale_events == 1
    p.feed(make_frame(1, 1, 150), 1.50)  # a gap nobody asked status() about
    assert p.counters.stale_events == 2


def test_no_stale_event_before_the_first_frame():
    p = parser()
    assert p.status(5.0) is Status.NO_DATA
    assert p.counters.stale_events == 0


@pytest.mark.parametrize(
    "q,s", [(0, 0.05), (QUALITY_MIN_FLOOR - 1, 0.05), (256, 0.05), (100, 0), (100, -1)]
)
def test_bad_settings_refused(q, s):
    with pytest.raises(ValueError):
        ThoneflowParser(quality_min=q, stale_s=s)


# --- replay of the bench captures (§9.1 "Replay", §2.4) ---------------------

CAPTURES = sorted(DATA.glob("*.csv"))


def load(path: Path) -> tuple[bytes, list[tuple[int, int, int]]]:
    with path.open() as fh:
        rows = [r for r in csv.reader(fh) if r and not r[0].startswith("#")][1:]
    raw = b"".join(bytes.fromhex(r[6]) for r in rows)
    want = [(int(r[2]), int(r[3]), int(r[4])) for r in rows if r[5] == "1"]
    return raw, want


def replay(path: Path, quality_min: int = 100):
    raw, _ = load(path)
    p = ThoneflowParser(quality_min=quality_min, stale_s=0.05)
    rng = random.Random(path.name)
    got = []
    i = 0
    while i < len(raw):
        n = rng.randint(1, 30)  # reads never aligned with frames
        got += p.feed(raw[i : i + n], 0.0)
        i += n
    return got


def test_captures_are_there():
    assert len(CAPTURES) == 4


@pytest.mark.parametrize("path", CAPTURES, ids=lambda p: p.name)
def test_replay_matches_the_capture(path):
    _, want = load(path)
    assert [(m.x, m.y, m.quality) for m in replay(path)] == want


def test_carpet_forward_move_is_seen_as_plus_y():
    out = replay(DATA / "forward_carpet_15cm.csv")
    assert sum(m.y for m in out) == 1015  # 30 cm at 15 cm, ~34 counts/cm
    assert abs(sum(m.x for m in out)) < 50
    assert all(m.status is Status.OK for m in out)


@pytest.mark.parametrize("q", [QUALITY_MIN_FLOOR, 100])
def test_covered_lens_is_all_low_quality_and_zero(q):
    # Even at the lowest quality_min allowed, "no image" is never "not moving"
    out = replay(DATA / "lens_covered.csv", quality_min=q)
    assert {(m.x, m.y) for m in out} == {(0, 0)}
    assert all(m.status is Status.LOW_QUALITY for m in out)


def test_white_table_move_is_not_seen():
    out = replay(DATA / "white_table_15cm.csv")
    assert abs(sum(m.y for m in out)) < 50
    assert all(m.status is Status.LOW_QUALITY for m in out)


def test_quality_alone_does_not_catch_the_black_mat():
    # Recorded hazard (ThoneFlow.md §2.4, TF-06): on the mat the motion is
    # reversed and ~4x too large, yet with a threshold that passes the carpet
    # at 30 cm (~125) a good part of the frames are still OK.
    out = replay(DATA / "black_mat_15cm.csv", quality_min=100)
    assert sum(m.y for m in out) < -3000
    assert sum(m.status is Status.OK for m in out) > len(out) // 2
