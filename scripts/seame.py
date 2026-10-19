#!/usr/bin/env python3
"""One entry point for the SEA:ME drone bench scripts. Props off, no battery.

The ESP is soldered to the FC (UART4). This PC reaches the FC in two ways:
  - through the ESP, over the drone Wi-Fi (the default for `msp`)
  - straight over the FC USB cable (`msp --usb ...` and every `fc` command)

Run without arguments for the menu, or give a command:

    python3 scripts/seame.py network connect
    python3 scripts/seame.py msp info
    python3 scripts/seame.py msp attitude --usb
    python3 scripts/seame.py fc dump
    python3 scripts/seame.py test s2-01

Arguments after a command go to the specialist script (`<command> --help` lists
them), so no hardware logic is duplicated here.

Commands that need the ESP on its own USB port (`esp ...`, `network setup`,
`msp cycle1a`, ...) are refused unless `--esp-on-usb` comes first. With the ESP
soldered, its USB chip and the FC would both drive the ESP RX line (ADR-012).
"""

from __future__ import annotations

import shlex
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
QUIT = -1
NM_NAME = "seame-drone"  # NetworkManager profile made by setup_production.py

# Longest prefix first. `network` alone means the drone access point (production).
ROUTES = (
    (("network", "production"), "setup_production.py"),
    (("network", "test"), "setup_test.py"),
    (("network",), "setup_production.py"),
    (("fc",), "fc_cli.py"),
    (("msp",), "msp_check.py"),
    (("esp",), "esp_at.py"),
)

GROUP_HELP = (
    ("network", "drone Wi-Fi: connect, disconnect, status"),
    ("msp", "read the FC through the ESP (default) or over USB (--usb)"),
    ("fc", "Betaflight CLI over the FC USB: dump, run, compare-diffs, reboot"),
    ("test", "guided bench tests on the soldered link (see `test list`)"),
    ("esp", "ESP AT firmware over its own USB (needs --esp-on-usb)"),
)

# msp_check.py options that belong before its subcommand.
MSP_GLOBAL_FLAGS = ("--usb",)
MSP_GLOBAL_VALUES = ("--timeout", "--fc-port", "--esp", "--esp-port-usb")

ESP_USB_SUBCOMMANDS = {
    "setup_test.py": {"setup"},
    "setup_production.py": {"setup", "reboot-test"},
    "msp_check.py": {"cycle1a", "fake-fc", "usb-relay"},
}
ESP_USB_WARNING = """\
Refused: this command needs the ESP on its own USB port.

The ESP is soldered to the FC. With its USB plugged in, the USB chip and the FC
T4 pin both drive the ESP RX line (ADR-012, Safety Rules). `ping --loopback`
needs a TX-RX jumper, which the soldered wires make impossible.

Only follow a written, reviewed procedure for this (e.g. flashing the custom
firmware). If that is what you are doing, put --esp-on-usb first:
    python3 scripts/seame.py --esp-on-usb {command}"""


# ---------------------------------------------------------------- dispatching


def resolve_command(argv: list[str]) -> tuple[str, list[str]] | None:
    """Return (script, arguments for it), or None for an unknown command."""
    for words, script in ROUTES:
        if tuple(argv[: len(words)]) == words:
            args = argv[len(words) :]
            if script == "msp_check.py":
                args = normalize_msp_arguments(args)
            return script, args
    return None


def normalize_msp_arguments(args: list[str]) -> list[str]:
    """Move msp_check.py's global options before its subcommand, keeping their order.

    Lets `msp attitude --usb` and `msp ping --timeout 1` work like `msp --usb attitude`.
    """
    front, rest = [], []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in MSP_GLOBAL_FLAGS or (
            "=" in arg and arg.split("=", 1)[0] in MSP_GLOBAL_VALUES
        ):
            front.append(arg)
        elif arg in MSP_GLOBAL_VALUES and i + 1 < len(args):
            front += args[i : i + 2]
            i += 1
        else:
            rest.append(arg)
        i += 1
    return front + rest


