import pathlib
import socket
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))

import esp_timing  # noqa: E402

# The firmware's format: one R line, then one Q line per request.
PACKET = (
    b"R,7,5000000,1200,0\n"
    b"Q,1,1000000,1000050,1001000,1003200,1\n"
    b"Q,2,1020000,1020050,1021000,1023200,1\n"
    b"Q,3,1350000,1350050,0,0,0\n"
)


def test_parse_packet_reads_the_firmware_lines():
    report, stamps = esp_timing.parse_packet(PACKET + b"garbage\n", 1.5)
    assert report == {
        "pc_time_s": 1.5,
        "report": 7,
        "now_us": 5000000,
        "loop_max_us": 1200,
        "dropped": 0,
    }
    assert [s["seq"] for s in stamps] == [1, 2, 3]
    assert stamps[0]["uart_in"] == 1001000


def test_elapsed_ms_survives_the_micros_wrap():
    assert esp_timing.elapsed_ms(2**32 - 1000, 500) == 1.5


def test_summary_splits_time_and_loss_by_stage():
    report, stamps = esp_timing.parse_packet(PACKET, 0.0)
    text = "\n".join(esp_timing.summarize(stamps, [report], sent=5, pc_lost=4))
    assert "FC answered 2, no FC reply 1, ESP send failed 0" in text
    assert "max    3.20 ms" in text  # whole ESP + FC
    assert "330.0 ms before seq 3" in text  # the Wi-Fi silence before the ESP
    assert "Lost before the ESP (Wi-Fi PC -> ESP): 2 of 5" in text
    assert "Lost after the ESP (Wi-Fi ESP -> PC): 1" in text


def test_listen_saves_and_summary_reads_back(tmp_path, capsys):
    out = tmp_path / "run.csv"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    args = type(
        "Args",
        (),
        {
            "port": port,
            "duration": 1.5,
            "csv": str(out),
            "sent": None,
            "pc_lost": None,
            "threshold": 300,
        },
    )

    def send():
        time.sleep(0.5)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(PACKET, ("127.0.0.1", port))

    threading.Thread(target=send).start()
    esp_timing.cmd_listen(args)
    assert len(esp_timing.read_csv(out)) == 3
    assert esp_timing.read_csv(esp_timing.reports_path(out))[0]["report"] == 7
    capsys.readouterr()
    args.csv = str(out)
    esp_timing.cmd_summary(args)
    assert "records received 3" in capsys.readouterr().out
