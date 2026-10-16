#!/usr/bin/env python3
"""Betaflight CLI helper for the flight controller (USB only, no battery, props off).

Read-only by default. Any command that can change the configuration needs --write,
and a `diff all` backup is saved before it runs.

Examples:
  python3 scripts/fc_cli.py dump                       # version, status, serial, failsafe... + diff all → drone/config/betaflight/<ver>/
  python3 scripts/fc_cli.py run "get failsafe" "serial"
  python3 scripts/fc_cli.py --write run "set vtx_power = 1" --save
  python3 scripts/fc_cli.py --write enable-esp-uart    # UART4 = MSP 115200 (ADR-011)
  python3 scripts/fc_cli.py backup-firmware            # full 512 KB flash over DFU (needs dfu-util)
  python3 scripts/fc_cli.py compare-diffs              # S2-02: did the config change? (last two diff all)

On exit the FC reboots (Betaflight leaves the CLI that way). Nothing is saved unless you ask.
"""

import argparse
import datetime
import difflib
import hashlib
import pathlib
import re
import shutil
import subprocess
import sys
import time

import serial
from serial.tools import list_ports

REPO = pathlib.Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO / "drone" / "config" / "betaflight"  # repo layout from #12
BACKUP_DIR = pathlib.Path.home() / "Projects" / "SEAME" / "backups" / "betaflight"
FC_USB_ID = (0x0483, 0x5740)  # STM32 virtual COM port

READ_ONLY = {"version", "status", "tasks", "diff", "dump", "get", "help"}
LIST_WHEN_BARE = {
    "serial",
    "feature",
    "resource",
    "aux",
    "map",
    "vtxtable",
    "beacon",
    "mixer",
}
DUMP_COMMANDS = [
    "version",
    "status",
    "serial",
    "get failsafe",
    "get msp_override",
    "get vbat",
    "get vtx_power",
    "tasks",
]
ESP_UART_LINE = "serial 3 1 115200 57600 0 115200"  # UART4 (id 3), MSP (1), 115200


def is_read_only(line: str) -> bool:
    words = line.split()
    if not words:
        return True
    if words[0] in READ_ONLY:
        return True
    return words[0] in LIST_WHEN_BARE and len(words) == 1


def find_port() -> str:
    for p in list_ports.comports():
        if (p.vid, p.pid) == FC_USB_ID:
            return p.device
    sys.exit(
        "Flight controller not found (STM32 0483:5740). Is it plugged in, and not in DFU mode?"
    )


class Cli:
    def __init__(self, port: str):
        self.ser = serial.Serial(port, 115200, timeout=0.2)
        self.ser.reset_input_buffer()
        self.ser.write(b"#")
        self._read_until_prompt(5)

    def _read_until_prompt(self, limit: float) -> str:
        buf, t_last = b"", time.time()
        while time.time() - t_last < limit:
            chunk = self.ser.read(4096)
            if chunk:
                buf += chunk
                t_last = time.time()
                if buf.rstrip().endswith(b"#"):
                    time.sleep(0.2)
                    more = self.ser.read(65536)
                    buf += more
                    if not more:
                        break
        return buf.decode(errors="replace").replace("\r", "")

    def run(self, line: str, limit: float = 20) -> str:
        self.ser.write((line + "\r\n").encode())
        out = self._read_until_prompt(limit).split("\n")
        out = out[1:] if out and out[0].strip() == line.strip() else out
        while out and out[-1].strip() in ("", "#"):
            out.pop()
        return "\n".join(out).strip("\n") + "\n"

    def save(self):
        self.ser.write(b"save\r\n")  # the FC reboots and the port disappears
        time.sleep(2)
        self.ser.close()

    def exit(self):
        try:
            self.ser.write(b"exit\r\n")
            time.sleep(0.5)
        except serial.SerialException:
            pass
        self.ser.close()


def firmware_version(cli: Cli) -> str:
    m = re.search(r"Betaflight / \S+ \(\S+\) (\S+)", cli.run("version"))
    return m.group(1) if m else "unknown"


def save_diff(cli: Cli, label: str = "") -> pathlib.Path:
    ver = firmware_version(cli)
    out_dir = CONFIG_DIR / ver
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    path = out_dir / f"diff_all_{stamp}{label}.txt"
    path.write_text(cli.run("diff all", 30))
    print(f"diff all saved to {path.relative_to(REPO)}")
    return path


def cmd_dump(args):
    cli = Cli(args.port)
    try:
        ver = firmware_version(cli)
        out_dir = CONFIG_DIR / ver
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        parts = ["# Read-only Betaflight CLI output. Nothing was saved to the FC.\n"]
        for c in DUMP_COMMANDS:
            parts.append(f"# ===== {c}\n{cli.run(c)}")
        read_path = out_dir / f"cli_read_{stamp}.txt"
        read_path.write_text("\n".join(parts))
        print(f"CLI output saved to {read_path.relative_to(REPO)}")
        save_diff(cli)
        status = cli.run("status")
        for line in status.splitlines():
            if line.startswith(("GYRO=", "Arming disable", "Voltage")):
                print("  " + line)
    finally:
        cli.exit()