def subcommand(script: str, args: list[str]) -> str | None:
    """First positional argument, skipping the global options of `script`."""
    takes_value = set(MSP_GLOBAL_VALUES) | {"--port"}
    skip = False
    for arg in args:
        if skip:
            skip = False
        elif arg in takes_value:
            skip = True
        elif not arg.startswith("-"):
            return arg
    return None


def needs_esp_usb(script: str, args: list[str]) -> bool:
    if "-h" in args or "--help" in args:
        return False
    if script == "esp_at.py":
        return True
    if script == "msp_check.py" and "--loopback" in args:
        return True
    return subcommand(script, args) in ESP_USB_SUBCOMMANDS.get(script, set())


def run_script(script: str, args: list[str]) -> int:
    """Run a specialist script. Ctrl+C stops the script, not this menu."""
    previous = signal.signal(signal.SIGINT, lambda *_: None)  # reset to default on exec
    try:
        completed = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / script), *args], check=False
        )
    finally:
        signal.signal(signal.SIGINT, previous)
    return completed.returncode


def run(argv: list[str], esp_on_usb: bool = False) -> int:
    resolved = resolve_command(argv)
    if resolved is None:
        print(f"Unknown command: {' '.join(argv)}\n\n{command_help()}", file=sys.stderr)
        return 2
    script, args = resolved
    if needs_esp_usb(script, args) and not esp_on_usb:
        print(ESP_USB_WARNING.format(command=" ".join(argv)), file=sys.stderr)
        return 2
    return run_script(script, args)


# ---------------------------------------------------------------- prompts


def clear_screen() -> None:
    if sys.stdout.isatty():
        print("\033[2J\033[H", end="")


