"""Unit tests of drone/tools/bench/late_log.py (#219): the packet parser and the replay
of a logged run through the late check. No network, no ESP."""

import pathlib
import sys

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[3] / "drone" / "tools" / "bench")
)

import late_log  # noqa: E402


def record(seq, t_send_us, arrive_us, result=0):
    """A SETPOINT sent at t_send_us (ground clock) that reached the ESP at
    arrive_us (ESP clock, 5 s ahead). rx_us is the check's ms clock."""
    esp_us = 5_000_000 + arrive_us
    return {
        "pc_time_s": 0.0,
        "seq": seq,
        "t_send_us": t_send_us,
        "rx_us": esp_us // 1000 * 1000,
        "micros": esp_us,
        "result": result,
    }


def stream(n, delay_us=8_000, start_seq=1, start_us=0):
    return [
        record(start_seq + i, start_us + i * 20_000, start_us + i * 20_000 + delay_us)
        for i in range(n)
    ]


def test_parse_packet_reads_records_and_the_flash_chip():
    data = b"R,7,1000,5009000,5009123,0\nR,8,21000,5240000,5240456,17\nF,001640ef,239,4194304,40000000,3\nR,bad\n"
    records, chip = late_log.parse_packet(data, 1.5)
    assert [r["seq"] for r in records] == [7, 8]
    assert records[1]["result"] == 17 and records[1]["pc_time_s"] == 1.5
    assert chip["chip_id"] == "001640ef" and chip["speed_hz"] == "40000000"
    assert chip["dropped"] == "3"


def test_normal_traffic_is_never_refused():
    stats = late_log.analyze(stream(500))
    assert stats["frames"] == 500 and stats["max_ms"] == 0.0
    assert stats["silences_off"] == 0
    assert all(row["late"] == 0 and row["silences"] == 0 for row in stats["bounds"])


def test_a_frame_held_300_ms_is_refused_at_every_bound_below_it():
    records = stream(100)
    held = record(101, 100 * 20_000, 100 * 20_000 + 8_000 + 300_000)
    after = stream(50, start_seq=102, start_us=101 * 20_000 + 300_000)
    stats = late_log.analyze(records + [held] + after)
    by_bound = {row["bound_ms"]: row for row in stats["bounds"]}
    for bound in (100, 150, 200, 250):
        assert by_bound[bound]["late"] == 1, bound
    assert stats["max_ms"] == 300.0


def test_a_long_stall_counts_as_a_silence_with_or_without_the_check():
    # 400 ms of nothing, then the queued frames arrive late in a burst: the
    # silence is there either way; the check must not add another one.
    before = stream(50)
    gap_from = 50 * 20_000
    burst = [
        record(51 + i, gap_from + i * 20_000, gap_from + 400_000 + i * 1_000)
        for i in range(20)
    ]
    after = stream(50, start_seq=71, start_us=gap_from + 420_000)
    stats = late_log.analyze(before + burst + after)
    assert stats["silences_off"] == 1
    for row in stats["bounds"]:
        assert row["silences"] == stats["silences_off"], row


def test_report_shows_the_extra_failsafes_column():
    text = late_log.report(late_log.analyze(stream(50)))
    assert "| 200 ms | 0 | 0 | 0 |" in text
    assert "Extra failsafes" in text
