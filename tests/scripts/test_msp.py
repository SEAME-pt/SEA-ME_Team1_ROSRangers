import csv
import pathlib
import struct
import sys
import threading

import pytest
import serial

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))

import msp  # noqa: E402
import msp_check  # noqa: E402
from fc_cli import is_read_only  # noqa: E402
from msp_check import (  # noqa: E402
    cmd_attitude,
    fake_fc,
    resolve_csv_path,
    run_ping,
    save_csv,
)


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


def test_fake_fc_retries_transient_serial_read_errors(monkeypatch):
    class RecoveringSerial:
        timeout = None

        def __init__(self):
            self.reads = 0
            self.writes = []

        def read(self, size):
            self.reads += 1
            if self.reads == 1:
                raise serial.SerialException("ESP is rebooting")
            if self.reads == 2:
                return msp.encode_request(msp.MSP_API_VERSION)
            stop.set()
            return b""

        def write(self, data):
            self.writes.append(data)

        def close(self):
            pass

    stop = threading.Event()
    ser = RecoveringSerial()
    stats = {"answered": 0, "bad": 0}
    fake_fc("/dev/ttyUSB0", stop, stats, ser=ser)

    assert stats.get("error") is None
    assert stats["answered"] == 1
    assert ser.writes == [msp.encode_response(msp.MSP_API_VERSION, bytes([0, 1, 46]))]


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


def test_resolve_csv_path_defaults_bare_filename_to_results():
    assert resolve_csv_path("bridge.csv") == (
        pathlib.Path(msp_check.__file__).resolve().parents[1] / "results" / "bridge.csv"
    )


def test_resolve_csv_path_preserves_explicit_directory(tmp_path):
    path = tmp_path / "logs" / "bridge.csv"
    assert resolve_csv_path(path) == path


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


class LoopbackFc:
    """Transport wired straight to the fake FC: no serial port, no ESP."""

    def __init__(self, armed=False, apply_rc=True):
        self.parser = msp.Parser()
        self.rc = list(msp_check.RC_DEFAULT)
        self.out = b""
        self.armed = armed
        self.apply_rc = apply_rc

    def send(self, data):
        for f in self.parser.feed(data):
            if f.cmd == msp.MSP_STATUS:
                answer, error = (
                    struct.pack("<HHHI", 125, 0, 0x23, int(self.armed)),
                    False,
                )
            elif f.cmd == msp.MSP_SET_RAW_RC and not self.apply_rc:
                answer, error = (
                    b"",
                    False,
                )  # acknowledged but ignored, like a CRSF receiver
            else:
                answer, error = msp_check.fake_answer(f.cmd, f.payload, self.rc)
            self.out += msp.encode_response(f.cmd, answer, error=error)

    def recv(self):
        data, self.out = self.out, b""
        return data

    def close(self):
        pass


def rc_order_args(write=True):
    return type("Args", (), {"write": write, "timeout": 0.1, "seconds": 0.05})()


def test_decode_status_reads_betaflight_extended_fields():
    payload = (
        struct.pack("<HHHI", 125, 2, 0x23, 1)
        + bytes([0])  # PID profile
        + struct.pack("<H", 17)  # CPU load
        + struct.pack("<H", 0)  # gyro cycle time
        + bytes([1, 0xAA])  # one extra flight-mode byte
        + bytes([26])  # arming disable flag count
        + struct.pack("<I", 0x40000)
        + bytes([1])  # reboot required
    )
    st = msp.decode_status(payload)
    assert st["armed"] and st["i2c_errors"] == 2 and st["cpu_load"] == 17
    assert st["arming_disable_flags"] == 0x40000
    assert st["reboot_required"] is True


def test_decode_status_short_payload_has_only_basic_fields():
    st = msp.decode(msp.MSP_STATUS, msp_check.FAKE_ANSWERS[msp.MSP_STATUS])
    assert st["armed"] is False and "reboot_required" not in st


