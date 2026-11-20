import pathlib
import sys

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[3] / "drone" / "tools" / "bench")
)

import rt_profile  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[3]
TASKS = """# ===== tasks
Task list             rate/hz  max/us  avg/us maxload avgload  total/ms   late    run reqd/us
00 - (         SYSTEM)     10       7       0    0.0%    0.0%        19     38  28942       2
04 - (            PID)   8052      72      44   57.9%   35.5%   1087300      0 23324698       0
27 - (SPEED_NEGOTIATION)     99      14       1    0.1%    0.0%       368    190 287806       3
RX Check Function                  11       4                      4397
Total (excluding SERIAL)                                51.5%
"""


def test_parse_tasks_reads_the_rows_by_header():
    rows = rt_profile.parse_tasks(TASKS)
    assert [r["name"] for r in rows] == ["SYSTEM", "PID", "SPEED_NEGOTIATION"]
    assert rows[1] == {
        "name": "PID",
        "rate_hz": 8052,
        "max_us": 72,
        "avg_us": 44,
        "maxload": 57.9,
        "avgload": 35.5,
    }


def test_parse_tasks_reads_a_committed_capture():
    log = REPO / "drone/config/betaflight/4.5.1/cli_read_2026-10-16_172704.txt"
    names = [r["name"] for r in rt_profile.parse_tasks(log.read_text())]
    assert {"GYRO", "PID", "SERIAL"} <= set(names)


def test_packet_to_stats():
    # 98 loops at 48-50 us, one at 1 ms, one stall of 30 ms (window max exact)
    a = rt_profile.parse_packet(b"L,0,1000000,99,1024\nB,48,98\nB,1024,1\n", 1.0)
    b = rt_profile.parse_packet(b"L,1,2000000,1,30123\nB,28672,1\n", 2.0)
    assert a[0] == {
        "pc_time_s": 1.0,
        "window": 0,
        "now_us": 1000000,
        "loops": 99,
        "max_us": 1024,
        "lo_us": 48,
        "count": 98,
    }
    assert rt_profile.esp_stats(a + b) == {
        "loops": 100,
        "windows": 2,
        "median_us": 48,
        "p99_us": 1024,
        "max_us": 30123,
    }


def test_bucket_width_follows_the_firmware_octaves():
    widths = [rt_profile.bucket_width(lo) for lo in (3, 16, 31, 32, 48, 60, 64, 1024)]
    assert widths == [1, 1, 1, 2, 2, 2, 4, 64]


def test_bad_lines_are_skipped():
    assert rt_profile.parse_packet(b"B,48,3\nL,x\n", 0.0) == []  # B before any L


def test_plot_writes_both_pngs(tmp_path):
    tasks = tmp_path / "tasks.txt"
    tasks.write_text(TASKS)
    esp = tmp_path / "esp.csv"
    esp.write_text(
        "pc_time_s,window,now_us,loops,max_us,lo_us,count\n1.0,0,1000000,3,60,48,2\n1.0,0,1000000,3,60,56,1\n"
    )
    args = rt_profile.argparse.Namespace(
        tasks=str(tasks), esp=str(esp), out=str(tmp_path / "out")
    )
    rt_profile.cmd_plot(args)
    assert (tmp_path / "out/betaflight_tasks.png").stat().st_size > 0
    assert (tmp_path / "out/esp_loop_period.png").stat().st_size > 0
