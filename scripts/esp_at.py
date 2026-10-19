#!/usr/bin/env python3
"""ESP8266 (AT firmware 1.7.x) low-level helper. The scenario scripts (setup_test.py,
setup_production.py) use the functions below; this CLI is for manual work.

The ESP must be on its own USB port. Never connect the ESP USB and the FC to the ESP's
TX/RX at the same time.

Examples:
  python3 scripts/esp_at.py query          # read-only state
  python3 scripts/esp_at.py backup         # full flash → ~/Projects/SEAME/backups/esp8266/ (never in Git)
  python3 scripts/esp_at.py write-flash blink.bin   # S1-02: flash another image (asks first)
  python3 scripts/esp_at.py write-flash      # S1-02: write the newest backup back
  python3 scripts/esp_at.py boot-log         # S1-03: boot bytes at 74880 and as the FC sees them
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

import msp
import serial
from serial.tools import list_ports

BACKUP_DIR = pathlib.Path.home() / "Projects" / "SEAME" / "backups" / "esp8266"
RESULTS_DIR = pathlib.Path(__file__).resolve().parents[1] / "results"
ROM_BAUD = 74880  # ESP8266 boot ROM messages
FC_BAUD = 115200  # what the FC UART reads
SLEEP_MODES = {"none": 0, "light": 1, "modem": 2}  # AT+SLEEP
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


def hard_reset(ser):
    """Pulse EN through RTS (D1 mini auto-reset circuit), like esptool's hard reset.
    Works in transparent mode too, where AT+RST is not available."""
    ser.dtr = False
    ser.rts = True
    time.sleep(0.1)
    ser.rts = False


def set_sleep_in_bridge(ser, mode: str) -> str:
    """Change AT+SLEEP while the bridge runs and return the previous setting.

    '+++' leaves transparent mode without closing the UDP link, AT+CIPSEND goes back.
    AT+SLEEP is not saved: the next reboot (or opening the USB port) restores the default.
    """
    time.sleep(1.2)
    ser.write(b"+++")
    time.sleep(1.5)
    ser.reset_input_buffer()
    if "OK" not in at(ser, "AT"):
        raise EspError(
            "The ESP did not leave transparent mode to change the sleep mode."
        )
    before = at(ser, "AT+SLEEP?", 2)
    at_ok(ser, f"AT+SLEEP={SLEEP_MODES[mode]}")
    out = at(ser, "AT+CIPSEND", 2)
    if "OK" not in out and ">" not in out:
        raise EspError(f"AT+CIPSEND failed, the bridge is not running again:\n{out}")
    time.sleep(0.2)
    ser.reset_input_buffer()
    m = re.search(r"\+SLEEP:(\d)", before)
    return m.group(1) if m else "unknown"


def esptool(port: str, *args, capture: bool = False):
    """Run esptool. Command names use underscores (flash_id): esptool 4.x accepts only
    those, esptool 5 accepts both."""
    cmd = [sys.executable, "-m", "esptool", "--port", port, "--baud", "460800", *args]
    r = subprocess.run(cmd, capture_output=capture, text=True)
    if capture:
        print(r.stdout, end="")
        print(r.stderr, end="", file=sys.stderr)
    if r.returncode != 0:
        raise EspError(
            f"esptool {args[0]} failed (exit {r.returncode}). Is the ESP on {port}, "
            "and is nothing else (fake-fc, usb-relay, a serial monitor) using the port?"
        )
    return r


def flash_size_bytes(flash_id_output: str):
    m = re.search(r"Detected flash size:\s*(\d+)\s*(MB|KB)", flash_id_output)
    if not m:
        return None
    return int(m.group(1)) * (1024 * 1024 if m.group(2) == "MB" else 1024)


def latest_backup():
    images = sorted(BACKUP_DIR.glob("esp8266_flash_*.bin"))
    return images[-1] if images else None


def capture_boot(port: str, baud: int, seconds: float) -> bytes:
    """Reset the ESP and record everything it sends on its UART for `seconds`."""
    ser = open_port(port, baud)
    try:
        hard_reset(ser)
        buf, t0 = b"", time.time()
        while time.time() - t0 < seconds:
            buf += ser.read(4096)
        return buf
    finally:
        ser.close()


def boot_bytes_report(data: bytes) -> dict:
    """What an MSP parser on the FC would make of the ESP boot bytes (F5/F7 in ESP_to_FC)."""
    parser = msp.Parser()
    frames = parser.feed(data)
    return {
        "bytes": len(data),
        "msp_headers": data.count(b"$M"),
        "msp_frames": len(frames),
        "bad_checksums": parser.bad_checksums,
    }


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
    size = flash_size_bytes(esptool(args.port, "flash_id", capture=True).stdout)
    esptool(args.port, "read_flash", "0", "ALL", str(out))
    out.chmod(0o600)
    esptool(args.port, "verify_flash", "0", str(out))
    print(f"\nSaved {out}\nSHA-256 {hashlib.sha256(out.read_bytes()).hexdigest()}")
    got = out.stat().st_size
    if size is None:
        print(f"Image size {got} bytes (flash-id did not report the flash size).")
    elif got == size:
        print(f"S1-01 PASS: image size {got} bytes = flash size")
    else:
        print(f"S1-01 FAIL: image size {got} bytes, flash size {size} bytes")
    print("This image can contain saved Wi-Fi passwords. Never commit or share it.")


def cmd_write_flash(args):
    image = pathlib.Path(args.image) if args.image else latest_backup()
    if image is None:
        sys.exit(f"No backup in {BACKUP_DIR}. Run esp_at.py backup first.")
    if not image.is_file():
        sys.exit(f"{image} does not exist.")
    if (
        input(
            f"This overwrites the ESP flash from address 0 with {image} "
            f"({image.stat().st_size} bytes). Type 'yes': "
        )
        != "yes"
    ):
        sys.exit("Cancelled.")
    size = flash_size_bytes(esptool(args.port, "flash_id", capture=True).stdout)
    if size is not None and image.stat().st_size > size:
        sys.exit(f"The image is larger than the flash ({size} bytes). Nothing written.")
    esptool(args.port, "write_flash", "0", str(image))
    esptool(args.port, "verify_flash", "0", str(image))
    time.sleep(2)
    ser = open_port(args.port)
    try:
        time.sleep(2)  # opening the port can reset the ESP: let it boot
        answers = "OK" in at(ser, "AT")
    finally:
        ser.close()
    print(f"\nWritten and verified: {image}")
    print(
        "AT firmware answers: yes"
        if answers
        else "AT firmware answers: no (expected for a sketch such as blink; "
        "after a restore, the ESP may have booted into the bridge: try esp_at.py query)"
    )


def cmd_boot_log(args):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    for baud in (ROM_BAUD, FC_BAUD):
        data = capture_boot(args.port, baud, args.seconds)
        out = RESULTS_DIR / f"esp_boot_{stamp}_{baud}.bin"
        out.write_bytes(data)
        print(f"\n===== {baud} baud: {len(data)} bytes in {args.seconds:.0f} s → {out}")
        if baud == ROM_BAUD:
            print(data.decode(errors="replace").replace("\r", ""))
        else:
            print(data[:96].hex(" ") + (" ..." if len(data) > 96 else ""))
            r = boot_bytes_report(data)
            print(
                f"As the FC UART reads it: {r['bytes']} bytes, {r['msp_headers']} '$M' headers, "
                f"{r['msp_frames']} valid MSP frames, {r['bad_checksums']} bad checksums"
            )


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
    p = sub.add_parser(
        "write-flash",
        help="write an image at 0x0 and verify it (asks for confirmation)",
    )
    p.add_argument("image", nargs="?", help="default: the newest backup")
    p = sub.add_parser(
        "boot-log", help=f"reset the ESP and record its boot bytes into {RESULTS_DIR}"
    )
    p.add_argument("--seconds", type=float, default=3)
    args = ap.parse_args()
    args.port = args.port or find_port()
    try:
        {
            "query": cmd_query,
            "raw": cmd_raw,
            "backup": cmd_backup,
            "exit-bridge": cmd_exit_bridge,
            "restore": cmd_restore,
            "write-flash": cmd_write_flash,
            "boot-log": cmd_boot_log,
        }[args.cmd](args)
    except EspError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