def test_attitude_reply_bytes_decode_to_degrees():
    # Raw MSP_ATTITUDE replies as the FC sends them: roll and pitch in 0.1 deg,
    # yaw in whole degrees. The first matches the reading of 2026-10-23.
    parser = msp.Parser()
    frames = parser.feed(
        bytes.fromhex("244d3e066cb1065c005100d0")
        + bytes.fromhex("244d3e066c3bfe81ff6701b7")
    )
    assert [f.cmd for f in frames] == [msp.MSP_ATTITUDE, msp.MSP_ATTITUDE]
    assert msp.decode(frames[0].cmd, frames[0].payload) == {
        "roll_deg": 171.3,
        "pitch_deg": 9.2,
        "yaw_deg": 81,
    }
    assert msp.decode(frames[1].cmd, frames[1].payload) == {
        "roll_deg": -45.3,
        "pitch_deg": -12.7,
        "yaw_deg": 359,
    }
    assert parser.bad_checksums == 0


def test_rc_payload_roundtrip_and_analog_rssi():
    channels = [1500, 1000, 2000, 1234]
    assert msp.decode(msp.MSP_RC, msp.encode_rc(channels))["channels"] == channels
    analog = bytes([168]) + struct.pack("<HHh", 0, 512, 0)
    assert msp.decode(msp.MSP_ANALOG, analog) == {"vbat_v": 16.8, "rssi": 512}


def test_client_refuses_writes_unless_allowed():
    with pytest.raises(ValueError):
        msp.Client(LoopbackFc()).write(msp.MSP_SET_RAW_RC, b"")
    with pytest.raises(ValueError):
        msp.Client(LoopbackFc(), allow_write=True).request(msp.MSP_SET_RAW_RC)
    with pytest.raises(ValueError):
        msp.Client(LoopbackFc(), allow_write=True).write(msp.MSP_STATUS, b"")


def test_fake_fc_maps_raw_rc_like_betaflight():
    rc = list(msp_check.RC_DEFAULT)
    frame = msp.encode_rc(msp_check.RC_ORDER_SENT + msp_check.RC_AUX_LOW)
    assert msp_check.fake_answer(msp.MSP_SET_RAW_RC, frame, rc) == (b"", False)
    answer, _ = msp_check.fake_answer(msp.MSP_RC, b"", rc)
    channels = msp.decode(msp.MSP_RC, answer)["channels"]
    assert tuple(channels[:4]) == msp_check.RC_ORDER_EXPECTED
    assert msp_check.check_rc_order(channels)[1]


def test_check_rc_order_names_the_misplaced_input():
    rows, ok = msp_check.check_rc_order(list(msp_check.RC_ORDER_SENT))
    assert not ok
    assert rows[2] == ("yaw", 1600, 1000, "T", False)


def test_rc_order_passes_against_fake_fc(monkeypatch, capsys):
    monkeypatch.setattr(
        "msp_check.make_client",
        lambda args: (msp.Client(LoopbackFc(), allow_write=args.write), "loopback"),
    )
    msp_check.cmd_rc_order(rc_order_args())
    out = capsys.readouterr().out
    assert "S2-04 PASS" in out and "WRONG" not in out


def test_rc_order_explains_when_fc_ignores_values(monkeypatch, capsys):
    monkeypatch.setattr(
        "msp_check.make_client",
        lambda args: (msp.Client(LoopbackFc(apply_rc=False), allow_write=True), "x"),
    )
    msp_check.cmd_rc_order(rc_order_args())
    out = capsys.readouterr().out
    assert "S2-04 FAIL" in out and "did not apply" in out


def test_rc_order_needs_write_and_refuses_when_armed(monkeypatch):
    with pytest.raises(SystemExit, match="--write"):
        msp_check.cmd_rc_order(rc_order_args(write=False))
    fc = LoopbackFc(armed=True)
    monkeypatch.setattr(
        "msp_check.make_client",
        lambda args: (msp.Client(fc, allow_write=True), "x"),
    )
    with pytest.raises(SystemExit, match="ARMED"):
        msp_check.cmd_rc_order(rc_order_args())
    assert fc.rc == list(msp_check.RC_DEFAULT)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def test_link_watch_reports_detection_time_and_outage():
    clock = FakeClock()
    answers = iter([True] * 5 + [False] * 10 + [True] * 5)

    class Client:
        def request(self, cmd, timeout):
            clock.t += 0.01
            return (object(), 0.01) if next(answers) else (None, None)

    events, polls, ok = msp_check.run_link_watch(
        Client(), 50, 0.1, 0.1, duration=0.39, clock=clock, sleep=clock.sleep
    )
    assert (polls, ok) == (20, 10)
    lost, back = events
    assert lost["event"] == "lost" and 100 <= lost["ms"] <= 120
    assert back["event"] == "back" and back["ms"] == pytest.approx(220, abs=1)


