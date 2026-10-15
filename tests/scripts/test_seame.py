import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))

import seame  # noqa: E402


def test_resolve_command_preserves_specialist_arguments():
    command, args = seame.resolve_command(
        ["network", "production", "connect", "--verbose"]
    )
    assert command.script == "setup_production.py"
    assert args == ["connect", "--verbose"]


def test_list_is_available_without_hardware(capsys):
    assert seame.main(["list"]) == 0
    output = capsys.readouterr().out
    assert "network test" in output
    assert "msp" in output
    assert "fc dump" in output


def test_unknown_command_returns_usage_error(capsys):
    assert seame.main(["does-not-exist"]) == 2
    assert "Unknown command" in capsys.readouterr().err


def test_run_command_dispatches_to_script(monkeypatch):
    calls = []
    monkeypatch.setattr(
        seame.subprocess,
        "run",
        lambda command: calls.append(command) or type("Result", (), {"returncode": 0})(),
    )
    command, args = seame.resolve_command(["msp", "--usb", "info"])
    assert seame.run_command(command, args) == 0
    assert pathlib.Path(calls[0][-3]).name == "msp_check.py"
    assert calls[0][-2:] == ["--usb", "info"]


def test_msp_global_options_work_after_subcommand(monkeypatch):
    calls = []
    monkeypatch.setattr(
        seame.subprocess,
        "run",
        lambda command: calls.append(command) or type("Result", (), {"returncode": 0})(),
    )
    command, args = seame.resolve_command(
        ["msp", "ping", "-n", "30000", "--rate", "50", "--timeout", "1"]
    )
    assert seame.run_command(command, args) == 0
    assert calls[0][-7:] == [
        "--timeout",
        "1",
        "ping",
        "-n",
        "30000",
        "--rate",
        "50",
    ]


def test_network_menu_uses_numeric_scenario_and_action(monkeypatch):
    answers = iter(["1", "2"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    command, args = seame.menu_command("network")
    assert command.script == "setup_test.py"
    assert args == ["status"]


def test_group_menu_selects_msp_usb_info(monkeypatch):
    answers = iter(["8"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    command, args = seame.menu_command("msp")
    assert command.script == "msp_check.py"
    assert args == ["--usb", "info"]


def test_group_menu_selects_fake_fc_and_arguments(monkeypatch):
    answers = iter(["4", "--trace"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    command, args = seame.menu_command("msp")
    assert command.script == "msp_check.py"
    assert args == ["fake-fc", "--trace"]


def test_group_menu_selects_usb_relay_and_arguments(monkeypatch):
    answers = iter(["5", "--esp-port-usb /dev/ttyUSB0 --fc-port /dev/ttyACM0"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    command, args = seame.menu_command("msp")
    assert command.script == "msp_check.py"
    assert args == [
        "usb-relay",
        "--esp-port-usb",
        "/dev/ttyUSB0",
        "--fc-port",
        "/dev/ttyACM0",
    ]


def test_group_menu_selects_usb_attitude(monkeypatch):
    answers = iter(["7", ""])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    command, args = seame.menu_command("msp")
    assert command.script == "msp_check.py"
    assert args == ["--usb", "attitude"]


def test_interactive_stays_open_after_running_command(monkeypatch):
    answers = iter(["2", "1", "", "q"])
    calls = []
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    monkeypatch.setattr(
        seame,
        "run_command",
        lambda command, args: calls.append((command.name, args)) or 0,
    )
    assert seame.interactive() == 0
    assert calls == [("fc", ["dump"])]


def test_wait_for_enter_keeps_command_output_visible(monkeypatch, capsys):
    answers = iter([""])
    monkeypatch.setattr("builtins.input", lambda prompt: (print(prompt), next(answers))[1])
    seame.wait_for_enter()
    assert "Press Enter to return to the menu..." in capsys.readouterr().out


def test_quit_exits_from_nested_menu(monkeypatch):
    answers = iter(["1", "q"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert seame.interactive() == 0


def test_clear_screen_only_writes_escape_sequence_in_terminal(monkeypatch, capsys):
    monkeypatch.setattr(seame.sys.stdout, "isatty", lambda: True)
    seame.clear_screen()
    assert capsys.readouterr().out == "\033[2J\033[H"
