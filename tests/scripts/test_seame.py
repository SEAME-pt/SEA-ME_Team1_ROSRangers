import pathlib
import subprocess
import sys

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import seame  # noqa: E402


@pytest.fixture
def calls(monkeypatch):
    """Record the scripts seame would run instead of running them."""
    recorded = []
    monkeypatch.setattr(
        seame, "run_script", lambda script, args: recorded.append((script, args)) or 0
    )
    return recorded


@pytest.fixture
def answers(monkeypatch):
    def feed(*values):
        it = iter(values)
        monkeypatch.setattr("builtins.input", lambda _="": next(it))

    return feed


def test_resolve_command_preserves_specialist_arguments():
    script, args = seame.resolve_command(["network", "production", "connect", "-v"])
    assert script == "setup_production.py"
    assert args == ["connect", "-v"]


def test_network_alone_means_the_drone_access_point():
    assert seame.resolve_command(["network", "status"]) == (
        "setup_production.py",
        ["status"],
    )
    assert seame.resolve_command(["network", "test", "status"])[0] == "setup_test.py"


def test_list_is_available_without_hardware(capsys):
    assert seame.main(["list"]) == 0
    output = capsys.readouterr().out
    assert "network" in output
    assert "msp" in output
    assert "fc dump" in output


def test_unknown_command_returns_usage_error(capsys):
    assert seame.main(["does-not-exist"]) == 2
    assert "Unknown command" in capsys.readouterr().err


def test_run_script_dispatches_to_script(monkeypatch):
    seen = []
    monkeypatch.setattr(
        seame.subprocess,
        "run",
        lambda command, check: seen.append(command)
        or subprocess.CompletedProcess(command, 0),
    )
    assert seame.main(["msp", "--usb", "info"]) == 0
    assert pathlib.Path(seen[0][1]).name == "msp_check.py"
    assert seen[0][2:] == ["--usb", "info"]


def test_msp_global_options_work_after_subcommand(calls):
    seame.main(["msp", "ping", "-n", "30000", "--timeout", "1", "--rate", "50"])
    seame.main(["msp", "attitude", "--usb", "--esp=10.0.0.5"])
    assert calls == [
        ("msp_check.py", ["--timeout", "1", "ping", "-n", "30000", "--rate", "50"]),
        ("msp_check.py", ["--usb", "--esp=10.0.0.5", "attitude"]),
    ]


def test_msp_option_without_value_is_left_for_argparse():
    assert seame.normalize_msp_arguments(["ping", "--timeout"]) == ["ping", "--timeout"]


@pytest.mark.parametrize(
    "argv",
    [
        ["esp", "query"],
        ["network", "setup"],
        ["network", "test", "setup"],
        ["network", "production", "--port", "/dev/ttyUSB0", "reboot-test"],
        ["msp", "cycle1a"],
        ["msp", "fake-fc"],
        ["msp", "usb-relay"],
        ["msp", "ping", "--loopback"],
    ],
)
def test_esp_usb_commands_are_refused(argv, calls, capsys):
    assert seame.main(argv) == 2
    assert calls == []
    assert "--esp-on-usb" in capsys.readouterr().err


def test_esp_usb_commands_run_with_explicit_flag(calls):
    assert seame.main(["--esp-on-usb", "esp", "query"]) == 0
    assert calls == [("esp_at.py", ["query"])]


def test_help_of_esp_usb_commands_is_not_refused(calls):
    seame.main(["esp", "--help"])
    assert calls == [("esp_at.py", ["--help"])]


def test_soldered_link_commands_are_allowed(calls):
    for argv in (["network", "connect"], ["msp", "info"], ["fc", "reboot"]):
        assert seame.main(argv) == 0
    assert [script for script, _ in calls] == [
        "setup_production.py",
        "msp_check.py",
        "fc_cli.py",
    ]


def test_network_menu_connects(answers, calls):
    answers("1")
    assert seame.menu_network() == 0
    assert calls == [("setup_production.py", ["connect"])]