def test_link_watch_stops_when_port_disappears():
    clock = FakeClock()

    class Client:
        def request(self, cmd, timeout):
            raise serial.SerialException(
                "device reports readiness to read but returned no data"
            )

    events, polls, _ = msp_check.run_link_watch(
        Client(), 50, 0.3, 0.1, clock=clock, sleep=clock.sleep
    )
    assert polls == 1 and events[0]["event"] == "lost"


def test_status_changes_lists_what_moved():
    base = msp.decode_status(msp_check.FAKE_ANSWERS[msp.MSP_STATUS])
    cur = dict(base, armed=True, i2c_errors=3, arming_disable_flags=0x4)
    assert msp_check.status_changes(base, cur) == [
        "ARMED",
        "I2C errors 0 → 3",
        "arming_disable_flags ? → 0x00000004",
    ]
    assert msp_check.status_changes(base, base) == []


def test_apply_duration_sets_request_count():
    args = type("Args", (), {"duration": 600, "rate": 50, "n": 500})()
    msp_check.apply_duration(args)
    assert args.n == 30000
    args.rate = 0
    with pytest.raises(SystemExit):
        msp_check.apply_duration(args)


def test_esp_error_text_explains_a_lost_usb_port():
    text = msp_check.esp_error_text(
        serial.SerialException("device reports readiness to read but returned no data")
    )
    assert "USB disconnect" in text and "returned no data" in text
    assert msp_check.esp_error_text(ValueError("plain")) == "plain"


class StreamFc:
    """Fake FC behind a link with a fixed delay, on a fake clock. Drops chosen requests."""

    def __init__(self, clock, delay, drop=()):
        self.clock, self.delay, self.drop = clock, delay, set(drop)
        self.queue, self.received, self.last_due = [], 0, 0.0

    def send(self, data):
        for f in msp.Parser().feed(data):
            delay = self.delay(self.received) if callable(self.delay) else self.delay
            # one link keeps the order: an answer cannot overtake the one before it
            due = max(self.clock.t + delay, self.last_due)
            if self.received not in self.drop:
                answer, error = msp_check.fake_answer(f.cmd, f.payload, [])
                frame = msp.encode_response(f.cmd, answer, error=error)
                self.queue.append((due, frame))
                self.last_due = due
            self.received += 1

    def recv(self):
        self.clock.t += 0.001  # like the 1 ms transport timeout
        ready = [frame for due, frame in self.queue if due <= self.clock.t]
        self.queue = [(due, f) for due, f in self.queue if due > self.clock.t]
        return b"".join(ready)


def test_stream_keeps_the_rate_when_rtt_exceeds_the_period():
    clock = FakeClock()
    client = msp.Client(StreamFc(clock, delay=0.031))
    times = []
    rtts, lost, records = msp_check.run_stream(
        client, 500, 50, 0.5, quiet=True, answer_times=times, clock=clock
    )
    assert lost == 0 and len(records) == 500
    assert all(30 <= r <= 33 for r in rtts)
    # 500 requests at 50 Hz take 10 s (not 500 × 31 ms = 15.5 s), plus one timeout
    # waiting for the last answers
    assert clock.t == pytest.approx(10.0 + 0.5, abs=0.1)
    extra = msp_check.timing_metrics(times, clock.t, len(records))
    assert extra["max_gap_ms"] <= 22 and extra["gaps_over_250ms"] == 0


