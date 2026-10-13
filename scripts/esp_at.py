#!/usr/bin/env python3
"""ESP8266 (AT firmware 1.7.x) low-level helper. The scenario scripts (setup_test.py,
setup_production.py) use the functions below; this CLI is for manual work.

The ESP must be on its own USB port. Never connect the ESP USB and the FC to the ESP's
TX/RX at the same time.

Examples:
  python3 scripts/esp_at.py query          # read-only state
  python3 scripts/esp_at.py backup         # full flash → ~/Projects/SEAME/backups/esp8266/ (never in Git)
  python3 scripts/esp_at.py exit-bridge    # leave transparent mode and stop it at boot
  python3 scripts/esp_at.py raw "AT+CWLAP"
  python3 scripts/esp_at.py restore        # AT+RESTORE (asks for confirmation)
"""

import argparse
import datetime
import hashlib
import pathlib
import re
import subprocess
import sys
import time

import serial
from serial.tools import list_ports

BACKUP_DIR = pathlib.Path.home() / "Projects" / "SEAME" / "backups" / "esp8266"
USB_SERIAL_IDS = {
    (0x0403, 0x6001),
    (0x1A86, 0x7523),
    (0x10C4, 0xEA60),
    (0x1A86, 0x55D4),
}
QUERIES = [
    "AT+GMR",
    "AT+CWMODE_DEF?",
    "AT+CWJAP_DEF?",
    "AT+CWSAP_DEF?",
    "AT+CIPAP_DEF?",
    "AT+CIFSR",
    "AT+CWAUTOCONN?",
    "AT+CIPMODE?",
    "AT+SYSRAM?",
]


class EspError(RuntimeError):
    pass


def find_port() -> str:
    for p in list_ports.comports():
        if (p.vid, p.pid) in USB_SERIAL_IDS:
            return p.device
    sys.exit(
        "ESP USB-serial not found (FT232R / CH340 / CP210x). Is the ESP plugged in by USB?"
    )


def open_port(port: str, baud: int = 115200) -> serial.Serial:
    ser = serial.Serial()
    ser.port, ser.baudrate, ser.timeout = port, baud, 0.2
    ser.dtr = ser.rts = False  # keep EN/GPIO0 released: do not reset the ESP
    ser.open()
    time.sleep(0.3)
    ser.reset_input_buffer()
    return ser


def at(ser: serial.Serial, cmd: str, wait: float = 1.0, secret: str = "") -> str:
    """Send one AT command and return the answer (up to OK/ERROR/FAIL or `wait` seconds)."""
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode())
    buf, t0 = b"", time.time()
    while time.time() - t0 < wait:
        buf += ser.read(1024)
        if re.search(rb"\r\n(OK|ERROR|FAIL)\r\n", buf):
            break
    out = buf.decode(errors="replace").replace("\r", "").strip()
    return out.replace(secret, "********") if secret else out


def at_ok(ser, cmd: str, wait: float = 3, secret: str = "") -> str:
    out = at(ser, cmd, wait, secret)
    shown = cmd.replace(secret, "********") if secret else cmd
    if not re.search(r"(^|\n)OK$", out):
        raise EspError(f"{shown} failed:\n{out}")
    print(f"  {shown} → OK")
    return out


def leave_passthrough(ser):
    """Leave transparent mode ('+++' needs 1 s of silence before and after) and stop it at boot."""
    time.sleep(1.2)
    ser.write(b"+++")
    time.sleep(1.5)
    ser.reset_input_buffer()
    at(ser, "AT+SAVETRANSLINK=0", 2)
    at(ser, "AT+CIPMODE=0", 2)


def ensure_at_mode(ser):
    """Make sure the ESP answers AT commands, leaving transparent mode if needed."""
    if "OK" in at(ser, "AT"):
        return
    leave_passthrough(ser)
    if "OK" not in at(ser, "AT"):
        raise EspError(
            "The ESP does not answer AT commands. Check the USB cable and the port."
        )
    print("  ESP was in transparent mode: left it.")


def station_ip(ser) -> str:
    m = re.search(r'STAIP,"([\d.]+)"', at(ser, "AT+CIFSR", 2))
    return m.group(1) if m and m.group(1) != "0.0.0.0" else ""


def join_wifi(ser, ssid: str, password: str):
    out = at(ser, f'AT+CWJAP_DEF="{ssid}","{password}"', 25, secret=password)
    if "WIFI GOT IP" not in out and not out.endswith("OK"):
        code = re.search(r"\+CWJAP:(\d)", out)
        reason = {
            "1": "timeout",
            "2": "wrong password",
            "3": "network not found",
            "4": "connection failed",
        }.get(code.group(1) if code else "", "unknown")
        raise EspError(f"Could not join '{ssid}' ({reason}).")
    print(f"  joined '{ssid}'")


def reboot(ser):
    at(ser, "AT+RST", 2)


def cmd_query(args):
    ser = open_port(args.port)
    try:
        if "OK" not in at(ser, "AT"):
            sys.exit(
                "No answer to AT: the ESP is probably in transparent mode. Run: esp_at.py exit-bridge"
            )
        for q in QUERIES:
            print(at(ser, q, 2))
            print("---")
    finally:
        ser.close()


def cmd_raw(args):
    ser = open_port(args.port)
    try:
        for c in args.commands:
            print(at(ser, c, args.wait))
    finally:
        ser.close()


def cmd_backup(args):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.parent.chmod(0o700)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out = BACKUP_DIR / f"esp8266_flash_{stamp}.bin"
    base = [sys.executable, "-m", "esptool", "--port", args.port, "--baud", "460800"]
    subprocess.run(base + ["flash-id"], check=True)
    subprocess.run(base + ["read-flash", "0", "ALL", str(out)], check=True)
    out.chmod(0o600)
    subprocess.run(base + ["verify-flash", "0", str(out)], check=True)
    print(f"\nSaved {out}\nSHA-256 {hashlib.sha256(out.read_bytes()).hexdigest()}")
    print("This image can contain saved Wi-Fi passwords. Never commit or share it.")


def cmd_exit_bridge(args):
    ser = open_port(args.port)
    try:
        leave_passthrough(ser)
        print(at(ser, "AT"))
    finally:
        ser.close()
    print("Transparent mode is off and will not start at boot.")


def cmd_restore(args):
    if (
        input(
            "AT+RESTORE erases saved Wi-Fi settings and resets all AT settings. Type 'yes': "
        )
        != "yes"
    ):
        sys.exit("Cancelled.")
    ser = open_port(args.port)
    try:
        ensure_at_mode(ser)
        print(at(ser, "AT+RESTORE", 3))
    finally:
        ser.close()


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--port", help="ESP serial port (default: auto-detect)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("query", help="read-only AT state")
    p = sub.add_parser("raw", help="send AT commands as written")
    p.add_argument("commands", nargs="+")
    p.add_argument("--wait", type=float, default=3)
    sub.add_parser("backup", help=f"full flash backup into {BACKUP_DIR}")
    sub.add_parser("exit-bridge", help="leave transparent mode and disable it at boot")
    sub.add_parser("restore", help="AT+RESTORE (asks for confirmation)")
    args = ap.parse_args()
    args.port = args.port or find_port()
    try:
        {
            "query": cmd_query,
            "raw": cmd_raw,
            "backup": cmd_backup,
            "exit-bridge": cmd_exit_bridge,
            "restore": cmd_restore,
        }[args.cmd](args)
    except EspError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