def test_read_menu_asks_the_path_once(answers, calls):
    answers("2", "2")  # FC USB, attitude
    assert seame.menu_read() == 0
    assert calls == [("msp_check.py", ["--usb", "attitude"])]


def test_read_menu_ping_streams_with_duration_and_csv(answers, calls):
    answers("1", "4", "120", "near.csv")
    assert seame.menu_read() == 0
    assert calls == [
        ("msp_check.py", ["ping", "--stream", "--duration", "120", "--csv", "near.csv"])
    ]


def test_fc_menu_runs_quoted_cli_commands(answers, calls):
    answers("3", '"get failsafe" serial')
    assert seame.menu_fc_usb() == 0
    assert calls == [("fc_cli.py", ["run", "get failsafe", "serial"])]


def test_menu_back_returns_none(answers):
    answers("b")
    assert seame.menu_fc_usb() is None


def test_interactive_stays_open_after_running_command(answers, calls):
    answers("3", "1", "", "q")  # FC over USB → dump → Enter → quit
    assert seame.interactive() == 0
    assert calls == [("fc_cli.py", ["dump"])]


def test_quit_exits_from_nested_menu(answers):
    answers("1", "q")
    assert seame.interactive() == 0


def test_wait_for_enter_keeps_command_output_visible(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt: print(prompt) or "")
    seame.wait_for_enter()
    assert "Press Enter to return to the menu..." in capsys.readouterr().out


def test_clear_screen_only_writes_escape_sequence_in_terminal(monkeypatch, capsys):
    monkeypatch.setattr(seame.sys.stdout, "isatty", lambda: True)
    seame.clear_screen()
    assert capsys.readouterr().out == "\033[2J\033[H"


def test_test_list_names_every_test(capsys):
    assert seame.main(["test", "list"]) == 0
    output = capsys.readouterr().out
    assert all(key in output for key in seame.TESTS)


def test_unknown_test_is_an_error(capsys):
    assert seame.main(["test", "s9-99"]) == 2


def test_s2_01_runs_info_then_100_pings(monkeypatch, calls):
    monkeypatch.setattr(seame, "on_drone_network", lambda: True)
    assert seame.main(["test", "s2-01"]) == 0
    assert calls == [
        ("msp_check.py", ["info"]),
        ("msp_check.py", ["ping", "-n", "100", "--csv", "s2-01.csv"]),
    ]


def test_wifi_test_stops_when_not_on_drone_network(monkeypatch, answers, calls):
    monkeypatch.setattr(seame, "on_drone_network", lambda: False)
    answers("no")
    assert seame.main(["test", "s2-03"]) == 1
    assert calls == []


def test_rc_order_write_needs_confirmation(monkeypatch, answers, calls):
    monkeypatch.setattr(seame, "on_drone_network", lambda: True)
    answers("no")
    assert seame.test_s2_04_write() == 1
    answers("yes")
    assert seame.test_s2_04_write() == 0
    assert calls == [("msp_check.py", ["rc-order", "--write"])]


def test_s2_06_reboots_the_fc_twice_during_link_watch(monkeypatch, calls):
    monkeypatch.setattr(seame, "on_drone_network", lambda: True)
    started = []

    class FakeProc:
        def __init__(self, command):
            started.append(command)

        def poll(self):
            return None

        def wait(self):
            return 0

    monkeypatch.setattr(seame.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(seame.time, "sleep", lambda _: None)
    assert seame.test_s2_06(first=0, second=0, total=1) == 0
    assert started[0][2:] == ["link-watch", "--duration", "1", "--csv", "s2-06.csv"]
    assert calls == [("fc_cli.py", ["reboot"]), ("fc_cli.py", ["reboot"])]


@pytest.mark.parametrize("script", ["setup_test.py", "setup_production.py"])
def test_setup_scripts_need_a_subcommand(script):
    """Without one they used to run `setup`, which talks to the ESP over USB."""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / script)], capture_output=True, text=True
    )
    assert result.returncode == 2
    assert "required" in result.stderr
