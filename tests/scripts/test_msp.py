import csv
import pathlib
import struct
import sys
import threading

import pytest
import serial

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))

import msp  # noqa: E402
from fc_cli import is_read_only  # noqa: E402
from msp_check import cmd_attitude, fake_fc, run_ping, save_csv  # noqa: E402


def test_api_version_request_matches_known_bytes():
    assert msp.encode_request(msp.MSP_API_VERSION) == bytes.fromhex("244d3c000101")


def test_request_rejects_out_of_range():
    with pytest.raises(ValueError):
        msp.encode_request(256)
    with pytest.raises(ValueError):
        msp.encode_request(1, bytes(256))


def test_parser_roundtrip_and_split_feed():
    frame = msp.encode_response(msp.MSP_API_VERSION, bytes([0, 1, 46]))
    p = msp.Parser()
    assert p.feed(frame[:4]) == []
    (f,) = p.feed(frame[4:])
    assert (f.direction, f.cmd, f.payload) == (
        ">",
        msp.MSP_API_VERSION,
        bytes([0, 1, 46]),
    )


def test_parser_skips_noise_and_counts_bad_checksum():
    good = msp.encode_response(msp.MSP_FC_VARIANT, b"BTFL")
    bad = bytearray(good)
    bad[-1] ^= 0xFF
    p = msp.Parser()
    frames = p.feed(b"\x00garbage$" + bytes(bad) + good)
    assert [f.payload for f in frames] == [b"BTFL"]
    assert p.bad_checksums == 1


def test_parser_error_frame():
    (f,) = msp.Parser().feed(msp.encode_response(200, error=True))
    assert f.direction == "!" and f.cmd == 200


def test_decoders():
    assert msp.decode(msp.MSP_API_VERSION, bytes([0, 1, 46])) == {
        "protocol": 0,
        "api": "1.46",
    }
    att = msp.decode(msp.MSP_ATTITUDE, struct.pack("<hhh", -123, 45, 270))
    assert att == {"roll_deg": -12.3, "pitch_deg": 4.5, "yaw_deg": 270}
    st = msp.decode(msp.MSP_STATUS, struct.pack("<HHHI", 125, 0, 0x23, 1))
    assert st["armed"] is True and st["cycle_time_us"] == 125


def test_client_refuses_write_commands():
    class Dummy:
        def send(self, data):
            raise AssertionError("must not send")

        def recv(self):
            return b""

        def close(self):
            pass

    with pytest.raises(ValueError):
        msp.Client(Dummy()).request(200)  # MSP_SET_RAW_RC


def test_client_trace_is_disabled_by_default():
    class Dummy:
        def send(self, data):
            pass

        def recv(self):
            return msp.encode_response(msp.MSP_API_VERSION, bytes([0, 1, 46]))

        def close(self):
            pass

    frame, _ = msp.Client(Dummy()).request(msp.MSP_API_VERSION)
    assert frame.payload == bytes([0, 1, 46])


@pytest.mark.parametrize(
    "line,expected",
    [
        ("version", True),
        ("get failsafe", True),
        ("diff all", True),
        ("serial", True),
        ("serial 3 1 115200 57600 0 115200", False),
        ("set vtx_power = 1", False),
        ("save", False),
        ("feature -3D", False),
        ("feature", True),
        ("defaults", False),
    ],
)
def test_fc_cli_read_only_filter(line, expected):
    assert is_read_only(line) is expected


def test_fake_fc_records_serial_errors(monkeypatch):
    error = serial.SerialException("device disconnected")
    monkeypatch.setattr(
        "msp_check.esp_at.open_port",
        lambda port: (_ for _ in ()).throw(error),
    )
    stats = {"answered": 0, "bad": 0}
    fake_fc("/dev/ttyUSB0", threading.Event(), stats)
    assert stats["error"] == "device disconnected"


def test_run_ping_records_successes_and_timeouts():
    class DummyClient:
        def __init__(self):
            self.calls = 0

        def request(self, command, timeout):
            self.calls += 1
            if self.calls == 2:
                return None, None
            return object(), 0.025

    rtts, lost, records = run_ping(DummyClient(), 3, 0, 0.5, quiet=True)
    assert rtts == [25.0, 25.0]
    assert lost == 1
    assert records == [
        {"request": 1, "status": "ok", "rtt_ms": "25.000"},
        {"request": 2, "status": "timeout", "rtt_ms": ""},
        {"request": 3, "status": "ok", "rtt_ms": "25.000"},
    ]


def test_save_csv_writes_raw_and_analytics(tmp_path):
    raw_path = tmp_path / "results" / "bridge.csv"
    records = [
        {"request": 1, "status": "ok", "rtt_ms": "20.000"},
        {"request": 2, "status": "timeout", "rtt_ms": ""},
    ]
    save_csv(raw_path, records, lost=1, bad=0)
    analytics_path = tmp_path / "results" / "bridge_analytics.csv"
    assert raw_path.exists()
    assert analytics_path.exists()
    with raw_path.open(newline="") as fh:
        assert list(csv.DictReader(fh))[1]["status"] == "timeout"
    with analytics_path.open(newline="") as fh:
        analytics = dict(csv.reader(fh))
    assert analytics["requests"] == "2"
    assert analytics["lost"] == "1"
    assert analytics["loss_percent"] == "50.0"


def test_attitude_reports_lost_serial_connection(monkeypatch, capsys):
    class DummyClient:
        parser = type("Parser", (), {"bad_checksums": 0})()

        def request(self, command, timeout):
            raise serial.SerialException("write failed: Input/output error")

        def close(self):
            pass

    monkeypatch.setattr(
        "msp_check.make_client", lambda args: (DummyClient(), "FC over USB")
    )
    args = type("Args", (), {"trace": False, "timeout": 0.5, "rate": 10})()
    with pytest.raises(SystemExit) as exc:
        cmd_attitude(args)
    assert "FC serial connection was lost" in str(exc.value)
    assert "USB cable" in str(exc.value)
