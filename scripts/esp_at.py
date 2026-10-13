#!/usr/bin/env python3
"""ESP8266 (AT firmware 1.7.x) helper: query, back up, set up and undo the transparent bridge.

The ESP is connected to the PC by its own USB port. Never connect the ESP USB and the FC
to the ESP's TX/RX at the same time.

Examples:
  python3 scripts/esp_at.py query                 # read-only state (mode, saved AP, SoftAP, bridge)
  python3 scripts/esp_at.py backup                # full flash → ~/Projects/SEAME/backups/esp8266/ (never in Git)
  python3 scripts/esp_at.py setup-bridge --ssid drone-team1 --channel 6
  python3 scripts/esp_at.py exit-bridge           # leave passthrough and stop it at boot
  python3 scripts/esp_at.py raw "AT+CWLAP"
  python3 scripts/esp_at.py restore               # AT+RESTORE: factory settings, erases saved Wi-Fi credentials

Passwords are read with getpass (or from the ESP_AP_PASSWORD variable) and are never printed or written.
"""

import argparse
import datetime
import getpass
import hashlib
import os
import pathlib
import re
import subprocess
import sys
import time

import serial
from serial.tools import list_ports

BACKUP_DIR = pathlib.Path.home() / "Projects" / "SEAME" / "backups" / "esp8266"
USB_SERIAL_IDS = {(0x0403, 0x6001), (0x1A86, 0x7523), (0x10C4, 0xEA60), (0x1A86, 0x55D4)}
QUERIES = ["AT+GMR", "AT+CWMODE_DEF?", "AT+CWJAP_DEF?", "AT+CWSAP_DEF?", "AT+CIPAP_DEF?",
           "AT+CWAUTOCONN?", "AT+CIPMODE?", "AT+UART_DEF?", "AT+SYSRAM?"]


def find_port() -> str:
    for p in list_ports.comports():
        if (p.vid, p.pid) in USB_SERIAL_IDS:
            return p.device
    sys.exit("ESP USB-serial not found (FT232R / CH340 / CP210x). Is it plugged in?")


def open_port(port: str, baud: int) -> serial.Serial:
    ser = serial.Serial()
    ser.port, ser.baudrate, ser.timeout = port, baud, 0.2
    ser.dtr = ser.rts = False  # keep EN/GPIO0 released: do not reset the ESP
    ser.open()
    time.sleep(0.3)
    ser.reset_input_buffer()
    return ser


def at(ser: serial.Serial, cmd: str, wait: float = 1.0, secret: str = "") -> str:
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode())
    buf, t0 = b"", time.time()
    while time.time() - t0 < wait:
        buf += ser.read(1024)
        if re.search(rb"\r\n(OK|ERROR|FAIL)\r\n", buf):
            break
    out = buf.decode(errors="replace").replace("\r", "").strip()
    if secret:
        out = out.replace(secret, "********")
    return out


def check_alive(ser: serial.Serial):
    if "OK" not in at(ser, "AT"):
        sys.exit("The ESP does not answer AT. It is probably in transparent (passthrough) mode:\n"
                 "  python3 scripts/esp_at.py exit-bridge")


def cmd_query(args):
    ser = open_port(args.port, args.baud)
    try:
        check_alive(ser)
        for q in QUERIES:
            print(at(ser, q))
            print("---")
    finally:
        ser.close()


def cmd_raw(args):
    ser = open_port(args.port, args.baud)
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
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"\nSaved {out}\nSHA-256 {digest}")
    print("This image can contain saved Wi-Fi passwords. Never commit or share it.")


def cmd_setup_bridge(args):
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,32}", args.ssid):
        sys.exit("--ssid: use 1-32 letters, digits, '_' or '-'")
    password = os.environ.get("ESP_AP_PASSWORD") or getpass.getpass("SoftAP WPA2 password (8-63 chars): ")
    if not 8 <= len(password) <= 63 or any(c in password for c in '",\\'):
        sys.exit("Password must be 8-63 characters, without quotes, commas or backslashes.")
    cmds = [
        "AT+CWMODE_DEF=2",
        f'AT+CWSAP_DEF="{args.ssid}","{password}",{args.channel},3',
        f'AT+SAVETRANSLINK=1,"{args.pc_ip}",{args.pc_port},"UDP",{args.esp_port}',
    ]
    ser = open_port(args.port, args.baud)
    try:
        check_alive(ser)
        for c in cmds:
            out = at(ser, c, 3, secret=password)
            print(out)
            if "OK" not in out:
                sys.exit("Stopped: the command above failed.")
        print(at(ser, "AT+CWSAP_DEF?", secret=password))
        print("Rebooting the ESP. From now on it starts in transparent mode.")
        at(ser, "AT+RST", 2)
    finally:
        ser.close()
    print(f"\nNext: join Wi-Fi '{args.ssid}', set this PC to {args.pc_ip}/24 (static), then\n"
          f"  python3 scripts/bridge_test.py --udp 192.168.4.1:{args.esp_port} --local-port {args.pc_port} info")


def cmd_exit_bridge(args):
    ser = open_port(args.port, args.baud)
    try:
        time.sleep(1.2)          # the ESP needs silence before and after '+++'
        ser.write(b"+++")
        time.sleep(1.5)
        ser.reset_input_buffer()
        for c in ["AT", "AT+SAVETRANSLINK=0", "AT+CIPMODE=0"]:
            print(at(ser, c, 2))
    finally:
        ser.close()
    print("Transparent mode is off and will not start at boot.")


def cmd_restore(args):
    if input("AT+RESTORE erases saved Wi-Fi settings and resets all AT settings. Type 'yes': ") != "yes":
        sys.exit("Cancelled.")
    ser = open_port(args.port, args.baud)
    try:
        print(at(ser, "AT+RESTORE", 3))
    finally:
        ser.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="ESP serial port (default: auto-detect)")
    ap.add_argument("--baud", type=int, default=115200)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("query", help="read-only AT state")
    p = sub.add_parser("raw", help="send AT commands as written")
    p.add_argument("commands", nargs="+")
    p.add_argument("--wait", type=float, default=3)
    sub.add_parser("backup", help=f"full flash backup into {BACKUP_DIR}")
    p = sub.add_parser("setup-bridge", help="SoftAP (WPA2) + UDP transparent mode at boot")
    p.add_argument("--ssid", required=True)
    p.add_argument("--channel", type=int, default=6, choices=range(1, 14), metavar="1-13")
    p.add_argument("--pc-ip", default="192.168.4.2")
    p.add_argument("--pc-port", type=int, default=5760)
    p.add_argument("--esp-port", type=int, default=5760)
    sub.add_parser("exit-bridge", help="leave transparent mode and disable it at boot")
    sub.add_parser("restore", help="AT+RESTORE (asks for confirmation)")
    args = ap.parse_args()
    args.port = args.port or find_port()
    {"query": cmd_query, "raw": cmd_raw, "backup": cmd_backup, "setup-bridge": cmd_setup_bridge,
     "exit-bridge": cmd_exit_bridge, "restore": cmd_restore}[args.cmd](args)


if __name__ == "__main__":
    main()