def test_stream_marks_dropped_requests_and_measures_the_silence():
    clock = FakeClock()
    drops = range(100, 120)  # 20 requests in a row = 400 ms without answers
    client = msp.Client(StreamFc(clock, delay=0.03, drop=drops))
    times = []
    rtts, lost, records = msp_check.run_stream(
        client, 300, 50, 0.5, quiet=True, answer_times=times, clock=clock
    )
    assert lost == 20
    assert [r["request"] for r in records if r["status"] == "timeout"] == [
        i + 1 for i in drops
    ]
    assert all(r <= 33 for r in rtts)  # no answer is matched to the wrong request
    extra = msp_check.timing_metrics(times, clock.t, len(records))
    assert 400 <= extra["max_gap_ms"] <= 425 and extra["gaps_over_250ms"] == 1


def test_save_csv_adds_timing_metrics(tmp_path):
    path = tmp_path / "run.csv"
    extra = msp_check.timing_metrics([0.0, 0.02, 0.32], 10.0, 3)
    msp_check.save_csv(
        path, [{"request": 1, "status": "ok", "rtt_ms": "5"}], 0, 0, extra
    )
    with (tmp_path / "run_analytics.csv").open(newline="") as fh:
        analytics = dict(csv.reader(fh))
    assert analytics["effective_rate_hz"] == "0.3"
    assert analytics["max_gap_ms"] == "300.0"
    assert analytics["gaps_over_250ms"] == "1"


def test_stream_matches_answers_during_a_latency_spike():
    clock = FakeClock()
    # requests 100-109 take 150 ms: several requests with the same command are pending
    client = msp.Client(
        StreamFc(clock, delay=lambda i: 0.15 if 100 <= i < 110 else 0.03)
    )
    rtts, lost, records = msp_check.run_stream(
        client, 300, 50, 0.5, quiet=True, clock=clock
    )
    assert lost == 0
    rtt = {r["request"]: float(r["rtt_ms"]) for r in records}
    assert 149 <= rtt[101] <= 153 and 29 <= rtt[100] <= 33
    assert 29 <= rtt[150] <= 33


def test_match_stream_ignores_an_answer_that_fits_nowhere():
    sends = [(1, 0.00), (2, 0.02), (1, 0.04)]
    answers = [(1, 0.03), (3, 0.04), (1, 0.07)]
    assert msp_check.match_stream(sends, answers, 0.5) == pytest.approx(
        {0: 0.03, 2: 0.03}
    )


def test_match_stream_with_backlog_and_drops():
    """Harsh link: backlogs up to 340 ms, random drops and a 75-request drop burst.
    The loss count must be exact; only the burst edge may be ambiguous."""
    import random

    rng = random.Random(1)
    k = len(msp_check.STREAM_COMMANDS)
    sends = [(msp_check.STREAM_COMMANDS[i % k], i * 0.02) for i in range(3000)]
    answers, truth, last = [], {}, 0.0
    for i, (cmd, t) in enumerate(sends):
        if 2000 <= i < 2075 or rng.random() < 0.002:
            continue
        last = max(t + rng.choice([0.03] * 20 + [0.1, 0.2, 0.34]), last)
        answers.append((cmd, last))
        truth[i] = last - t
    matched = msp_check.match_stream(sends, answers, 0.5)
    assert len(matched) == len(truth)
    wrong = [
        i for i in matched if i not in truth or matched[i] != pytest.approx(truth[i])
    ]
    assert len(wrong) <= 0.01 * len(truth)
    assert all(1990 <= i < 2085 for i in wrong)  # only around the burst


class EchoLink:
    """ESP with TX wired to RX: every request comes back unchanged."""

    def __init__(self):
        self.buf = b""

    def send(self, data):
        self.buf += data

    def recv(self):
        data, self.buf = self.buf, b""
        return data


def test_loopback_counts_the_echo_only_when_asked():
    assert msp.Client(EchoLink()).request(msp.MSP_API_VERSION, 0.01) == (None, None)
    f, rtt = msp.Client(EchoLink(), loopback=True).request(msp.MSP_API_VERSION, 0.01)
    assert f.direction == "<" and rtt is not None


def test_stream_with_loopback_echo():
    clock = FakeClock()

    class TimedEcho(EchoLink):
        def recv(self):
            clock.t += 0.001
            return super().recv()

    client = msp.Client(TimedEcho(), loopback=True)
    rtts, lost, _ = msp_check.run_stream(client, 100, 50, 0.5, quiet=True, clock=clock)
    assert lost == 0 and len(rtts) == 100
