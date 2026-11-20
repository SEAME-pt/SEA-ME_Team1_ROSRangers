"""Unit tests of drone/tools/bench/late_log.py (#219): the packet parser and
the replay of a logged run through the late check. No network, no ESP."""

import pathlib
import sys

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[3] / "drone" / "tools" / "bench")
)

import late_log  # noqa: E402

SESSION = 0x1234
LATE = 17  # RxResult.LATE
CLEAN = {"dropped": 0, "lost_batches": 0, "restarts": 0}  # an info file with no losses
BAD_TAG = 7  # RxResult.BAD_TAG


def record(seq, t_send_us, arrive_us, result=0, session=SESSION, esp_start=5_000_000):
    """A SETPOINT sent at t_send_us (ground clock) that reached the ESP at
    arrive_us (ESP clock, esp_start ahead). rx_us is the check's ms clock;
    both ESP clocks are u32 and may wrap."""
    esp_us = esp_start + arrive_us
    return {
        "pc_time_s": 0.0,
        "session": session,
        "seq": seq,
        "t_send_us": t_send_us & 0xFFFFFFFF,
        "rx_us": (esp_us // 1000 * 1000) & 0xFFFFFFFF,
        "micros": esp_us & 0xFFFFFFFF,
        "result": result,
    }


def stream(n, delay_us=8_000, start_seq=1, start_us=0, **kw):
    return [
        record(
            start_seq + i, start_us + i * 20_000, start_us + i * 20_000 + delay_us, **kw
        )
        for i in range(n)
    ]


def by_bound(stats):
    return {row["bound_ms"]: row for row in stats["bounds"]}


def test_int32_reads_a_wrapped_difference_as_signed():
    assert late_log.int32(0x00000010 - 0xFFFFFFF0) == 32
    assert late_log.int32(0xFFFFFFF0 - 0x00000010) == -32


def test_parse_packet_reads_records_and_the_flash_chip():
    data = (
        b"B,41\nR,4660,7,1000,5009000,5009123,0\nR,4660,8,21000,5240000,5240456,17\n"
        b"F,001640ef,239,4194304,40000000,3\nR,bad\n"
    )
    records, chip, batch = late_log.parse_packet(data, 1.5)
    assert batch == 41
    assert [r["seq"] for r in records] == [7, 8]
    assert records[0]["session"] == 4660 and records[1]["result"] == 17
    assert chip["chip_id"] == "001640ef" and chip["bus_hz"] == "40000000"
    assert chip["dropped"] == 3


def test_normal_traffic_is_never_refused():
    stats = late_log.analyze(stream(500), CLEAN)
    assert stats["frames"] == 500 and stats["max_ms"] == 0.0
    assert stats["accepted_off"] == 500 and stats["refused_off"] == 0
    assert stats["silences_off"] == 0 and stats["skipped"] == 0
    assert all(row["late"] == 0 and row["silences"] == 0 for row in stats["bounds"])


def test_a_frame_held_300_ms_is_refused_at_every_bound_below_it():
    records = stream(100)
    held = record(101, 100 * 20_000, 100 * 20_000 + 8_000 + 300_000)
    after = stream(50, start_seq=102, start_us=101 * 20_000 + 300_000)
    stats = late_log.analyze(records + [held] + after, CLEAN)
    for bound in (100, 150, 200, 250):
        assert by_bound(stats)[bound]["late"] == 1, bound
    assert stats["max_ms"] == 300.0


def test_a_long_stall_counts_as_a_silence_with_or_without_the_check():
    before = stream(50)
    gap_from = 50 * 20_000
    burst = [
        record(51 + i, gap_from + i * 20_000, gap_from + 400_000 + i * 1_000)
        for i in range(20)
    ]
    after = stream(50, start_seq=71, start_us=gap_from + 420_000)
    stats = late_log.analyze(before + burst + after, CLEAN)
    assert stats["silences_off"] == 1
    for row in stats["bounds"]:
        assert row["silences"] == stats["silences_off"], row


def test_the_esps_micros_wrapping_during_the_run_keeps_the_order():
    # #225 review: the ESP has been up ~71.6 min, so micros() wraps 1 s into the
    # run. Sorting on the raw value would put the later records first.
    records = stream(200, esp_start=0xFFFFFFFF - 1_000_000)
    assert records[100]["micros"] < records[0]["micros"]  # it did wrap
    order = [r["seq"] for _, r in late_log.arrival_order(records)]
    assert order == list(range(1, 201))
    stats = late_log.analyze(records, CLEAN)
    assert stats["accepted_off"] == 200 and stats["refused_off"] == 0
    assert stats["max_ms"] == 0.0
    assert all(row["late"] == 0 and row["silences"] == 0 for row in stats["bounds"])


def test_a_reconnect_starts_a_new_replay():
    # #225 review: `R` in ground_link.py opens a new session; seq starts at 1
    # again. Without a new Receiver every frame after it would be OLD_SEQ.
    first = stream(100)
    second = stream(100, start_us=100 * 20_000 + 50_000, session=0x5678)
    stats = late_log.analyze(first + second, CLEAN)
    assert stats["sessions"] == 2 and stats["replayed"] == 200
    assert stats["accepted_off"] == 200 and stats["refused_off"] == 0
    assert all(row["late"] == 0 and row["silences"] == 0 for row in stats["bounds"])


def test_frames_refused_for_other_reasons_are_skipped():
    # #225 review: a bad-tag frame the ESP refused would be "accepted" by the
    # replay, which seals its own frames; it must not count.
    records = stream(50)
    records.insert(25, record(999, 25 * 20_000, 25 * 20_000 + 5_000, result=BAD_TAG))
    stats = late_log.analyze(records, CLEAN)
    assert stats["skipped"] == 1 and stats["replayed"] == 50


def test_report_warns_about_dropped_records_and_lost_packets():
    def text(info):
        return late_log.report(late_log.analyze(stream(50), info))

    assert "WARNING: the ESP dropped 4 records" in text({**CLEAN, "dropped": 4})
    assert "2 record packets were lost" in text({**CLEAN, "lost_batches": 2})
    assert "the ESP restarted 1 time(s)" in text({**CLEAN, "restarts": 1})
    # #228 review: a file without an F line is not "no info file"
    assert "dropped count unknown (no F line received)" in text(
        {**CLEAN, "dropped": None}
    )
    assert "no info file" in text(None)
    clean = text(CLEAN)
    assert "WARNING" not in clean and "| 200 ms | 0 | 0 | 0 |" in clean


def test_report_warns_when_the_replay_refuses_frames_with_the_check_off():
    # A seq that goes back within one session can't be replayed faithfully.
    records = stream(20) + stream(20, start_us=20 * 20_000)  # seq 1..20 twice
    text = late_log.report(late_log.analyze(records, CLEAN))
    assert "refused by the replay with the check off" in text


def B(*numbers):
    return [("B", n) for n in numbers]


def test_loss_summary_counts_the_gaps():
    # 6 and 8 never arrived; a duplicate counts once.
    summary = late_log.loss_summary(B(4, 5, 7, 9, 9))
    assert summary["lost_batches"] == 2 and summary["restarts"] == 0
    assert summary["batches_received"] == 4  # the duplicate 9 counts once


def test_an_esp_restart_is_not_counted_as_loss():
    # #228 review: 1000..1100 then a reboot (the counter restarts at 0) used
    # to count 949 lost packets.
    summary = late_log.loss_summary(B(*range(1000, 1101)) + B(*range(0, 51)))
    assert summary["lost_batches"] == 0 and summary["restarts"] == 1
    # and a real loss in the second boot is still seen
    summary = late_log.loss_summary(B(*range(1000, 1101)) + B(0, 1, 3))
    assert summary["lost_batches"] == 1


def test_a_lost_last_packet_is_seen_through_next_batch():
    # #228 review: the last R packet (flushed after `q`) is lost; the F line
    # sent after the stream stops says the next batch would be 10, so 9 is
    # missing.
    events = B(0, 1, 2, 3, 4, 5, 6, 7, 8) + [("F", 0, 10)]
    assert late_log.loss_summary(events)["lost_batches"] == 1


def test_the_highest_dropped_count_is_kept():
    # #228 review: an older F line arriving last must not lower the count.
    events = B(0, 1) + [("F", 7, 2), ("F", 3, 2)] + B(2)
    assert late_log.loss_summary(events)["dropped"] == 7
    # per boot: 7 before a reboot, then 2 after it
    events += B(*range(3, 40)) + [("F", 2, 1)] + B(0)
    summary = late_log.loss_summary(events)
    assert summary["restarts"] == 1 and summary["dropped"] == 9


def test_no_f_line_leaves_the_dropped_count_unknown():
    assert late_log.loss_summary(B(0, 1, 2))["dropped"] is None


def test_an_old_info_file_is_still_read(tmp_path):
    # #228 review: runs saved with <csv>.chip.json before the rename
    csv_path = tmp_path / "run.csv"
    (tmp_path / "run.csv.chip.json").write_text('{"dropped": 3}')
    assert late_log.read_info(csv_path) == {"dropped": 3}
    (tmp_path / "run.csv.info.json").write_text('{"dropped": 0, "lost_batches": 1}')
    assert late_log.read_info(csv_path)["lost_batches"] == 1
    assert late_log.read_info(tmp_path / "other.csv") is None


def test_a_packet_without_a_batch_line_has_no_batch():
    _, chip, batch = late_log.parse_packet(
        b"F,001640ef,239,4194304,40000000,0,57\n", 0.0
    )
    assert batch is None and chip["dropped"] == 0 and chip["next_batch"] == 57
    _, old, _ = late_log.parse_packet(b"F,001640ef,239,4194304,40000000,0\n", 0.0)
    assert old["next_batch"] is None  # an F line from #225's firmware
