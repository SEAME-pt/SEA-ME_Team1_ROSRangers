import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))

import esp_at  # noqa: E402
import fc_cli  # noqa: E402
import msp  # noqa: E402
import setup_production  # noqa: E402


def test_flash_size_from_esptool_output():
    assert esp_at.flash_size_bytes("Detected flash size: 4MB\n") == 4 * 1024 * 1024
    assert esp_at.flash_size_bytes("Detected flash size: 512KB") == 512 * 1024
    assert esp_at.flash_size_bytes("Manufacturer: 20") is None


def test_latest_backup_is_newest_stamp(tmp_path, monkeypatch):
    monkeypatch.setattr(esp_at, "BACKUP_DIR", tmp_path)
    assert esp_at.latest_backup() is None
    for stamp in ("2026-10-12_090000", "2026-10-14_080000", "2026-10-13_230000"):
        (tmp_path / f"esp8266_flash_{stamp}.bin").write_bytes(b"x")
    assert esp_at.latest_backup().name == "esp8266_flash_2026-10-14_080000.bin"


def test_boot_bytes_report_counts_what_an_msp_parser_sees():
    good = msp.encode_response(msp.MSP_API_VERSION, bytes([0, 1, 46]))
    report = esp_at.boot_bytes_report(b"\x00\xff ets Jan  8 2013$M<\x02\x01xx\x00" + good)
    assert report["msp_headers"] == 2
    assert report["msp_frames"] == 1
    assert report["bad_checksums"] == 1


def test_hard_reset_pulses_rts_with_dtr_released(monkeypatch):
    monkeypatch.setattr(esp_at.time, "sleep", lambda s: None)

    class Ser:
        def __init__(self):
            self.log = []

        def __setattr__(self, name, value):
            if name != "log":
                self.log.append((name, value))
            object.__setattr__(self, name, value)

    ser = Ser()
    esp_at.hard_reset(ser)
    assert ser.log == [("dtr", False), ("rts", True), ("rts", False)]


def test_set_sleep_in_bridge_leaves_and_reenters_passthrough(monkeypatch):
    sent, written = [], []
    replies = {
        "AT": "OK",
        "AT+SLEEP?": "+SLEEP:2\nOK",
        "AT+SLEEP=0": "OK",
        "AT+CIPSEND": "OK\n>",
    }
    monkeypatch.setattr(esp_at.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        esp_at, "at", lambda ser, cmd, wait=1.0, secret="": sent.append(cmd) or replies[cmd]
    )
    ser = type(
        "Ser",
        (),
        {"write": lambda self, d: written.append(d), "reset_input_buffer": lambda self: None},
    )()
    assert esp_at.set_sleep_in_bridge(ser, "none") == "2"
    assert written == [b"+++"]
    assert sent == ["AT", "AT+SLEEP?", "AT+SLEEP=0", "AT+CIPSEND"]


def test_compare_diffs_ignores_comments(tmp_path, capsys):
    old, new = tmp_path / "a.txt", tmp_path / "b.txt"
    old.write_text("# version\n# 4.5.1 Oct 12\nset gyro = 1\n")
    new.write_text("# version\n# 4.5.1 Oct 16\n\nset gyro = 1\n")
    fc_cli.cmd_compare_diffs(type("Args", (), {"files": [old, new]})())
    assert "No configuration change" in capsys.readouterr().out
    new.write_text("set gyro = 2\n")
    fc_cli.cmd_compare_diffs(type("Args", (), {"files": [old, new]})())
    out = capsys.readouterr().out
    assert "-set gyro = 1" in out and "+set gyro = 2" in out


def test_reboot_once_times_connect_and_ping(monkeypatch):
    clock = iter([0.0, 4.2, 4.5, 5.1, 5.3])
    pings = iter([False, True])
    calls = []
    monkeypatch.setattr(setup_production, "nmcli", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(setup_production, "nm_up", lambda pw, wait: calls.append("up"))
    monkeypatch.setattr(setup_production, "ping_once", lambda ip: next(pings))
    monkeypatch.setattr(esp_at, "hard_reset", lambda ser: calls.append("reset"))
    row = setup_production.reboot_once(None, "pw", 30, clock=lambda: next(clock))
    assert calls == [("connection", "down", "seame-drone"), "reset", "up"]
    assert row == {"connected": True, "connect_s": "4.20", "reachable_s": "5.30"}


def test_reboot_once_records_failed_connect(monkeypatch):
    def fail(pw, wait):
        raise esp_at.EspError("timeout")

    monkeypatch.setattr(setup_production, "nmcli", lambda *a, **k: "")
    monkeypatch.setattr(setup_production, "nm_up", fail)
    monkeypatch.setattr(esp_at, "hard_reset", lambda ser: None)
    row = setup_production.reboot_once(None, "pw", 30)
    assert row["connected"] is False and row["connect_s"] == ""


def test_esptool_uses_names_esptool_4_accepts_and_reports_failures(monkeypatch):
    import subprocess

    import pytest

    calls = []

    def run(cmd, capture_output, text):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 2, "", "invalid choice")

    monkeypatch.setattr(esp_at.subprocess, "run", run)
    with pytest.raises(esp_at.EspError, match="flash_id failed"):
        esp_at.cmd_backup(type("Args", (), {"port": "/dev/ttyUSB0"})())
    assert calls[0][-1] == "flash_id"


def test_installed_esptool_accepts_the_command_names():
    import subprocess

    for name in ("flash_id", "read_flash", "verify_flash", "write_flash"):
        r = subprocess.run(
            [sys.executable, "-m", "esptool", name, "-h"], capture_output=True
        )
        assert r.returncode == 0, name


def test_fc_cli_saves_into_the_repo_layout():
    root = pathlib.Path(__file__).resolve().parents[2]
    assert fc_cli.CONFIG_DIR == root / "drone" / "config" / "betaflight"
