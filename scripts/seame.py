#!/usr/bin/env python3
"""Unified command interface for the SEA:ME drone scripts.

Run without arguments for an interactive menu, or use a command directly:

    python3 scripts/seame.py list
    python3 scripts/seame.py network test setup
    python3 scripts/seame.py fc dump
    python3 scripts/seame.py msp --usb info

Arguments after a command are passed to the existing specialist script. This
keeps one entry point convenient without duplicating hardware logic.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Command:
    name: str
    description: str
    script: str | None
    prefix: tuple[str, ...] = ()


COMMANDS = (
    Command("network test", "Configure or inspect the SEAME Wi-Fi scenario.", "setup_test.py"),
    Command(
        "network production",
        "Configure, connect, disconnect, or inspect the drone AP.",
        "setup_production.py",
    ),
    Command("fc", "Run Betaflight USB CLI and firmware-backup commands.", "fc_cli.py"),
    Command("msp", "Run read-only MSP checks over USB or the ESP bridge.", "msp_check.py"),
    Command("esp", "Inspect, back up, or manually control ESP AT firmware.", "esp_at.py"),
)

GROUPS = (
    ("network", "network"),
    ("fc", "fc"),
    ("msp", "msp"),
    ("esp", "esp"),
)


def clear_screen() -> None:
    if sys.stdout.isatty():
        print("\033[2J\033[H", end="")


def prompt_choice(title: str, options: list[tuple[str, str]]) -> int | None:
    """Show numbered options and return the selected zero-based index."""
    while True:
        clear_screen()
        print(f"\n{title}")
        for number, (_, label) in enumerate(options, 1):
            print(f"  {number}) {label}")
        print("  b) Back")
        try:
            choice = input("Select an option: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if choice in {"q", "quit", "exit"}:
            return -1
        if choice in {"b", "back"}:
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(options):
            return int(choice) - 1
        print("Please enter one of the displayed numbers.")


def menu_command(group: str) -> tuple[Command, list[str]] | int | None:
    if group == "network":
        scenario_index = prompt_choice(
            "Choose a network scenario:",
            [
                ("test", "Test network (SEAME Wi-Fi)"),
                ("production", "Production network (drone access point)"),
            ],
        )
        if scenario_index is None:
            return None
        if scenario_index == -1:
            return -1
        scenario = ("test", "production")[scenario_index]
        script = "setup_test.py" if scenario == "test" else "setup_production.py"
        actions = (
            [("setup", "Configure the scenario"), ("status", "Show scenario status")]
            if scenario == "test"
            else [
                ("setup", "Configure the scenario"),
                ("connect", "Connect to the drone network"),
                ("disconnect", "Disconnect from the drone network"),
                ("status", "Show scenario status"),
            ]
        )
        action_index = prompt_choice(
            f"Choose a {scenario} network action:", actions
        )
        if action_index is None:
            return None
        if action_index == -1:
            return -1
        action = actions[action_index][0]
        return Command(f"network {scenario}", "", script), [action]

    if group == "fc":
        options = [
            ("dump", "Save read-only CLI diagnostics"),
            ("enable-esp-uart", "Configure UART4 for MSP (writes FC configuration)"),
            ("backup-firmware", "Back up FC firmware"),
            ("run", "Run a custom Betaflight CLI command"),
        ]
        index = prompt_choice("Choose a flight-controller action:", options)
        if index is None:
            return None
        if index == -1:
            return -1
        action = options[index][0]
        if action == "enable-esp-uart":
            return Command("fc", "", "fc_cli.py"), ["--write", action]
        if action == "run":
            return Command("fc", "", "fc_cli.py"), prompt_arguments(
                "Enter Betaflight command(s), quoted separately if needed:"
            )
        return Command("fc", "", "fc_cli.py"), [action]

    if group == "msp":
        options = [
            ("info", "Read FC information through the ESP bridge"),
            ("ping", "Measure bridge latency and packet loss"),
            ("cycle1a", "Run the bridge test with a fake FC"),
            ("fake-fc", "Start a fake FC responder on the ESP USB port"),
            ("usb-relay", "Relay bytes between ESP USB and FC USB"),
            ("attitude", "Display attitude continuously"),
            ("--usb attitude", "Display attitude continuously over FC USB"),
            ("--usb info", "Read FC information over USB"),
            ("--usb core", "Read core FC values over USB"),
            ("--usb ping", "Measure USB latency and packet loss"),
        ]
        index = prompt_choice("Choose an MSP check:", options)
        if index is None:
            return None
        if index == -1:
            return -1
        action = options[index][0]
        args = shlex.split(action)
        if action.endswith("ping"):
            extra = prompt_arguments(
                "Optional MSP arguments (press Enter for defaults):\n"
                "  -n N              number of requests (ping default: 100)\n"
                "  --rate HZ          requests per second (default: 50; 0 = maximum)\n"
                "  --timeout SECONDS  response timeout (default: 0.5)\n"
                "  --csv FILE         save successful RTTs to a CSV file\n"
                "  --trace            print raw MSP frames\n"
                "Example: -n 30000 --rate 50 --timeout 1 --csv bridge_10min.csv\n"
                "Arguments: "
            )
            args.extend(extra)
        elif action == "--usb attitude":
            extra = prompt_arguments(
                "Optional USB attitude arguments (press Enter for defaults):\n"
                "  --fc-port PORT  FC USB serial port (default: auto-detect)\n"
                "  --rate HZ        updates per second (default: 10)\n"
                "  --trace          print raw MSP frames\n"
                "Example: --rate 5 --trace\n"
                "Arguments: "
            )
            args.extend(extra)
        elif action == "fake-fc":
            extra = prompt_arguments(
                "Optional fake-FC arguments (press Enter for defaults):\n"
                "  --esp-port-usb PORT  ESP USB serial port (default: auto-detect)\n"
                "  --trace               print received and sent MSP frames\n"
                "Example: --trace\n"
                "Arguments: "
            )
            args.extend(extra)
        elif action == "usb-relay":
            extra = prompt_arguments(
                "Optional USB relay arguments (press Enter for auto-detect):\n"
                "  --esp-port-usb PORT  ESP USB serial port\n"
                "  --fc-port PORT       FC USB serial port\n"
                "Example: --esp-port-usb /dev/ttyUSB0 --fc-port /dev/ttyACM0\n"
                "Arguments: "
            )
            args.extend(extra)
        return Command("msp", "", "msp_check.py"), args

    options = [
        ("query", "Show ESP AT and network state"),
        ("backup", "Back up ESP flash"),
        ("exit-bridge", "Leave transparent bridge mode"),
        ("restore", "Factory-reset ESP AT settings"),
        ("raw", "Send a custom AT command"),
    ]
    index = prompt_choice("Choose an ESP action:", options)
    if index is None:
        return None
    if index == -1:
        return -1
    action = options[index][0]
    if action == "raw":
        return Command("esp", "", "esp_at.py"), ["raw", *prompt_arguments(
            "Enter AT command(s), quoted separately:"
        )]
    return Command("esp", "", "esp_at.py"), [action]


def prompt_arguments(prompt: str) -> list[str]:
    while True:
        try:
            line = input(f"{prompt} ").strip()
        except (EOFError, KeyboardInterrupt):
            return []
        if not line:
            return []
        try:
            return shlex.split(line)
        except ValueError as exc:
            print(f"Invalid command line: {exc}")


def command_help() -> str:
    lines = ["Available command groups:", ""]
    width = max(len(command.name) for command in COMMANDS)
    for command in COMMANDS:
        lines.append(f"  {command.name:<{width}}  {command.description}")
    lines.extend(
        [
            "",
            "Common examples:",
            "  seame.py network test setup",
            "  seame.py network production connect",
            "  seame.py fc dump",
            "  seame.py msp --usb info",
            "  seame.py esp query",
            "",
            "Use '<group> --help' for the specialist script's complete options.",
        ]
    )
    return "\n".join(lines)


def resolve_command(argv: list[str]) -> tuple[Command, list[str]] | None:
    for command in sorted(COMMANDS, key=lambda item: len(item.name), reverse=True):
        words = command.name.split()
        if argv[: len(words)] == words:
            return command, argv[len(words) :]
    return None


def run_command(command: Command, args: list[str]) -> int:
    if command.script is None:
        return 0
    if command.script == "msp_check.py":
        args = normalize_msp_arguments(args)
    script = SCRIPTS_DIR / command.script
    completed = subprocess.run([sys.executable, str(script), *command.prefix, *args])
    return completed.returncode


def normalize_msp_arguments(args: list[str]) -> list[str]:
    """Allow global MSP options after the subcommand in the unified interface."""
    normalized = list(args)
    for option in ("--timeout", "--fc-port", "--esp", "--esp-port-usb"):
        try:
            index = normalized.index(option)
        except ValueError:
            continue
        if index > 0:
            value_index = index + 1
            if value_index >= len(normalized):
                return normalized
            normalized[0:0] = normalized[index:value_index + 1]
            del normalized[index + 2:index + 4]
    return normalized


def wait_for_enter() -> None:
    try:
        input("\nPress Enter to return to the menu...")
    except (EOFError, KeyboardInterrupt):
        print()


def interactive() -> int:
    print("SEA:ME drone command interface")
    while True:
        group_index = prompt_choice("Choose a command group:", list(GROUPS))
        if group_index is None:
            return 0
        if group_index == -1:
            return 0
        group = GROUPS[group_index][0]
        selected = menu_command(group)
        if selected is None:
            continue
        if selected == -1:
            return 0
        command, args = selected
        result = run_command(command, args)
        if result:
            print(f"Command exited with status {result}.")
        else:
            print("Command completed.")
        wait_for_enter()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return interactive()
    if argv[0] in {"list", "help", "--help", "-h"}:
        print(command_help())
        return 0
    resolved = resolve_command(argv)
    if resolved is None:
        print(f"Unknown command: {' '.join(argv)}\n\n{command_help()}", file=sys.stderr)
        return 2
    command, args = resolved
    return run_command(command, args)


if __name__ == "__main__":
    raise SystemExit(main())