def prompt_choice(title: str, options: list[str]) -> int | None:
    """Show numbered options. Return the zero-based index, None (back) or QUIT."""
    clear_screen()
    while True:
        print(f"\n{title}")
        for number, label in enumerate(options, 1):
            print(f"  {number}) {label}")
        print("  b) Back    q) Quit")
        try:
            choice = input("Select an option: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if choice in {"q", "quit", "exit"}:
            return QUIT
        if choice in {"b", "back"}:
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(options):
            return int(choice) - 1
        print("Please enter one of the displayed numbers.")


def ask(prompt: str, default: str = "") -> str:
    shown = f" [{default}]" if default else " [none]"
    try:
        answer = input(f"{prompt}{shown}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return answer or default


def pause(message: str) -> bool:
    """Wait for Enter. False when the user cancels with Ctrl+C / Ctrl+D."""
    try:
        input(f"{message}\nPress Enter to continue (Ctrl+C to cancel)... ")
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return True


def confirm(message: str) -> bool:
    try:
        return input(f"{message}\nType 'yes' to continue: ").strip().lower() == "yes"
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def wait_for_enter() -> None:
    try:
        input("\nPress Enter to return to the menu...")
    except (EOFError, KeyboardInterrupt):
        print()


def csv_args(name: str) -> list[str]:
    return ["--csv", name] if name else []


def duration_args(seconds: str) -> list[str]:
    return ["--duration", seconds] if seconds else []


def on_drone_network() -> bool:
    try:
        out = subprocess.run(
            ["nmcli", "-t", "-f", "NAME", "connection", "show", "--active"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
    except FileNotFoundError:
        return True  # no NetworkManager: nothing to check
    return NM_NAME in out.splitlines()


def check_drone_network() -> bool:
    if on_drone_network():
        return True
    print("This PC is not on the drone Wi-Fi. Run `network connect` first.")
    return confirm("Continue anyway (e.g. another network setup)?")


# ---------------------------------------------------------------- bench tests
# Each runs the steps of the vault's ESP Bench Test Plan that still apply with the
# ESP soldered to the FC. Results go to results/ (git-ignored): copy them to #30.


def run_steps(*steps: list[str]) -> int:
    for argv in steps:
        print(f"\n$ seame.py {shlex.join(argv)}")
        result = run(argv)
        if result:
            return result
    return 0


def test_s2_01() -> int:
    print("S2-01: expect API 1.46 and 100/100 answers.")
    if not check_drone_network():
        return 1
    return run_steps(
        ["msp", "info"], ["msp", "ping", "-n", "100", "--csv", "s2-01.csv"]
    )


def test_latency() -> int:
    print(
        "Latency and loss at a true 50 Hz through the real FC.\n"
        "Replaces S1-05 on the production AP and the S1-09 runs, which needed the ESP\n"
        "USB or a TX-RX jumper. Note where the PC and the drone are."
    )
    if not check_drone_network():
        return 1
    seconds = ask("Duration in seconds", "600")
    name = ask("CSV file in results/", "s1-05_soldered.csv")
    return run_steps(
        ["msp", "ping", "--stream", *duration_args(seconds), *csv_args(name)]
    )


def test_s2_02() -> int:
    print(
        "S2-02: the FC must ignore ESP resets.\n"
        "Needs the FC on USB and the ESP powered. Each `fc dump` ends with `exit`,\n"
        "which reboots the FC: that is expected and is not part of the test."
    )
    if run_steps(["fc", "dump"]):
        return 1
    if not pause(
        "\nWait about 5 s for the FC to come back. During the status watch, press the\n"
        "ESP reset button 20 times, about 5 s apart, then stop the watch with Ctrl+C."
    ):
        return 1
    run_steps(["msp", "--usb", "status-watch", "--csv", "s2-02.csv"])
    time.sleep(1)
    return run_steps(["fc", "dump"], ["fc", "compare-diffs"])


def test_s2_03() -> int:
    print("S2-03: tilt the drone by hand; the angles must follow. Ctrl+C to stop.")
    if not check_drone_network():
        return 1
    return run_steps(["msp", "attitude"])


def test_s2_04_read() -> int:
    print("S2-04 (read): move the sticks; the channels must follow. Ctrl+C to stop.")
    if not check_drone_network():
        return 1
    return run_steps(["msp", "rc"])


def test_s2_04_write() -> int:
    if not confirm(
        "S2-04 (write) sends MSP_SET_RAW_RC: roll 1200, pitch 1400, throttle 1000,\n"
        "yaw 1600, AUX1-4 low. Only with props off, no battery, and MSP Override\n"
        "configured and approved (ESP_to_FC §10.5)."
    ):
        return 1
    if not check_drone_network():
        return 1
    return run_steps(["msp", "rc-order", "--write"])


def test_s2_05() -> int:
    print(
        "S2-05: Wi-Fi next to ELRS. Radio on and bound; read the ELRS link quality on\n"
        "the radio while the link streams. Only the current AP channel can be tested:\n"
        "changing it needs `network setup --channel`, which needs the ESP USB."
    )
    if not check_drone_network():
        return 1
    seconds = ask("Duration in seconds", "300")
    name = ask("CSV file in results/", "s2-05_ch6.csv")
    return run_steps(
        ["msp", "ping", "--stream", *duration_args(seconds), *csv_args(name)]
    )


def test_s2_06(first: float = 10, second: float = 25, total: float = 40) -> int:
    print(
        f"S2-06: the FC goes silent and the loss must be reported within 500 ms.\n"
        f"A soldered wire cannot be unplugged, so the FC is rebooted over USB at\n"
        f"{first:.0f} s and {second:.0f} s while the ESP stays powered. Needs the FC on USB."
    )
    if not check_drone_network():
        return 1
    watch = ["link-watch", "--duration", str(total), "--csv", "s2-06.csv"]
    previous = signal.signal(signal.SIGINT, lambda *_: None)
    try:
        proc = subprocess.Popen(
            [sys.executable, str(SCRIPTS_DIR / "msp_check.py"), *watch]
        )
        start = time.monotonic()
        for at in (first, second):
            time.sleep(max(0.0, at - (time.monotonic() - start)))
            if proc.poll() is not None:
                break
            print(f"\n[{at:.0f} s] rebooting the FC")
            run_script("fc_cli.py", ["reboot"])
        return proc.wait()
    finally:
        signal.signal(signal.SIGINT, previous)


def test_s1_08() -> int:
    print(
        "S1-08: ESP current. USB power meter between this PC and the FC.\n"
        "  1. Hold the ESP reset button and note the current (FC alone).\n"
        "  2. Release it, wait for the drone Wi-Fi, then note average and peak during\n"
        "     the 60 s stream below. ESP current = stream current - step 1."
    )
    if not pause("\nDo step 1 now."):
        return 1
    if not check_drone_network():
        return 1
    return run_steps(
        ["msp", "ping", "--stream", "--duration", "60", "--csv", "s1-08_stream.csv"]
    )


TESTS: dict[str, tuple[str, Callable[[], int]]] = {
    "s2-01": ("S2-01 link check: FC info and 100 pings", test_s2_01),
    "latency": ("S1-05/S1-09 latency, 10 min at 50 Hz", test_latency),
    "s2-02": ("S2-02 ESP resets while the FC runs (FC USB)", test_s2_02),
    "s2-03": ("S2-03 attitude while tilting the drone", test_s2_03),
    "s2-04": ("S2-04 RC channels, read-only", test_s2_04_read),
    "s2-04-write": ("S2-04 RC channel order (sends RC values)", test_s2_04_write),
    "s2-05": ("S2-05 Wi-Fi next to ELRS", test_s2_05),
    "s2-06": ("S2-06 FC loss detection (reboots the FC)", test_s2_06),
    "s1-08": ("S1-08 ESP current draw (USB power meter)", test_s1_08),
}


def run_test(args: list[str]) -> int:
    if not args or args[0] in {"list", "-h", "--help"}:
        width = max(len(key) for key in TESTS)
        for key, (label, _) in TESTS.items():
            print(f"  {key:<{width}}  {label}")
        return 0 if args else 2
    if args[0] not in TESTS:
        print(f"Unknown test: {args[0]}. Use `test list`.", file=sys.stderr)
        return 2
    return TESTS[args[0]][1]()


# ---------------------------------------------------------------- menus
# A menu returns None (back), QUIT, or the exit status of what it ran.


def pick(title: str, items: list[tuple[str, Callable[[], int | None]]]) -> int | None:
    index = prompt_choice(title, [label for label, _ in items])
    if index is None or index == QUIT:
        return index
    return items[index][1]()


def menu_network() -> int | None:
    return pick(
        "Drone Wi-Fi (this PC has no internet while connected):",
        [
            ("Connect", lambda: run(["network", "connect"])),
            ("Disconnect", lambda: run(["network", "disconnect"])),
            ("Status", lambda: run(["network", "status"])),
        ],
    )


def menu_read() -> int | None:
    path = prompt_choice(
        "Read the FC through:",
        ["the ESP, over the drone Wi-Fi", "the FC USB cable"],
    )
    if path is None or path == QUIT:
        return path
    via = [] if path == 0 else ["--usb"]

    def ping() -> int:
        seconds = ask("Duration in seconds at 50 Hz", "60")
        name = ask("CSV file in results/")
        return run(
            ["msp", *via, "ping", "--stream", *duration_args(seconds), *csv_args(name)]
        )

    def link_watch() -> int:
        seconds = ask("Duration in seconds (Enter = until Ctrl+C)")
        name = ask("CSV file in results/")
        return run(
            ["msp", *via, "link-watch", *duration_args(seconds), *csv_args(name)]
        )

    return pick(
        "Read the FC (read-only):",
        [
            (
                "Info: firmware, status, attitude, battery",
                lambda: run(["msp", *via, "info"]),
            ),
            (
                "Attitude, continuously (Ctrl+C to stop)",
                lambda: run(["msp", *via, "attitude"]),
            ),
            (
                "RC channels, continuously (Ctrl+C to stop)",
                lambda: run(["msp", *via, "rc"]),
            ),
            ("Latency and loss (ping)", ping),
            ("Link watch: report when the FC stops answering", link_watch),
        ],
    )


def menu_fc_usb() -> int | None:
    def cli_commands() -> int:
        line = ask("Betaflight CLI command(s), each quoted (read-only)")
        try:
            commands = shlex.split(line)
        except ValueError as exc:
            print(f"Invalid command line: {exc}")
            return 2
        return run(["fc", "run", *commands]) if commands else 0

    def status_watch() -> int:
        seconds = ask("Duration in seconds (Enter = until Ctrl+C)")
        name = ask("CSV file in results/")
        return run(
            ["msp", "--usb", "status-watch", *duration_args(seconds), *csv_args(name)]
        )

    return pick(
        "FC over USB:",
        [
            (
                "CLI dump: read-only output and `diff all` (reboots the FC)",
                lambda: run(["fc", "dump"]),
            ),
            (
                "Compare the two newest `diff all` files",
                lambda: run(["fc", "compare-diffs"]),
            ),
            ("Run read-only CLI command(s)", cli_commands),
            ("Status watch: log FC status changes", status_watch),
            ("Reboot the FC", lambda: run(["fc", "reboot"])),
            ("Back up the FC firmware (DFU)", lambda: run(["fc", "backup-firmware"])),
        ],
    )


def menu_tests() -> int | None:
    return pick(
        "Bench tests on the soldered link. Props off, no battery, ESP USB unplugged:",
        [(label, func) for label, func in TESTS.values()],
    )


MAIN_MENU = (
    ("Drone Wi-Fi: connect, disconnect, status", menu_network),
    ("Read the FC: info, attitude, RC, latency", menu_read),
    ("FC over USB: CLI dump, compare, reboot, status watch", menu_fc_usb),
    ("Bench tests (ESP Bench Test Plan, Stage 2)", menu_tests),
)


def interactive() -> int:
    while True:
        index = prompt_choice(
            "SEA:ME drone. The ESP is soldered to the FC: never plug in the ESP USB.",
            [label for label, _ in MAIN_MENU],
        )
        if index is None or index == QUIT:
            return 0
        result = MAIN_MENU[index][1]()
        if result == QUIT:
            return 0
        if result is None:
            continue
        print(
            "Command completed."
            if result == 0
            else f"Command exited with status {result}."
        )
        wait_for_enter()


# ---------------------------------------------------------------- entry point


def command_help() -> str:
    width = max(len(name) for name, _ in GROUP_HELP)
    lines = ["Command groups:", ""]
    lines += [f"  {name:<{width}}  {text}" for name, text in GROUP_HELP]
    lines += [
        "",
        "Examples:",
        "  seame.py network connect",
        "  seame.py msp info",
        "  seame.py msp attitude --usb",
        "  seame.py msp ping --stream --duration 600 --csv run.csv",
        "  seame.py fc dump",
        "  seame.py test list",
        "",
        "Use '<group> --help' for all the options of a group.",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    esp_on_usb = bool(argv) and argv[0] == "--esp-on-usb"
    if esp_on_usb:
        argv = argv[1:]
    if not argv:
        return interactive()
    if argv[0] in {"list", "help", "--help", "-h"}:
        print(command_help())
        return 0
    if argv[0] == "test":
        return run_test(argv[1:])
    return run(argv, esp_on_usb)


if __name__ == "__main__":
    raise SystemExit(main())