def cmd_run(args):
    writes = [c for c in args.commands if not is_read_only(c)]
    if writes and not args.write:
        sys.exit(
            f"These commands can change the FC: {writes}\nAdd --write if you mean it."
        )
    if args.save and not args.write:
        sys.exit("--save needs --write.")
    cli = Cli(args.port)
    saved = False
    try:
        if writes:
            save_diff(cli, "_before")
        for c in args.commands:
            print(f"# {c}\n{cli.run(c)}")
        if args.save:
            print("Saving. The FC reboots.")
            cli.save()
            saved = True
    finally:
        if not saved:
            cli.exit()


def cmd_enable_esp_uart(args):
    if not args.write:
        sys.exit(f"This sets `{ESP_UART_LINE}` and saves. Add --write if you mean it.")
    cli = Cli(args.port)
    current = cli.run("serial")
    if ESP_UART_LINE in current:
        print("UART4 is already MSP at 115200. Nothing to do.")
        cli.exit()
        return
    save_diff(cli, "_before")
    print(cli.run(ESP_UART_LINE))
    print(cli.run("serial"))
    print("Saving. The FC reboots.")
    cli.save()


def sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cmd_backup_firmware(args):
    if not shutil.which("dfu-util"):
        sys.exit("dfu-util is not installed: sudo apt install dfu-util")
    cli = Cli(args.port)
    ver = firmware_version(cli)
    cli.ser.write(b"bl\r\n")  # reboot into the STM32 DFU bootloader; changes no setting
    time.sleep(1)
    try:
        cli.ser.close()
    except serial.SerialException:
        pass
    time.sleep(3)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.date.today().isoformat()
    out = BACKUP_DIR / f"betaflight_{ver}_flash_{stamp}.bin"
    check = BACKUP_DIR / f".check_{stamp}.bin"
    size = 524288  # STM32F722: 512 KB internal flash
    for target, leave in ((out, ""), (check, ":leave")):
        if target.exists():
            target.unlink()
        r = subprocess.run(
            [
                "dfu-util",
                "-a",
                "0",
                "-s",
                f"0x08000000:{size}{leave}",
                "-U",
                str(target),
            ],
            capture_output=True,
            text=True,
        )
        if not target.exists() or target.stat().st_size != size:
            sys.exit(
                f"DFU read failed:\n{r.stdout[-500:]}\n{r.stderr[-500:]}\n"
                "If the FC stays in DFU, unplug and replug the USB cable."
            )
    same = out.read_bytes() == check.read_bytes()
    check.unlink()
    print(f"Firmware saved to {out}")
    print(f"SHA-256 {sha256(out)}")
    print(
        "Two reads identical"
        if same
        else "WARNING: the two reads differ. Repeat the backup."
    )
    print("The FC is back in normal mode (if not, replug the USB cable).")


def diff_files():
    return sorted(CONFIG_DIR.glob("*/diff_all_*.txt"), key=lambda p: p.name)


def config_lines(path: pathlib.Path):
    """`diff all` lines without comments (version banner, dates) and blank lines."""
    return [
        line
        for line in path.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]


def cmd_compare_diffs(args):
    files = [pathlib.Path(f) for f in args.files] or diff_files()[-2:]
    if len(files) != 2:
        sys.exit(
            "Need two `diff all` files: run `fc_cli.py dump` before and after the test."
        )
    old, new = files
    changes = list(
        difflib.unified_diff(
            config_lines(old), config_lines(new), str(old), str(new), lineterm=""
        )
    )
    print(
        "\n".join(changes) if changes else f"No configuration change:\n  {old}\n  {new}"
    )


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--port", help="FC serial port (default: auto-detect 0483:5740)")
    ap.add_argument(
        "--write", action="store_true", help="allow commands that change the FC"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser(
        "dump",
        help="save read-only CLI output and diff all to drone/config/betaflight/<ver>/",
    )
    p = sub.add_parser("run", help="run CLI commands")
    p.add_argument("commands", nargs="+")
    p.add_argument(
        "--save", action="store_true", help="save after the commands (needs --write)"
    )
    sub.add_parser(
        "enable-esp-uart", help=f"set `{ESP_UART_LINE}` and save (needs --write)"
    )
    sub.add_parser(
        "backup-firmware", help=f"read the full flash over DFU into {BACKUP_DIR}"
    )
    p = sub.add_parser(
        "compare-diffs",
        help="compare two `diff all` files (default: the two newest); no FC needed",
    )
    p.add_argument("files", nargs="*", metavar="FILE")
    args = ap.parse_args()
    if args.cmd == "compare-diffs":
        return cmd_compare_diffs(args)
    args.port = args.port or find_port()
    {
        "dump": cmd_dump,
        "run": cmd_run,
        "enable-esp-uart": cmd_enable_esp_uart,
        "backup-firmware": cmd_backup_firmware,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
